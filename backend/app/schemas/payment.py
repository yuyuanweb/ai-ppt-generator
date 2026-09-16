from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PaymentType = Literal["alipay", "wxpay"]
PaymentMode = Literal["qrcode", "redirect"]


class PaymentConfigPublic(BaseModel):
    """结账页一次拿齐：开关、可用方式、金额边界、费率、倍率与每页扣费。"""

    enabled: bool
    provider: str
    currency: str
    payment_types: list[str]
    min_amount: Decimal
    # 0 表示不限
    max_amount: Decimal
    fee_rate: Decimal
    recharge_multiplier: Decimal
    order_timeout_minutes: int
    # 每生成一页扣多少余额；0 = 免费
    charge_per_page: Decimal
    preset_amounts: list[Decimal]


class WalletPublic(BaseModel):
    balance: Decimal
    currency: str


class CreateOrderRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12)
    payment_type: PaymentType
    is_mobile: bool = False


class PaymentOrderPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    out_trade_no: str
    order_type: str
    amount: Decimal
    pay_amount: Decimal
    fee_rate: Decimal
    currency: str
    payment_type: str
    provider_key: str
    status: str
    pay_url: str | None
    qr_code: str | None
    expires_at: datetime
    paid_at: datetime | None
    completed_at: datetime | None
    failed_reason: str | None
    created_at: datetime


class CreateOrderResponse(BaseModel):
    order: PaymentOrderPublic
    payment_mode: PaymentMode
    pay_url: str | None
    qr_code: str | None


class LedgerEntryPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    type: str
    amount: Decimal
    balance_after: Decimal
    order_id: uuid.UUID | None
    notes: str | None
    created_at: datetime
