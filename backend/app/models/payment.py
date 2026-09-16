"""充值订单、余额流水与审计日志。

沿用 sub2api 的思路：订单状态机全部用「条件更新 + rowcount」推进，
余额入账靠 balance_ledger.code 唯一约束保证同一订单只兑现一次
（对应 sub2api 里用兑换码 recharge_code 做幂等键的做法）。
表结构与 alembic f6a7b8c9d0e1 一致。
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

OrderStatus = Literal[
    "PENDING",
    "PAID",
    "RECHARGING",
    "COMPLETED",
    "EXPIRED",
    "CANCELLED",
    "FAILED",
]
LedgerType = Literal["recharge", "consume", "refund", "adjust"]


class PaymentOrder(Base):
    __tablename__ = "payment_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 我方商户订单号，发给网关；回调按它找回订单
    out_trade_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False, default="balance")

    # amount = 到账余额（乘过倍率）；pay_amount = 用户实付（含手续费）
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    pay_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    fee_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=Decimal(0))
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")

    payment_type: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(30), nullable=False)
    payment_trade_no: Mapped[str | None] = mapped_column(String(128))
    pay_url: Mapped[str | None] = mapped_column(Text)
    qr_code: Mapped[str | None] = mapped_column(Text)
    # 下单时冻结的商户身份（pid 等），回调时逐字段比对，防止换配置后串单
    provider_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", index=True)
    # 入账幂等键：balance_ledger.code 唯一，重复兑现会撞约束
    recharge_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    client_ip: Mapped[str | None] = mapped_column(String(64))

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BalanceLedger(Base):
    """余额流水：每一笔余额变动一行，balance_after 便于对账。"""

    __tablename__ = "balance_ledger"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 业务幂等键：充值用订单的 recharge_code，扣费由调用方给出唯一码
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    # 正数入账，负数扣减
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class PaymentAuditLog(Base):
    __tablename__ = "payment_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # system / user:<id> / webhook:<provider>
    operator: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
