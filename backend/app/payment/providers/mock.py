"""本地联调用的假网关：不出网，把「已支付」记在进程内存里。

只在 PAYMENT_PROVIDER=mock 时装配。前端拿到 mock:// 二维码后
点「模拟支付成功」即调 /orders/{id}/mock-pay 走一遍完整兑现链路。
"""

import json
from collections.abc import Mapping
from decimal import Decimal

from app.payment.providers.base import (
    PaymentCreateRequest,
    PaymentCreateResult,
    PaymentNotification,
    PaymentProviderError,
    PaymentQueryResult,
)


class MockProvider:
    key = "mock"

    def __init__(self) -> None:
        self._paid: dict[str, Decimal] = {}

    def merchant_snapshot(self) -> dict[str, str]:
        return {"pid": "mock"}

    async def create_payment(self, request: PaymentCreateRequest) -> PaymentCreateResult:
        return PaymentCreateResult(
            payment_mode="qrcode",
            qr_code=f"mock://pay/{request.out_trade_no}?money={request.pay_amount:.2f}",
            trade_no=f"MOCK{request.out_trade_no}",
        )

    def mark_paid(self, out_trade_no: str, amount: Decimal) -> PaymentNotification:
        self._paid[out_trade_no] = amount
        return PaymentNotification(
            out_trade_no=out_trade_no,
            trade_no=f"MOCK{out_trade_no}",
            amount=amount,
            success=True,
            metadata={"pid": "mock"},
        )

    async def query_order(self, out_trade_no: str) -> PaymentQueryResult | None:
        amount = self._paid.get(out_trade_no)
        if amount is None:
            return PaymentQueryResult(paid=False)
        return PaymentQueryResult(paid=True, trade_no=f"MOCK{out_trade_no}", amount=amount)

    def verify_notification(
        self, raw_body: str, headers: Mapping[str, str]
    ) -> PaymentNotification | None:
        try:
            body = json.loads(raw_body)
        except ValueError as error:
            raise PaymentProviderError("mock 回调不是 JSON") from error
        return PaymentNotification(
            out_trade_no=str(body.get("out_trade_no", "")),
            trade_no=str(body.get("trade_no", "")),
            amount=Decimal(str(body.get("money", "0"))),
            success=body.get("trade_status") == "TRADE_SUCCESS",
            metadata={"pid": "mock"},
        )
