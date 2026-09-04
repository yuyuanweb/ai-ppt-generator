"""过期订单巡检：ARQ cron 每分钟一次。

ARQ 的 cron 默认 unique=True，多 worker 也只会跑一份，
不需要像 sub2api 那样再搞一把分布式 leader 锁。读订单时还有懒过期兜底。
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.services.payment import expire_timed_out_orders

logger = logging.getLogger(__name__)


async def expire_payment_orders(ctx: dict[str, Any]) -> int:
    if not get_settings().payment_enabled:
        return 0
    async with async_session_factory() as session:
        count = await expire_timed_out_orders(session, check_upstream=True)
    if count:
        logger.info("已过期 %d 笔超时未支付订单", count)
    return count
