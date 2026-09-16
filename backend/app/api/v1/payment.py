"""充值：结账配置、钱包、下单、查单、取消、订单与流水，以及网关回调 / 回跳。

`router` 需要登录；`webhook_router` 不鉴权，签名即凭证。
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.db import async_session_factory, get_session
from app.models.payment import BalanceLedger, PaymentOrder
from app.models.user import User
from app.payment.providers import get_provider
from app.payment.providers.base import PaymentProviderError
from app.schemas.payment import (
    CreateOrderRequest,
    CreateOrderResponse,
    LedgerEntryPublic,
    PaymentConfigPublic,
    PaymentOrderPublic,
    WalletPublic,
)
from app.services import payment as payment_service
from app.services.payment import OrderNotFound, PaymentRejected

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payment", tags=["payment"])
webhook_router = APIRouter(prefix="/payment", tags=["payment"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]

_MAX_BODY = 1024 * 1024


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.get("/config", response_model=PaymentConfigPublic)
async def payment_config(_: CurrentUser) -> PaymentConfigPublic:
    cfg = get_settings()
    return PaymentConfigPublic(
        enabled=cfg.payment_enabled,
        provider=cfg.payment_provider,
        currency=cfg.payment_currency,
        payment_types=list(payment_service.PAYMENT_TYPES),
        min_amount=cfg.payment_min_amount,
        max_amount=cfg.payment_max_amount,
        fee_rate=cfg.payment_fee_rate,
        recharge_multiplier=cfg.payment_recharge_multiplier,
        order_timeout_minutes=cfg.payment_order_timeout_minutes,
        charge_per_page=cfg.charge_per_page,
        preset_amounts=[
            preset
            for preset in payment_service.PRESET_AMOUNTS
            if preset >= cfg.payment_min_amount
            and (cfg.payment_max_amount <= 0 or preset <= cfg.payment_max_amount)
        ],
    )


@router.get("/wallet", response_model=WalletPublic)
async def wallet(current_user: CurrentUser, session: SessionDep) -> WalletPublic:
    return WalletPublic(
        balance=await payment_service.get_balance(session, current_user.id),
        currency=get_settings().payment_currency,
    )


@router.get("/ledger", response_model=list[LedgerEntryPublic])
async def ledger(current_user: CurrentUser, session: SessionDep) -> list[BalanceLedger]:
    rows = await session.scalars(
        select(BalanceLedger)
        .where(BalanceLedger.user_id == current_user.id)
        .order_by(BalanceLedger.created_at.desc())
        .limit(200)
    )
    return list(rows)


@router.post("/orders", response_model=CreateOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    body: CreateOrderRequest, request: Request, current_user: CurrentUser, session: SessionDep
) -> CreateOrderResponse:
    order, created = await payment_service.create_order(
        session,
        current_user,
        amount=Decimal(body.amount),
        payment_type=body.payment_type,
        client_ip=_client_ip(request),
        is_mobile=body.is_mobile,
    )
    return CreateOrderResponse(
        order=PaymentOrderPublic.model_validate(order),
        payment_mode=created.payment_mode,
        pay_url=created.pay_url,
        qr_code=created.qr_code,
    )


@router.get("/orders", response_model=list[PaymentOrderPublic])
async def list_orders(current_user: CurrentUser, session: SessionDep) -> list[PaymentOrder]:
    rows = list(
        await session.scalars(
            select(PaymentOrder)
            .where(PaymentOrder.user_id == current_user.id)
            .order_by(PaymentOrder.created_at.desc())
            .limit(100)
        )
    )
    return [await payment_service.refresh_expiry(session, order) for order in rows]


async def _owned_order(session: AsyncSession, user: User, order_id: uuid.UUID) -> PaymentOrder:
    # 别人的订单一律 404 而非 403，避免响应码泄露 id 是否存在
    order = await session.scalar(
        select(PaymentOrder).where(PaymentOrder.id == order_id, PaymentOrder.user_id == user.id)
    )
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订单不存在")
    return await payment_service.refresh_expiry(session, order)


@router.get("/orders/by-trade-no/{out_trade_no}", response_model=PaymentOrderPublic)
async def get_order_by_trade_no(
    out_trade_no: str, current_user: CurrentUser, session: SessionDep
) -> PaymentOrder:
    """回跳结果页只拿得到商户单号，用它找回订单。"""
    order = await session.scalar(
        select(PaymentOrder).where(
            PaymentOrder.out_trade_no == out_trade_no, PaymentOrder.user_id == current_user.id
        )
    )
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订单不存在")
    return await payment_service.refresh_expiry(session, order)


@router.get("/orders/{order_id}", response_model=PaymentOrderPublic)
async def get_order(
    order_id: uuid.UUID, current_user: CurrentUser, session: SessionDep
) -> PaymentOrder:
    return await _owned_order(session, current_user, order_id)


@router.post("/orders/{order_id}/verify", response_model=PaymentOrderPublic)
async def verify_order(
    order_id: uuid.UUID, current_user: CurrentUser, session: SessionDep
) -> PaymentOrder:
    """主动向网关查单：回调丢失或用户已付却还在等时的兜底。"""
    order = await _owned_order(session, current_user, order_id)
    return await payment_service.verify_order(session, order)


@router.post("/orders/{order_id}/cancel", response_model=PaymentOrderPublic)
async def cancel_order(
    order_id: uuid.UUID, current_user: CurrentUser, session: SessionDep
) -> PaymentOrder:
    order = await _owned_order(session, current_user, order_id)
    await payment_service.cancel_order(session, order, operator=f"user:{current_user.id}")
    await session.refresh(order)
    return order


@router.post("/orders/{order_id}/mock-pay", response_model=PaymentOrderPublic)
async def mock_pay(
    order_id: uuid.UUID, current_user: CurrentUser, session: SessionDep
) -> PaymentOrder:
    """本地联调「模拟支付成功」：让 mock 网关记一笔已付，再走正规入账链路。"""
    cfg = get_settings()
    if cfg.payment_provider != "mock" or cfg.app_env == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    order = await _owned_order(session, current_user, order_id)
    provider = get_provider()
    notification = provider.mark_paid(order.out_trade_no, Decimal(order.pay_amount))  # type: ignore[attr-defined]
    try:
        return await payment_service.handle_notification(session, notification, provider.key)
    except PaymentRejected as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


# ---------------------------------------------------------------- 网关回调 / 回跳（无鉴权）


@webhook_router.api_route(
    "/webhook/{provider_key}", methods=["GET", "POST"], include_in_schema=False
)
async def gateway_notify(provider_key: str, request: Request) -> Response:
    """网关异步通知。响应体按易支付约定：成功回纯文本 success。

    订单不存在也回 2xx 让网关停止重试；签名失败 400；瞬时错误 500 让网关稍后重来。
    mock 通道不暴露公网回调（它只走 /orders/{id}/mock-pay）。
    """
    cfg = get_settings()
    if provider_key == "mock" or provider_key != cfg.payment_provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    try:
        provider = get_provider()
    except PaymentProviderError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found") from error

    if request.method == "GET":
        raw = request.url.query
    else:
        body = await request.body()
        if len(body) > _MAX_BODY:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="回调体过大")
        raw = body.decode("utf-8", "replace")
    headers = {key.lower(): value for key, value in request.headers.items()}

    try:
        notification = provider.verify_notification(raw, headers)
    except PaymentProviderError as error:
        logger.warning("%s 回调验签失败: %s", provider_key, error)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="verify failed"
        ) from error
    if notification is None:
        return Response("success", media_type="text/plain")

    async with async_session_factory() as session:
        try:
            await payment_service.handle_notification(session, notification, provider_key)
        except OrderNotFound:
            logger.warning("收到未知订单的回调 %s", notification.out_trade_no)
        except PaymentRejected as error:
            # 金额 / 身份不一致：已记审计，ack 停止重试，留给人工核对
            logger.warning("回调被拒 %s: %s", notification.out_trade_no, error)
    return Response("success", media_type="text/plain")


@webhook_router.get("/return/{provider_key}", include_in_schema=False)
async def gateway_return(provider_key: str, request: Request) -> RedirectResponse:
    """网关同步回跳：只负责把用户送到前端结果页，状态由异步通知与轮询推进。"""
    out_trade_no = payment_service.sanitize_out_trade_no(request.query_params.get("out_trade_no"))
    return RedirectResponse(
        payment_service.result_page_url(out_trade_no), status_code=status.HTTP_302_FOUND
    )
