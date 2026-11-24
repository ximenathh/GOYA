"""POST /api/checkout y POST /api/webhooks/stripe."""
import time

from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError

from ..crm.auth import requiere_rol
from ..docs import documentar
from ..extensions import db
from ..observer import bus_ordenes
from ..scm.models import CarritoItem
from ..scm.routes import carrito_de
from ..scm.stock import StockInsuficiente
from ..utils import error
from .dto import CheckoutDTO
from .estados import CANCELADA, PENDIENTE_PAGO, PREPARANDO, validar_transicion
from .factura import emitir_factura
from .models import ItemOrden, Orden
from .pagos import construir_evento, construir_lineas, crear_sesion_stripe

bp = Blueprint("pagos", __name__)

EVENTOS_PAGO = {"checkout.session.completed", "checkout.session.async_payment_succeeded"}
EVENTOS_FALLO = {"checkout.session.expired", "checkout.session.async_payment_failed"}
MINUTOS_PARA_PAGAR = 30   # Stripe exige un mínimo de 30 min


def _mensaje(e):
    return "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())


@bp.post("/checkout")
@requiere_rol("consumidor")
@documentar("POST", "/api/checkout", "Iniciar pago con tarjeta (Stripe Checkout)", ["Pagos"],
            body="CheckoutDTO",
            descripcion="Crea la orden en estado pendiente_pago (reserva el stock) y devuelve la URL de Stripe Checkout. "
                        "La orden pasa a preparando cuando Stripe confirma el pago por webhook.",
            respuestas={201: ("Sesión de Stripe creada", "CheckoutRespuestaDTO"),
                        400: ("Datos inválidos o carrito vacío", "ErrorDTO"),
                        401: ("Sin sesión", "ErrorDTO"),
                        403: ("Solo los compradores", "ErrorDTO"),
                        409: ("Stock insuficiente", "ErrorDTO"),
                        502: ("Stripe no respondió", "ErrorDTO"),
                        503: ("Stripe no está configurado", "ErrorDTO")})
def checkout(u):
    try:
        datos = CheckoutDTO.model_validate(request.get_json(silent=True) or {})
    except ValidationError as e:
        return error(_mensaje(e))
    cfg = current_app.config
    if not cfg["STRIPE_SECRET_KEY"]:
        return error("Stripe no está configurado", 503)
    carrito, filas = carrito_de(u.id)
    if carrito.esta_vacio():
        return error("el carrito está vacío")

    orden = Orden(consumidor_id=u.id, ubicacion_entrega=datos.ubicacion_entrega, estado=PENDIENTE_PAGO)
    try:
        for f in filas:
            f.producto.inventario.reservar(f.cantidad)
            orden.items.append(ItemOrden(producto_id=f.producto_id, cantidad=f.cantidad,
                                         precio_unitario=f.producto.precio))
    except StockInsuficiente as e:
        db.session.rollback()
        return error(f"{f.producto.nombre}: {e}", 409)

    db.session.add(orden)
    db.session.flush()
    factura = emitir_factura(orden, carrito.subtotal(), datos.metodo_pago, cfg["COMISION_PORCENTAJE"])
    lineas = construir_lineas(carrito.items(), factura.comision, cfg["STRIPE_CURRENCY"])
    base = cfg["BASE_URL"].rstrip("/")
    try:
        sesion = crear_sesion_stripe(
            cfg["STRIPE_SECRET_KEY"], mode="payment", line_items=lineas,
            client_reference_id=str(orden.id),
            metadata={"orden_id": str(orden.id)},
            payment_intent_data={"metadata": {"orden_id": str(orden.id)}},
            success_url=f"{base}/?pago=exitoso&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base}/?pago=cancelado",
            expires_at=int(time.time()) + MINUTOS_PARA_PAGAR * 60)
    except Exception:  # noqa: BLE001 - cualquier fallo de Stripe deshace la reserva de stock
        db.session.rollback()
        current_app.logger.exception("Stripe falló al crear la sesión de pago")
        return error("no se pudo iniciar el pago, intenta de nuevo", 502)

    orden.stripe_session_id = sesion["id"]
    orden.checkout_url = sesion["url"]
    CarritoItem.query.filter_by(usuario_id=u.id).delete()
    db.session.commit()
    return jsonify({"orden_id": orden.id, "checkout_url": orden.checkout_url,
                    "subtotal": float(factura.subtotal), "comision": float(factura.comision),
                    "total": float(factura.total), "expira_en_minutos": MINUTOS_PARA_PAGAR}), 201


def marcar_pagada(orden, sesion):
    """pendiente_pago -> preparando, y avisa a los actores mediante el Observer. Idempotente."""
    if orden.estado != PENDIENTE_PAGO:
        return False          # Stripe reintenta eventos: si ya se procesó, no se repite
    validar_transicion(orden.estado, PREPARANDO)
    orden.estado = PREPARANDO
    orden.stripe_payment_intent = sesion.get("payment_intent")
    bus_ordenes.notificar(orden, "pago_confirmado")
    return True


def liberar_orden(orden):
    """Sesión vencida o pago fallido: cancela la orden y devuelve el stock reservado."""
    if orden.estado != PENDIENTE_PAGO:
        return False
    for i in orden.items:
        i.producto.inventario.reponer(i.cantidad)
    orden.estado = CANCELADA
    return True


def _orden_de(sesion):
    bruto = sesion.get("client_reference_id") or (sesion.get("metadata") or {}).get("orden_id")
    try:
        return db.session.get(Orden, int(bruto))
    except (TypeError, ValueError):
        return None


@bp.post("/webhooks/stripe")
@documentar("POST", "/api/webhooks/stripe", "Webhook de Stripe (checkout.session.completed)", ["Pagos"], sesion=False,
            descripcion="Lo llama Stripe, no el frontend. Verifica la firma del encabezado Stripe-Signature. "
                        "Con pago confirmado, la orden pasa de pendiente_pago a preparando y el Observer notifica.",
            respuestas={200: ("Evento recibido (también si ya estaba procesado)", "OkDTO"),
                        400: ("Payload o firma inválidos", "ErrorDTO"),
                        503: ("Webhook sin configurar", "ErrorDTO")})
def webhook_stripe():
    secreto = current_app.config["STRIPE_WEBHOOK_SECRET"]
    if not secreto:
        return error("webhook de Stripe sin configurar", 503)
    try:
        evento = construir_evento(request.get_data(), request.headers.get("Stripe-Signature", ""), secreto)
    except ValueError:
        return error("payload inválido", 400)
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ == "SignatureVerificationError":
            return error("firma inválida", 400)
        raise

    sesion = evento["data"]["object"]
    orden = _orden_de(sesion)
    if orden is not None:
        if evento["type"] in EVENTOS_PAGO and sesion.get("payment_status") == "paid":
            marcar_pagada(orden, sesion)
        elif evento["type"] in EVENTOS_FALLO:
            liberar_orden(orden)
        db.session.commit()
    return jsonify({"ok": True})
