"""充值订单生命周期与余额账本。

状态机（与 sub2api 一致）：
  PENDING ─cancel→ CANCELLED      PENDING ─expiry→ EXPIRED
  PENDING / CANCELLED / EXPIRED(宽限内) ─回调成功→ PAID ─锁→ RECHARGING ─入账→ COMPLETED
                                                    └失败→ FAILED（下次回调 / 查单重试）
所有状态推进都是「WHERE status IN (...) 的条件更新 + rowcount 判定」：
重复回调、并发查单、过期任务撞在一起时只有一方能赢，其余各自按现状收尾。

余额只做相对更新（balance = balance ± x），并且每次变动同事务插一行 balance_ledger；
ledger.code 唯一，因此同一订单 / 同一扣费码不可能入账两次。
"""

from __future__ import annotations

import logging
import re
import secrets
import string
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.payment import BalanceLedger, PaymentAuditLog, PaymentOrder
from app.models.user import User
from app.payment.amounts import (
    amounts_match,
    calculate_credited_balance,
    calculate_pay_amount,
    has_valid_scale,
)
from app.payment.providers import get_provider
from app.payment.providers.base import (
    PaymentCreateRequest,
    PaymentCreateResult,
    PaymentNotification,
    PaymentProvider,
    PaymentProviderError,
)

logger = logging.getLogger(__name__)

# 用户可见的支付方式；具体由哪家网关承接是运行期配置
PAYMENT_TYPES: tuple[str, ...] = ("alipay", "wxpay")
# 结账页的金额快捷档位；超出全局上下限的会被过滤
PRESET_AMOUNTS: tuple[Decimal, ...] = tuple(
    Decimal(v) for v in ("10", "20", "50", "100", "200", "500")
)

# 过期后仍接受迟到回调的窗口：网关已扣款却因为我们先判过期而丢单，比多等几分钟糟得多
PAYMENT_GRACE = timedelta(minutes=5)
_ORDER_PREFIX = "ppt"
_ALNUM = string.ascii_letters + string.digits
_OUT_TRADE_NO_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

PAID_STATUSES: tuple[str, ...] = ("PAID", "RECHARGING", "COMPLETED")
TERMINAL_STATUSES: tuple[str, ...] = ("COMPLETED", "EXPIRED", "CANCELLED", "FAILED")


class PaymentError(HTTPException):
    """业务规则拒绝，直接带状态码；API 层原样抛出。"""


class PaymentRejected(Exception):
    """回调 / 查单结果与订单不符（网关、商户身份、金额），已写审计，拒绝入账。"""


class OrderNotFound(Exception):
    """回调里的商户单号在库里不存在。"""


class InsufficientBalance(HTTPException):
    def __init__(self, required: Decimal, balance: Decimal) -> None:
        super().__init__(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"余额不足：本次需要 {required:.2f}，当前余额 {balance:.2f}，请先充值",
        )
        self.required = required
        self.balance = balance


def _now() -> datetime:
    return datetime.now(UTC)


def sanitize_out_trade_no(raw: str | None) -> str | None:
    """回跳 / 公开查询里的商户单号只认白名单字符，避免把任意串带进页面。"""
    if raw and _OUT_TRADE_NO_RE.match(raw):
        return raw
    return None


def generate_out_trade_no() -> str:
    # 日期前缀便于人工排查；随机段用 secrets：单号会出现在回跳 URL 里，不能可猜
    return (
        _ORDER_PREFIX
        + _now().strftime("%Y%m%d")
        + "".join(secrets.choice(_ALNUM) for _ in range(10))
    )


def public_base_url() -> str:
    return get_settings().payment_public_base_url.rstrip("/")


def result_page_url(out_trade_no: str | None) -> str:
    url = f"{public_base_url()}/payment/result"
    return f"{url}?out_trade_no={out_trade_no}" if out_trade_no else url


async def audit(
    session: AsyncSession,
    order_id: uuid.UUID,
    action: str,
    *,
    operator: str = "system",
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        PaymentAuditLog(order_id=order_id, action=action, operator=operator, detail=detail or {})
    )


# ---------------------------------------------------------------- 余额账本


async def get_balance(session: AsyncSession, user_id: uuid.UUID) -> Decimal:
    value = await session.scalar(select(User.balance).where(User.id == user_id))
    return Decimal(value) if value is not None else Decimal("0.00")


async def _credit(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    amount: Decimal,
    code: str,
    ledger_type: str,
    order_id: uuid.UUID | None,
    notes: str | None,
) -> bool:
    """入账。code 已存在（重复兑现）返回 False 且余额不动。"""
    async with session.begin_nested():
        # 先插流水：撞唯一约束就在保存点内回滚，余额不会被动
        session.add(
            BalanceLedger(
                user_id=user_id,
                code=code,
                type=ledger_type,
                amount=amount,
                balance_after=Decimal("0"),
                order_id=order_id,
                notes=notes,
            )
        )
        try:
            await session.flush()
        except IntegrityError:
            return False
        new_balance = (
            await session.execute(
                update(User)
                .where(User.id == user_id)
                .values(balance=User.balance + amount)
                .returning(User.balance)
            )
        ).scalar_one()
        await session.execute(
            update(BalanceLedger)
            .where(BalanceLedger.code == code)
            .values(balance_after=new_balance)
        )
    return True


async def charge_balance(
    session: AsyncSession,
    user_id: uuid.UUID,
    amount: Decimal,
    *,
    code: str,
    notes: str | None = None,
) -> Decimal:
    """扣费并返回新余额。余额不足抛 InsufficientBalance(402)，不写任何东西。不 commit。"""
    amount = amount.quantize(Decimal("0.01"))
    if amount <= 0:
        return await get_balance(session, user_id)
    new_balance = (
        await session.execute(
            update(User)
            .where(User.id == user_id, User.balance >= amount)
            .values(balance=User.balance - amount)
            .returning(User.balance)
        )
    ).scalar_one_or_none()
    if new_balance is None:
        raise InsufficientBalance(amount, await get_balance(session, user_id))
    session.add(
        BalanceLedger(
            user_id=user_id,
            code=code,
            type="consume",
            amount=-amount,
            balance_after=new_balance,
            order_id=None,
            notes=notes,
        )
    )
    await session.flush()
    return Decimal(new_balance)


async def refund_balance(
    session: AsyncSession,
    user_id: uuid.UUID,
    amount: Decimal,
    *,
    code: str,
    notes: str | None = None,
) -> None:
    """把一笔已扣费用退回余额（如入队失败）。code 唯一，重复调用不会退两次。不 commit。"""
    amount = amount.quantize(Decimal("0.01"))
    if amount <= 0:
        return
    await _credit(
        session,
        user_id=user_id,
        amount=amount,
        code=code,
        ledger_type="refund",
        order_id=None,
        notes=notes,
    )


# ---------------------------------------------------------------- 下单


def _provider() -> PaymentProvider:
    try:
        return get_provider()
    except PaymentProviderError as error:
        raise PaymentError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error


def _validate_amount(amount: Decimal) -> Decimal:
    cfg = get_settings()
    if not amount.is_finite() or amount <= 0:
        raise PaymentError(status_code=status.HTTP_400_BAD_REQUEST, detail="充值金额无效")
    if not has_valid_scale(amount):
        raise PaymentError(status_code=status.HTTP_400_BAD_REQUEST, detail="充值金额最多两位小数")
    minimum, maximum = cfg.payment_min_amount, cfg.payment_max_amount
    if amount < minimum or (maximum > 0 and amount > maximum):
        bound = (
            f"需在 {minimum:.2f} ~ {maximum:.2f} 元之间"
            if maximum > 0
            else f"不能低于 {minimum:.2f} 元"
        )
        raise PaymentError(status_code=status.HTTP_400_BAD_REQUEST, detail=f"充值金额{bound}")
    return amount.quantize(Decimal("0.01"))


async def _check_limits(session: AsyncSession, user_id: uuid.UUID, pay_amount: Decimal) -> None:
    cfg = get_settings()
    now = _now()
    # 已超时但还没被巡检改状态的订单不占名额，否则用户得等一分钟才能重下
    pending = await session.scalar(
        select(func.count())
        .select_from(PaymentOrder)
        .where(
            PaymentOrder.user_id == user_id,
            PaymentOrder.status == "PENDING",
            PaymentOrder.expires_at > now,
        )
    )
    if (pending or 0) >= cfg.payment_max_pending_orders:
        raise PaymentError(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="待支付订单过多，请先完成或取消已有订单",
        )
    if cfg.payment_daily_limit > 0:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        used = await session.scalar(
            select(func.coalesce(func.sum(PaymentOrder.pay_amount), 0)).where(
                PaymentOrder.user_id == user_id,
                PaymentOrder.status.in_(PAID_STATUSES),
                PaymentOrder.paid_at >= day_start,
            )
        )
        if Decimal(used or 0) + pay_amount > cfg.payment_daily_limit:
            raise PaymentError(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="已达今日充值上限"
            )


async def _allocate_out_trade_no(session: AsyncSession) -> str:
    for _ in range(5):
        candidate = generate_out_trade_no()
        exists = await session.scalar(
            select(PaymentOrder.id).where(PaymentOrder.out_trade_no == candidate)
        )
        if exists is None:
            return candidate
    raise PaymentError(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="系统繁忙，请重试")


async def create_order(
    session: AsyncSession,
    user: User,
    *,
    amount: Decimal,
    payment_type: str,
    client_ip: str | None = None,
    is_mobile: bool = False,
) -> tuple[PaymentOrder, PaymentCreateResult]:
    cfg = get_settings()
    if not cfg.payment_enabled:
        raise PaymentError(status_code=status.HTTP_403_FORBIDDEN, detail="充值功能暂未开放")
    if payment_type not in PAYMENT_TYPES:
        raise PaymentError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="不支持的支付方式"
        )
    provider = _provider()
    amount = _validate_amount(amount)
    pay_amount = calculate_pay_amount(amount, cfg.payment_fee_rate)
    credited = calculate_credited_balance(amount, cfg.payment_recharge_multiplier)
    await _check_limits(session, user.id, pay_amount)

    out_trade_no = await _allocate_out_trade_no(session)
    order = PaymentOrder(
        user_id=user.id,
        out_trade_no=out_trade_no,
        order_type="balance",
        amount=credited,
        pay_amount=pay_amount,
        fee_rate=cfg.payment_fee_rate,
        currency=cfg.payment_currency,
        payment_type=payment_type,
        provider_key=provider.key,
        provider_snapshot={"provider_key": provider.key, **provider.merchant_snapshot()},
        status="PENDING",
        recharge_code=f"PAY-{out_trade_no}",
        client_ip=(client_ip or "")[:64] or None,
        expires_at=_now() + timedelta(minutes=max(cfg.payment_order_timeout_minutes, 1)),
    )
    session.add(order)
    await session.flush()

    base = public_base_url()
    request = PaymentCreateRequest(
        out_trade_no=out_trade_no,
        payment_type=payment_type,
        pay_amount=pay_amount,
        subject=f"AI PPT 余额充值 {credited:.2f}",
        notify_url=f"{base}/api/v1/payment/webhook/{provider.key}",
        return_url=f"{base}/api/v1/payment/return/{provider.key}?out_trade_no={out_trade_no}",
        client_ip=client_ip,
        is_mobile=is_mobile,
    )
    try:
        created = await provider.create_payment(request)
    except PaymentProviderError as error:
        order.status = "FAILED"
        order.failed_at = _now()
        order.failed_reason = str(error)
        await audit(session, order.id, "PROVIDER_CREATE_FAILED", detail={"error": str(error)})
        await session.commit()
        raise PaymentError(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error

    order.payment_trade_no = created.trade_no
    order.pay_url = created.pay_url
    order.qr_code = created.qr_code
    await audit(
        session,
        order.id,
        "ORDER_CREATED",
        operator=f"user:{user.id}",
        detail={"pay_amount": str(pay_amount), "credited": str(credited), "type": payment_type},
    )
    await session.commit()
    await session.refresh(order)
    return order, created


# ---------------------------------------------------------------- 确认与入账


async def handle_notification(
    session: AsyncSession, notification: PaymentNotification, provider_key: str
) -> PaymentOrder:
    """回调 / 主动查单 / 模拟支付三条路都汇到这里。"""
    order = await session.scalar(
        select(PaymentOrder).where(PaymentOrder.out_trade_no == notification.out_trade_no)
    )
    if order is None:
        raise OrderNotFound(notification.out_trade_no)
    operator = f"webhook:{provider_key}"

    if order.provider_key != provider_key:
        await _reject(session, order, "PAYMENT_PROVIDER_MISMATCH", operator, {"got": provider_key})
    snapshot = order.provider_snapshot or {}
    for field, value in notification.metadata.items():
        expected = snapshot.get(field)
        if expected is not None and str(expected) != str(value):
            await _reject(
                session,
                order,
                "PAYMENT_PROVIDER_METADATA_MISMATCH",
                operator,
                {field: value, "expected": str(expected)},
            )

    if not notification.success:
        await audit(session, order.id, "PAYMENT_NOT_SUCCESS", operator=operator)
        await session.commit()
        return order

    paid = notification.amount
    if not paid.is_finite() or paid <= 0:
        await _reject(session, order, "PAYMENT_INVALID_AMOUNT", operator, {"paid": str(paid)})
    if not amounts_match(Decimal(order.pay_amount), paid):
        await _reject(
            session,
            order,
            "PAYMENT_AMOUNT_MISMATCH",
            operator,
            {"paid": str(paid), "expected": str(order.pay_amount)},
        )

    previous = order.status
    if await _to_paid(session, order, trade_no=notification.trade_no, operator=operator):
        if previous in ("CANCELLED", "EXPIRED"):
            await audit(
                session, order.id, "ORDER_RECOVERED", operator=operator, detail={"from": previous}
            )
            await session.commit()
        await session.refresh(order)
        await fulfill(session, order)
        return order

    # 条件更新没赢：别人已经推进过了，按现状收尾
    await session.refresh(order)
    if order.status in ("PAID", "FAILED"):
        await fulfill(session, order)
    elif order.status == "EXPIRED":
        await audit(session, order.id, "PAYMENT_AFTER_EXPIRY", operator=operator)
        await session.commit()
        logger.warning("订单 %s 过期超过宽限期才收到支付成功", order.out_trade_no)
    return order


async def _reject(
    session: AsyncSession,
    order: PaymentOrder,
    action: str,
    operator: str,
    detail: dict[str, Any],
) -> None:
    await audit(session, order.id, action, operator=operator, detail=detail)
    await session.commit()
    raise PaymentRejected(action)


async def _to_paid(
    session: AsyncSession, order: PaymentOrder, *, trade_no: str, operator: str
) -> bool:
    now = _now()
    result = await session.execute(
        update(PaymentOrder)
        .where(
            PaymentOrder.id == order.id,
            (PaymentOrder.status.in_(("PENDING", "CANCELLED")))
            | (
                (PaymentOrder.status == "EXPIRED")
                & (PaymentOrder.updated_at >= now - PAYMENT_GRACE)
            ),
        )
        .values(
            status="PAID",
            paid_at=now,
            payment_trade_no=trade_no or PaymentOrder.payment_trade_no,
            failed_at=None,
            failed_reason=None,
        )
    )
    if result.rowcount == 0:
        await session.commit()
        return False
    await audit(session, order.id, "ORDER_PAID", operator=operator, detail={"trade_no": trade_no})
    await session.commit()
    return True


async def fulfill(session: AsyncSession, order: PaymentOrder) -> None:
    """PAID / FAILED → RECHARGING → 入账 → COMPLETED，每一步都是条件更新。"""
    locked = await session.execute(
        update(PaymentOrder)
        .where(PaymentOrder.id == order.id, PaymentOrder.status.in_(("PAID", "FAILED")))
        .values(status="RECHARGING")
    )
    await session.commit()
    if locked.rowcount == 0:
        await session.refresh(order)
        return
    try:
        credited = await _credit(
            session,
            user_id=order.user_id,
            amount=Decimal(order.amount),
            code=order.recharge_code,
            ledger_type="recharge",
            order_id=order.id,
            notes=f"充值 {order.out_trade_no}",
        )
        done = await session.execute(
            update(PaymentOrder)
            .where(PaymentOrder.id == order.id, PaymentOrder.status == "RECHARGING")
            .values(status="COMPLETED", completed_at=_now())
        )
        if done.rowcount:
            await audit(
                session,
                order.id,
                "RECHARGE_SUCCESS",
                detail={"amount": str(order.amount), "credited_now": credited},
            )
        await session.commit()
    except Exception as error:  # noqa: BLE001 - 任何失败都要把状态落到 FAILED
        await session.rollback()
        logger.exception("订单 %s 入账失败", order.out_trade_no)
        await session.execute(
            update(PaymentOrder)
            .where(PaymentOrder.id == order.id, PaymentOrder.status == "RECHARGING")
            .values(status="FAILED", failed_at=_now(), failed_reason=str(error)[:500])
        )
        await audit(session, order.id, "FULFILLMENT_FAILED", detail={"error": str(error)[:500]})
        await session.commit()
        raise
    await session.refresh(order)


# ---------------------------------------------------------------- 查单 / 取消 / 过期


async def refresh_expiry(session: AsyncSession, order: PaymentOrder) -> PaymentOrder:
    """读订单时顺手判过期：巡检有一分钟间隔，界面不该显示一个已经过期的「待支付」。"""
    if order.status == "PENDING" and order.expires_at <= _now():
        result = await session.execute(
            update(PaymentOrder)
            .where(PaymentOrder.id == order.id, PaymentOrder.status == "PENDING")
            .values(status="EXPIRED")
        )
        if result.rowcount:
            await audit(session, order.id, "ORDER_EXPIRED", detail={"lazy": True})
        await session.commit()
        await session.refresh(order)
    return order


async def verify_order(session: AsyncSession, order: PaymentOrder) -> PaymentOrder:
    """向网关主动查单；网关说已付就走正规入账链路。只对 PENDING / EXPIRED 有意义。"""
    if order.status not in ("PENDING", "EXPIRED"):
        return order
    provider = _provider()
    if provider.key != order.provider_key:
        return order
    try:
        result = await provider.query_order(order.out_trade_no)
    except PaymentProviderError as error:
        logger.warning("查单失败 %s: %s", order.out_trade_no, error)
        return order
    if result is None or not result.paid:
        # 只做对账，不在这里判过期：取消 / 巡检要靠 PENDING 状态继续往下走
        return order
    notification = PaymentNotification(
        out_trade_no=order.out_trade_no,
        trade_no=result.trade_no or order.payment_trade_no or "",
        amount=result.amount if result.amount is not None else Decimal(order.pay_amount),
        success=True,
        metadata=provider.merchant_snapshot(),
    )
    try:
        return await handle_notification(session, notification, provider.key)
    except PaymentRejected:
        await session.refresh(order)
        return order


async def cancel_order(
    session: AsyncSession,
    order: PaymentOrder,
    *,
    operator: str,
    new_status: str = "CANCELLED",
    check_upstream: bool = True,
) -> str:
    """取消或过期。先向网关确认没付过：付过就直接入账，而不是作废。"""
    if order.status != "PENDING":
        raise PaymentError(status_code=status.HTTP_409_CONFLICT, detail="订单当前状态不可取消")
    if check_upstream:
        checked = await verify_order(session, order)
        if checked.status in PAID_STATUSES:
            return "already_paid"
        if checked.status != "PENDING":
            return "noop"
    result = await session.execute(
        update(PaymentOrder)
        .where(PaymentOrder.id == order.id, PaymentOrder.status == "PENDING")
        .values(status=new_status)
    )
    if result.rowcount == 0:
        await session.commit()
        await session.refresh(order)
        return "already_paid" if order.status in PAID_STATUSES else "noop"
    await audit(
        session,
        order.id,
        "ORDER_EXPIRED" if new_status == "EXPIRED" else "ORDER_CANCELLED",
        operator=operator,
    )
    await session.commit()
    await session.refresh(order)
    return "cancelled"


async def expire_timed_out_orders(
    session: AsyncSession, *, check_upstream: bool = True, limit: int = 100
) -> int:
    """巡检入口：逐单走 cancel_order，网关侧已付的会被顺手入账。"""
    rows = list(
        await session.scalars(
            select(PaymentOrder)
            .where(PaymentOrder.status == "PENDING", PaymentOrder.expires_at <= _now())
            .order_by(PaymentOrder.expires_at)
            .limit(limit)
        )
    )
    expired = 0
    for order in rows:
        try:
            outcome = await cancel_order(
                session,
                order,
                operator="system",
                new_status="EXPIRED",
                check_upstream=check_upstream,
            )
        except PaymentError:
            continue
        if outcome == "cancelled":
            expired += 1
    return expired
