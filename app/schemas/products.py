from typing import Annotated

from pydantic import BaseModel, Field, model_validator


class StockResponse(BaseModel):
    sku: str
    available: int  # stock - reserved: what a new order can take
    on_hand: int    # physical units still present
    reserved: int   # committed to accepted, not-yet-settled orders


class ProductListItem(BaseModel):
    sku: str
    name: str
    price_cents: int
    in_stock: bool  # availability only — deliberately not the remaining count


class ProductCreateRequest(BaseModel):
    sku: Annotated[str, Field(min_length=1, max_length=200)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    price_cents: Annotated[int, Field(ge=0)]
    stock: Annotated[int, Field(ge=0)] = 0


class ProductDetail(BaseModel):
    sku: str
    name: str
    price_cents: int
    on_hand: int
    reserved: int
    available: int


class StockUpdateRequest(BaseModel):
    """Provide exactly one of `set` (absolute on-hand) or `add` (delta, may be negative)."""

    set: int | None = Field(default=None, ge=0)
    add: int | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.set is None) == (self.add is None):
            raise ValueError("provide exactly one of 'set' or 'add'")
        return self
