import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.core.db import async_session_factory
from app.main import app
from app.models.payment import BalanceLedger, PaymentAuditLog, PaymentOrder
from app.payment.amounts import (
    amounts_match,
    calculate_credited_balance,
    calculate_pay_amount,
    has_valid_scale,
)
from app.payment.providers.base import PaymentNotification
from app.payment.providers.easypay import easypay_sign, easypay_verify_sign, parse_notify_body
from app.services import payment as payment_service


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _register(client: AsyncClient) -> tuple[dict[str, str], str]:
    email = f"pay_{uuid.uuid4().hex}@example.com"
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": "password123"}
    )
    assert response.status_code == 201
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


async def _create_order(client: AsyncClient, headers: dict[str, str], amount: str = "10") -> dict:
    response = await client.post(
        "/api/v1/payment/orders",
        json={"amount": amount, "payment_type": "alipay"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------- 纯函数


def test_pay_amount_rounds_fee_up() -> None:
    # 10 元 0.6% 手续费 = 0.06 元；100.01 元 0.6% = 0.60006 → 向上到 0.61
    assert calculate_pay_amount(Decimal("10"), Decimal("0.6")) == Decimal("10.06")
    assert calculate_pay_amount(Decimal("100.01"), Decimal("0.6")) == Decimal("100.62")
    assert calculate_pay_amount(Decimal("10"), Decimal("0")) == Decimal("10.00")


def test_credited_balance_multiplier() -> None:
    assert calculate_credited_balance(Decimal("100"), Decimal("1.1")) == Decimal("110.00")
    assert calculate_credited_balance(Decimal("100"), Decimal("0")) == Decimal("100.00")
    assert calculate_credited_balance(Decimal("33.33"), Decimal("1.5")) == Decimal("50.00")


def test_amount_tolerance_and_scale() -> None:
    assert amounts_match(Decimal("10.00"), Decimal("10.01"))
    assert not amounts_match(Decimal("10.00"), Decimal("10.02"))
    assert has_valid_scale(Decimal("10.50"))
    assert not has_valid_scale(Decimal("10.505"))


def test_easypay_sign_matches_protocol() -> None:
    params = {
        "pid": "1001",
        "type": "alipay",
        "out_trade_no": "ppt20260904abc",
        "notify_url": "https://x/notify",
        "return_url": "https://x/return",
        "name": "充值",
        "money": "10.00",
        "sign_type": "MD5",
        "sign": "should-be-ignored",
        "empty": "",
    }
    import hashlib

    expected_raw = (
        "money=10.00&name=充值&notify_url=https://x/notify&out_trade_no=ppt20260904abc"
        "&pid=1001&return_url=https://x/return&type=alipay" + "secret"
    )
    expected = hashlib.md5(expected_raw.encode()).hexdigest()  # noqa: S324
    assert easypay_sign(params, "secret") == expected
    assert easypay_verify_sign(params, "secret", expected)
    assert not easypay_verify_sign(params, "secret", "0" * 32)
    assert not easypay_verify_sign(params, "other", expected)


def test_easypay_parse_notify_body() -> None:
    params = parse_notify_body("pid=1&trade_status=TRADE_SUCCESS&money=10.00&remark=")
    assert params == {"pid": "1", "trade_status": "TRADE_SUCCESS", "money": "10.00", "remark": ""}


# ---------------------------------------------------------------- 接口


@pytest.mark.asyncio
async def test_config_and_empty_wallet(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    config = await client.get("/api/v1/payment/config", headers=headers)
    assert config.status_code == 200
    body = config.json()
    assert body["enabled"] is True
    assert body["provider"] == "mock"
    assert body["payment_types"] == ["alipay", "wxpay"]

    wallet = await client.get("/api/v1/payment/wallet", headers=headers)
    assert wallet.status_code == 200
    assert Decimal(str(wallet.json()["balance"])) == Decimal("0")


@pytest.mark.asyncio
async def test_config_requires_login(client: AsyncClient) -> None:
    response = await client.get("/api/v1/payment/wallet")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_order_pending_with_mock_qr(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    created = await _create_order(client, headers, "10")
    order = created["order"]
    assert order["status"] == "PENDING"
    assert order["payment_type"] == "alipay"
    assert order["provider_key"] == "mock"
    assert created["payment_mode"] == "qrcode"
    assert created["qr_code"].startswith("mock://pay/")
    assert order["out_trade_no"].startswith("ppt")

    listed = await client.get("/api/v1/payment/orders", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [order["id"]]

    by_no = await client.get(
        f"/api/v1/payment/orders/by-trade-no/{order['out_trade_no']}", headers=headers
    )
    assert by_no.status_code == 200
    assert by_no.json()["id"] == order["id"]


@pytest.mark.asyncio
async def test_create_order_validation(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    too_small = await client.post(
        "/api/v1/payment/orders",
        json={"amount": "0.5", "payment_type": "alipay"},
        headers=headers,
    )
    assert too_small.status_code == 400
    assert "充值金额" in too_small.json()["detail"]

    bad_type = await client.post(
        "/api/v1/payment/orders",
        json={"amount": "10", "payment_type": "paypal"},
        headers=headers,
    )
    assert bad_type.status_code == 422


@pytest.mark.asyncio
async def test_pending_order_limit(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    for _ in range(3):
        await _create_order(client, headers, "10")
    fourth = await client.post(
        "/api/v1/payment/orders",
        json={"amount": "10", "payment_type": "wxpay"},
        headers=headers,
    )
    assert fourth.status_code == 429
    assert fourth.json()["detail"] == "待支付订单过多，请先完成或取消已有订单"


@pytest.mark.asyncio
async def test_mock_pay_credits_balance_once(client: AsyncClient) -> None:
    headers, user_id = await _register(client)
    created = await _create_order(client, headers, "50")
    order_id = created["order"]["id"]

    paid = await client.post(f"/api/v1/payment/orders/{order_id}/mock-pay", headers=headers)
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "COMPLETED"
    assert paid.json()["paid_at"] is not None

    wallet = await client.get("/api/v1/payment/wallet", headers=headers)
    assert Decimal(str(wallet.json()["balance"])) == Decimal("50.00")

    # 重复回调：状态不变、余额不变、流水仍只有一条
    again = await client.post(f"/api/v1/payment/orders/{order_id}/mock-pay", headers=headers)
    assert again.status_code == 200
    assert again.json()["status"] == "COMPLETED"
    wallet = await client.get("/api/v1/payment/wallet", headers=headers)
    assert Decimal(str(wallet.json()["balance"])) == Decimal("50.00")

    ledger = await client.get("/api/v1/payment/ledger", headers=headers)
    assert ledger.status_code == 200
    entries = ledger.json()
    assert len(entries) == 1
    assert entries[0]["type"] == "recharge"
    assert Decimal(str(entries[0]["amount"])) == Decimal("50.00")
    assert Decimal(str(entries[0]["balance_after"])) == Decimal("50.00")
    assert entries[0]["order_id"] == order_id


@pytest.mark.asyncio
async def test_amount_mismatch_is_rejected_and_audited(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    created = await _create_order(client, headers, "20")
    out_trade_no = created["order"]["out_trade_no"]

    async with async_session_factory() as session:
        notification = PaymentNotification(
            out_trade_no=out_trade_no,
            trade_no="MOCKX",
            amount=Decimal("19.00"),
            success=True,
            metadata={"pid": "mock"},
        )
        with pytest.raises(payment_service.PaymentRejected):
            await payment_service.handle_notification(session, notification, "mock")

        order = (
            await session.execute(
                select(PaymentOrder).where(PaymentOrder.out_trade_no == out_trade_no)
            )
        ).scalar_one()
        assert order.status == "PENDING"
        actions = (
            await session.execute(
                select(PaymentAuditLog.action).where(PaymentAuditLog.order_id == order.id)
            )
        ).scalars()
        assert "PAYMENT_AMOUNT_MISMATCH" in list(actions)


@pytest.mark.asyncio
async def test_provider_metadata_mismatch_is_rejected(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    created = await _create_order(client, headers, "20")
    out_trade_no = created["order"]["out_trade_no"]
    async with async_session_factory() as session:
        notification = PaymentNotification(
            out_trade_no=out_trade_no,
            trade_no="MOCKX",
            amount=Decimal("20.00"),
            success=True,
            metadata={"pid": "someone-else"},
        )
        with pytest.raises(payment_service.PaymentRejected):
            await payment_service.handle_notification(session, notification, "mock")
        with pytest.raises(payment_service.PaymentRejected):
            await payment_service.handle_notification(
                session,
                PaymentNotification(
                    out_trade_no=out_trade_no,
                    trade_no="X",
                    amount=Decimal("20.00"),
                    success=True,
                ),
                "easypay",
            )


@pytest.mark.asyncio
async def test_unknown_order_notification(client: AsyncClient) -> None:
    async with async_session_factory() as session:
        with pytest.raises(payment_service.OrderNotFound):
            await payment_service.handle_notification(
                session,
                PaymentNotification(
                    out_trade_no="ppt00000000nope", trade_no="X", amount=Decimal("1"), success=True
                ),
                "mock",
            )


@pytest.mark.asyncio
async def test_cancel_then_late_payment_recovers(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    created = await _create_order(client, headers, "15")
    order_id = created["order"]["id"]

    cancelled = await client.post(f"/api/v1/payment/orders/{order_id}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    twice = await client.post(f"/api/v1/payment/orders/{order_id}/cancel", headers=headers)
    assert twice.status_code == 409
    assert twice.json()["detail"] == "订单当前状态不可取消"

    # 用户取消后网关仍回调成功：宽限期内照常兑现，避免钱进了网关却不到账
    paid = await client.post(f"/api/v1/payment/orders/{order_id}/mock-pay", headers=headers)
    assert paid.status_code == 200
    assert paid.json()["status"] == "COMPLETED"
    wallet = await client.get("/api/v1/payment/wallet", headers=headers)
    assert Decimal(str(wallet.json()["balance"])) == Decimal("15.00")

    async with async_session_factory() as session:
        actions = list(
            (
                await session.execute(
                    select(PaymentAuditLog.action).where(
                        PaymentAuditLog.order_id == uuid.UUID(order_id)
                    )
                )
            ).scalars()
        )
    assert "ORDER_RECOVERED" in actions
    assert "RECHARGE_SUCCESS" in actions


@pytest.mark.asyncio
async def test_timed_out_order_expires_on_read(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    created = await _create_order(client, headers, "10")
    order_id = uuid.UUID(created["order"]["id"])

    async with async_session_factory() as session:
        await session.execute(
            update(PaymentOrder)
            .where(PaymentOrder.id == order_id)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()

    fetched = await client.get(f"/api/v1/payment/orders/{order_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "EXPIRED"

    # 过期后原槽位释放，不再计入待支付上限
    for _ in range(3):
        await _create_order(client, headers, "10")


@pytest.mark.asyncio
async def test_expire_sweeper(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    created = await _create_order(client, headers, "10")
    order_id = uuid.UUID(created["order"]["id"])
    async with async_session_factory() as session:
        await session.execute(
            update(PaymentOrder)
            .where(PaymentOrder.id == order_id)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()
        expired = await payment_service.expire_timed_out_orders(session, check_upstream=True)
        assert expired >= 1
        order = (
            await session.execute(select(PaymentOrder).where(PaymentOrder.id == order_id))
        ).scalar_one()
        assert order.status == "EXPIRED"


@pytest.mark.asyncio
async def test_verify_order_reconciles_with_gateway(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    created = await _create_order(client, headers, "10")
    order = created["order"]
    # 网关侧已付但回调没到：主动查单应当直接兑现
    from app.payment.providers import get_provider

    get_provider().mark_paid(order["out_trade_no"], Decimal("10.00"))  # type: ignore[attr-defined]
    verified = await client.post(f"/api/v1/payment/orders/{order['id']}/verify", headers=headers)
    assert verified.status_code == 200
    assert verified.json()["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_orders_are_user_scoped(client: AsyncClient) -> None:
    headers_a, _ = await _register(client)
    headers_b, _ = await _register(client)
    created = await _create_order(client, headers_a, "10")
    order_id = created["order"]["id"]
    other = await client.get(f"/api/v1/payment/orders/{order_id}", headers=headers_b)
    assert other.status_code == 404
    assert other.json()["detail"] == "订单不存在"
    cancel = await client.post(f"/api/v1/payment/orders/{order_id}/cancel", headers=headers_b)
    assert cancel.status_code == 404


@pytest.mark.asyncio
async def test_charge_balance_requires_funds(client: AsyncClient) -> None:
    headers, user_id = await _register(client)
    uid = uuid.UUID(user_id)
    async with async_session_factory() as session:
        with pytest.raises(payment_service.InsufficientBalance) as error:
            await payment_service.charge_balance(
                session, uid, Decimal("3"), code=f"T-{uuid.uuid4().hex}", notes="test"
            )
        assert error.value.status_code == 402
        assert "余额不足" in error.value.detail

    created = await _create_order(client, headers, "10")
    await client.post(f"/api/v1/payment/orders/{created['order']['id']}/mock-pay", headers=headers)

    async with async_session_factory() as session:
        after = await payment_service.charge_balance(
            session, uid, Decimal("3"), code=f"T-{uuid.uuid4().hex}", notes="生成 3 页"
        )
        await session.commit()
        assert after == Decimal("7.00")
        rows = (
            await session.execute(
                select(BalanceLedger)
                .where(BalanceLedger.user_id == uid)
                .order_by(BalanceLedger.created_at)
            )
        ).scalars()
        types = [(row.type, row.amount) for row in rows]
        assert types == [("recharge", Decimal("10.00")), ("consume", Decimal("-3.00"))]

        # 只剩 7，扣 8 失败且余额不动
        with pytest.raises(payment_service.InsufficientBalance):
            await payment_service.charge_balance(
                session, uid, Decimal("8"), code=f"T-{uuid.uuid4().hex}", notes="x"
            )
        assert await payment_service.get_balance(session, uid) == Decimal("7.00")


@pytest.mark.asyncio
async def test_webhook_rejects_mock_and_unknown_provider(client: AsyncClient) -> None:
    # mock 通道不暴露公网回调；未知通道 404
    assert (await client.get("/api/v1/payment/webhook/mock?x=1")).status_code == 404
    assert (await client.post("/api/v1/payment/webhook/easypay", content="a=b")).status_code == 404


@pytest.mark.asyncio
async def test_return_redirects_to_result_page(client: AsyncClient) -> None:
    response = await client.get("/api/v1/payment/return/mock?out_trade_no=ppt1&x=<script>")
    assert response.status_code == 302
    assert response.headers["location"].endswith("/payment/result?out_trade_no=ppt1")


@pytest.mark.asyncio
async def test_refund_balance_is_idempotent(client: AsyncClient) -> None:
    headers, user_id = await _register(client)
    uid = uuid.UUID(user_id)
    created = await _create_order(client, headers, "10")
    await client.post(f"/api/v1/payment/orders/{created['order']['id']}/mock-pay", headers=headers)

    async with async_session_factory() as session:
        code = f"DECK-{uuid.uuid4().hex}"
        await payment_service.charge_balance(session, uid, Decimal("4"), code=code, notes="生成")
        await payment_service.refund_balance(
            session, uid, Decimal("4"), code=f"REFUND-{code}", notes="入队失败退回"
        )
        # 同一退款码再退一次：账本唯一约束挡住，余额不会多出 4 元
        await payment_service.refund_balance(
            session, uid, Decimal("4"), code=f"REFUND-{code}", notes="入队失败退回"
        )
        await session.commit()
        assert await payment_service.get_balance(session, uid) == Decimal("10.00")
        types = list(
            (
                await session.execute(
                    select(BalanceLedger.type)
                    .where(BalanceLedger.user_id == uid)
                    .order_by(BalanceLedger.created_at)
                )
            ).scalars()
        )
        assert types == ["recharge", "consume", "refund"]
