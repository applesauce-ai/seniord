from fastapi import APIRouter, HTTPException, status

from app.schemas.products import (
    ProductCreateRequest,
    ProductDetail,
    ProductListItem,
    StockResponse,
    StockUpdateRequest,
)
from app.services import product_service
from app.services.product_service import ProductExistsError, StockUpdateError

router = APIRouter(tags=["products"])


@router.get("/products", response_model=list[ProductListItem])
def list_products() -> list[ProductListItem]:
    return [ProductListItem(**p) for p in product_service.list_products()]


@router.post("/products", response_model=ProductDetail, status_code=status.HTTP_201_CREATED)
def create_product(req: ProductCreateRequest) -> ProductDetail:
    try:
        data = product_service.create_product(
            req.sku, req.name, req.price_cents, req.stock
        )
    except ProductExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return ProductDetail(**data)


@router.get("/products/{sku}/stock", response_model=StockResponse)
def get_stock(sku: str) -> StockResponse:
    data = product_service.get_stock(sku)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found"
        )
    return StockResponse(sku=sku, **data)


@router.patch("/products/{sku}/stock", response_model=StockResponse)
def update_stock(sku: str, req: StockUpdateRequest) -> StockResponse:
    try:
        data = product_service.update_stock(sku, set_to=req.set, add=req.add)
    except StockUpdateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found"
        )
    return StockResponse(sku=sku, **data)
