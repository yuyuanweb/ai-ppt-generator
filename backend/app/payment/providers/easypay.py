"""易支付（EasyPay）协议通道。

签名规则与 sub2api 的 easyPaySign 完全一致：参数按 key ASCII 升序拼成 k=v&k=v，
剔除 sign / sign_type 与空值，末尾直接拼商户密钥，取 MD5 小写十六进制。
"""

import hashlib
import hmac
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import parse_qs, urlencode

import httpx

from app.payment.providers.base import (
    PaymentCreateRequest,
    PaymentCreateResult,
    PaymentNotification,
    PaymentProviderError,
    PaymentQueryResult,
)

TRADE_STATUS_SUCCESS = "TRADE_SUCCESS"
_TIMEOUT = httpx.Timeout(15.0)


def easypay_sign(params: Mapping[str, str], key: str) -> str:
    pieces = [
        f"{k}={params[k]}"
        for k in sorted(params)
        if k not in ("sign", "sign_type") and params[k] != ""
    ]
    raw = "&".join(pieces) + key
    return hashlib.md5(raw.encode("utf-8")).hexdigest()  # noqa: S324 协议规定 MD5


def easypay_verify_sign(params: Mapping[str, str], key: str, sign: str) -> bool:
    return hmac.compare_digest(easypay_sign(params, key), sign)


def parse_notify_body(raw_body: str) -> dict[str, str]:
    values = parse_qs(raw_body, keep_blank_values=True)
    return {k: v[0] for k, v in values.items()}


class EasyPayProvider:
    key = "easypay"

    def __init__(
        self,
        *,
        pid: str,
        secret: str,
        api_base: str,
        mode: Literal["api", "submit"] = "api",
    ) -> None:
        if not pid or not secret or not api_base:
            raise PaymentProviderError(
                "易支付商户参数未配置（EASYPAY_PID / EASYPAY_KEY / EASYPAY_API_BASE）"
            )
        self._pid = pid
        self._secret = secret
        self._api_base = api_base.rstrip("/")
        self._mode = mode

    def merchant_snapshot(self) -> dict[str, str]:
        return {"pid": self._pid}

    def _signed(self, params: dict[str, str]) -> dict[str, str]:
        signed = {k: v for k, v in params.items() if v != ""}
        signed["sign"] = easypay_sign(signed, self._secret)
        signed["sign_type"] = "MD5"
        return signed

    async def create_payment(self, request: PaymentCreateRequest) -> PaymentCreateResult:
        params: dict[str, str] = {
            "pid": self._pid,
            "type": request.payment_type,
            "out_trade_no": request.out_trade_no,
            "notify_url": request.notify_url,
            "return_url": request.return_url,
            "name": request.subject,
            "money": f"{request.pay_amount:.2f}",
        }
        if request.is_mobile:
            params["device"] = "mobile"

        if self._mode == "submit":
            # 不经服务端，直接把用户浏览器引到网关收银台
            url = f"{self._api_base}/submit.php?{urlencode(self._signed(params))}"
            return PaymentCreateResult(payment_mode="redirect", pay_url=url)

        params["clientip"] = request.client_ip or "127.0.0.1"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            try:
                response = await client.post(
                    f"{self._api_base}/mapi.php", data=self._signed(params)
                )
                response.raise_for_status()
                body = response.json()
            except (httpx.HTTPError, ValueError) as error:
                raise PaymentProviderError(f"易支付下单请求失败：{error}") from error

        if str(body.get("code")) != "1":
            raise PaymentProviderError(f"易支付下单被拒绝：{body.get('msg') or body}")

        trade_no = body.get("trade_no")
        qr_code = body.get("qrcode") or None
        # 移动端优先 payurl2（H5 页），桌面端优先二维码
        pay_url = (body.get("payurl2") if request.is_mobile else None) or body.get("payurl") or None
        if qr_code and not request.is_mobile:
            return PaymentCreateResult(
                payment_mode="qrcode", qr_code=qr_code, pay_url=pay_url, trade_no=trade_no
            )
        if pay_url:
            return PaymentCreateResult(
                payment_mode="redirect", pay_url=pay_url, qr_code=qr_code, trade_no=trade_no
            )
        if qr_code:
            return PaymentCreateResult(payment_mode="qrcode", qr_code=qr_code, trade_no=trade_no)
        raise PaymentProviderError("易支付未返回支付地址")

    async def query_order(self, out_trade_no: str) -> PaymentQueryResult | None:
        params = {
            "act": "order",
            "pid": self._pid,
            "key": self._secret,
            "out_trade_no": out_trade_no,
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            try:
                response = await client.get(f"{self._api_base}/api.php", params=params)
                response.raise_for_status()
                body = response.json()
            except (httpx.HTTPError, ValueError) as error:
                raise PaymentProviderError(f"易支付查单失败：{error}") from error
        if str(body.get("code")) != "1":
            return None
        paid = str(body.get("status")) == "1"
        amount = _parse_amount(body.get("money"))
        return PaymentQueryResult(paid=paid, trade_no=body.get("trade_no"), amount=amount)

    def verify_notification(
        self, raw_body: str, headers: Mapping[str, str]
    ) -> PaymentNotification | None:
        params = parse_notify_body(raw_body)
        sign = params.get("sign", "")
        if not sign:
            raise PaymentProviderError("回调缺少 sign")
        if not easypay_verify_sign(params, self._secret, sign):
            raise PaymentProviderError("回调验签失败")
        amount = _parse_amount(params.get("money"))
        if amount is None:
            raise PaymentProviderError("回调金额无法解析")
        return PaymentNotification(
            out_trade_no=params.get("out_trade_no", ""),
            trade_no=params.get("trade_no", ""),
            amount=amount,
            success=params.get("trade_status") == TRADE_STATUS_SUCCESS,
            metadata={"pid": params.get("pid", "")},
        )


def _parse_amount(raw: object) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None
