"""ERP · DTOs de entrada validados con Pydantic."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CheckoutDTO(BaseModel):
    """Petición para iniciar un pago con Stripe Checkout (los ítems salen del carrito del servidor)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ubicacion_entrega: str = Field(min_length=3, max_length=160, examples=["Biblioteca, entrada principal"])
    metodo_pago: Literal["tarjeta"] = "tarjeta"
