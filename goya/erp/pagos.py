"""ERP · Pagos con Stripe Checkout (Sandbox).

Las funciones puras (a_centavos, construir_lineas) no dependen de Stripe; los envoltorios
importan `stripe` solo al usarse, así la app arranca aunque no esté configurado.
"""
from decimal import Decimal, ROUND_HALF_UP


def a_centavos(monto):
    """Stripe trabaja en la unidad mínima de la moneda: $21.00 MXN -> 2100."""
    return int((Decimal(str(monto)) * 100).quantize(Decimal("1"), ROUND_HALF_UP))


def construir_lineas(items, comision, moneda="mxn"):
    """Un renglón por producto y otro por la comisión de servicio (total = subtotal + comisión)."""
    lineas = [{
        "quantity": i["cantidad"],
        "price_data": {"currency": moneda, "unit_amount": a_centavos(i["precio"]),
                       "product_data": {"name": (i["nombre"] or "Producto")[:250]}},
    } for i in items]
    if a_centavos(comision) > 0:
        lineas.append({"quantity": 1, "price_data": {
            "currency": moneda, "unit_amount": a_centavos(comision),
            "product_data": {"name": "Comisión de servicio GO-ya"}}})
    return lineas


def total_centavos(lineas):
    return sum(l["quantity"] * l["price_data"]["unit_amount"] for l in lineas)


def crear_sesion_stripe(api_key, **datos):
    import stripe
    stripe.api_key = api_key
    return stripe.checkout.Session.create(**datos)


def construir_evento(payload, firma, secreto):
    """Verifica la firma del webhook y devuelve el evento (ValueError o SignatureVerificationError si falla)."""
    import stripe
    return stripe.Webhook.construct_event(payload, firma, secreto)


def expirar_sesion_stripe(api_key, session_id):
    """Cierra una sesión abierta para que no se pueda pagar una orden cancelada (mejor esfuerzo)."""
    try:
        import stripe
        stripe.api_key = api_key
        stripe.checkout.Session.expire(session_id)
    except Exception:  # noqa: BLE001 - la sesión puede haber expirado ya
        pass
