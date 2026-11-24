"""ERP · Estados: máquina de estados de una orden."""

PENDIENTE_PAGO, CREADA, PREPARANDO, EN_CAMINO, ENTREGADA, CANCELADA = (
    "pendiente_pago", "creada", "preparando", "en_camino", "entregada", "cancelada")
ESTADOS = (PENDIENTE_PAGO, CREADA, PREPARANDO, EN_CAMINO, ENTREGADA, CANCELADA)

TRANSICIONES = {
    PENDIENTE_PAGO: {PREPARANDO, CANCELADA},   # PREPARANDO solo lo fija el webhook de Stripe
    CREADA: {PREPARANDO, CANCELADA},
    PREPARANDO: {EN_CAMINO, CANCELADA},
    EN_CAMINO: {ENTREGADA},
    ENTREGADA: set(),
    CANCELADA: set(),
}


class TransicionInvalida(ValueError):
    pass


def validar_transicion(actual, nuevo):
    if nuevo not in ESTADOS:
        raise TransicionInvalida(f"estado debe ser uno de {ESTADOS}")
    if nuevo not in TRANSICIONES[actual]:
        raise TransicionInvalida(f"transición inválida: {actual} → {nuevo}")
    return nuevo
