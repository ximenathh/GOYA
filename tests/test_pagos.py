import unittest
from decimal import Decimal

from goya.erp.estados import ESTADOS, PENDIENTE_PAGO, TransicionInvalida, validar_transicion
from goya.erp.pagos import a_centavos, construir_lineas, total_centavos
from goya.erp.total import calcular_total

try:
    import pydantic  # noqa: F401
    HAY_PYDANTIC = True
except ImportError:
    HAY_PYDANTIC = False


class TestPagos(unittest.TestCase):
    def test_a_centavos(self):
        self.assertEqual(a_centavos(Decimal("21.00")), 2100)
        self.assertEqual(a_centavos("0.01"), 1)
        self.assertEqual(a_centavos(Decimal("10.105")), 1011)       # redondeo half-up

    def test_ejemplo_20_mas_comision_5_porciento(self):
        t = calcular_total("20", 5)                                   # $20 + $1 = $21
        items = [{"nombre": "Torta", "precio": Decimal("20.00"), "cantidad": 1}]
        lineas = construir_lineas(items, t["comision"], "mxn")
        self.assertEqual(total_centavos(lineas), 2100)
        self.assertEqual([l["price_data"]["unit_amount"] for l in lineas], [2000, 100])
        self.assertEqual(lineas[1]["price_data"]["product_data"]["name"], "Comisión de servicio GO-ya")

    def test_varios_items_y_cantidades(self):
        items = [{"nombre": "Torta", "precio": Decimal("40"), "cantidad": 2},
                 {"nombre": "Café", "precio": Decimal("25.50"), "cantidad": 1}]
        t = calcular_total("105.50", 5)
        self.assertEqual(total_centavos(construir_lineas(items, t["comision"])), a_centavos(t["total"]))

    def test_sin_comision_no_agrega_renglon(self):
        items = [{"nombre": "X", "precio": Decimal("10"), "cantidad": 1}]
        self.assertEqual(len(construir_lineas(items, Decimal("0"))), 1)


class TestEstadoPendientePago(unittest.TestCase):
    def test_transiciones(self):
        self.assertIn(PENDIENTE_PAGO, ESTADOS)
        self.assertEqual(validar_transicion("pendiente_pago", "preparando"), "preparando")
        self.assertEqual(validar_transicion("pendiente_pago", "cancelada"), "cancelada")
        for malo in ("en_camino", "entregada", "creada"):
            with self.assertRaises(TransicionInvalida):
                validar_transicion("pendiente_pago", malo)


@unittest.skipUnless(HAY_PYDANTIC, "Pydantic no instalado")
class TestCheckoutDTO(unittest.TestCase):
    def test_valido(self):
        from goya.erp.dto import CheckoutDTO
        d = CheckoutDTO.model_validate({"ubicacion_entrega": "  Biblioteca  "})
        self.assertEqual((d.ubicacion_entrega, d.metodo_pago), ("Biblioteca", "tarjeta"))

    def test_invalidos(self):
        from pydantic import ValidationError
        from goya.erp.dto import CheckoutDTO
        for malo in ({}, {"ubicacion_entrega": "ab"}, {"ubicacion_entrega": "x" * 161},
                     {"ubicacion_entrega": "Aula 3", "metodo_pago": "bitcoin"},
                     {"ubicacion_entrega": "Aula 3", "total": 0}):          # extra="forbid": no se puede mandar el total
            with self.assertRaises(ValidationError):
                CheckoutDTO.model_validate(malo)
