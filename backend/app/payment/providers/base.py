from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Protocol

PaymentMode = Literal["qrcode", "redirect"]


class PaymentProviderError(Exception):
    """网关调用失败或回调验签失败。"""


@dataclass(frozen=True)
class PaymentCreateRequest:
    out_trade_no: str
    payment_type: str
    pay_amount: Decimal
    subject: str
    notify_url: str
    return_url: str
    client_ip: str | None = None
    is_mobile: bool = False


@dataclass(frozen=True)
class PaymentCreateResult:
    payment_mode: PaymentMode
    pay_url: str | None = None
    qr_code: str | None = None
    trade_no: str | None = None


@dataclass(frozen=True)
class PaymentNotification:
    out_trade_no: str
    trade_no: str
    amount: Decimal
    success: bool
    # 网关回传的商户身份（如 pid / app_id），与下单快照比对
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentQueryResult:
    paid: bool
    trade_no: str | None = None
    amount: Decimal | None = None


class PaymentProvider(Protocol):
    key: str

    def merchant_snapshot(self) -> dict[str, str]:
        """下单时冻结进订单的商户身份，回调时逐字段比对。"""
        ...

    async def create_payment(self, request: PaymentCreateRequest) -> PaymentCreateResult: ...

    async def query_order(self, out_trade_no: str) -> PaymentQueryResult | None: ...

    def verify_notification(
        self, raw_body: str, headers: Mapping[str, str]
    ) -> PaymentNotification | None:
        """验签并解析回调；验签失败抛 PaymentProviderError；无关事件返回 None。"""
        ...
