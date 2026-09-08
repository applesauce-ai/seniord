from fastapi import APIRouter, HTTPException, Response, status

from app.schemas.orders import CreateOrderRequest, CreateOrderResponse, OrderDetail
from app.services import order_service
from app.services.order_service import InsufficientStockError, UnknownSkuError

router = APIRouter(tags=["orders"])


@router.post("/orders", response_model=CreateOrderResponse)
def create_order(req: CreateOrderRequest, response: Response) -> CreateOrderResponse:
    try:
        result, created = order_service.create_order(req)
    except UnknownSkuError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except InsufficientStockError as exc:
        # 409 Conflict: the order was rejected because stock couldn't be reserved.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    # 201 on first creation, 200 when the order_ref already existed (idempotent).
    response.status_code = (
        status.HTTP_201_CREATED if created else status.HTTP_200_OK
    )
    return result


@router.get("/orders/{order_ref}", response_model=OrderDetail)
def get_order(order_ref: str) -> OrderDetail:
    order = order_service.get_order(order_ref)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )
    return order
