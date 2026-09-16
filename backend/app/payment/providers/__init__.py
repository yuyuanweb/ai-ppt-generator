from functools import lru_cache

from app.core.config import get_settings
from app.payment.providers.base import PaymentProvider
from app.payment.providers.easypay import EasyPayProvider
from app.payment.providers.mock import MockProvider


@lru_cache
def get_provider() -> PaymentProvider:
    """按配置装配唯一的通道实例。缓存是为了让 mock 的内存状态在请求间保留。"""
    settings = get_settings()
    if settings.payment_provider == "easypay":
        return EasyPayProvider(
            pid=settings.easypay_pid,
            secret=settings.easypay_key,
            api_base=settings.easypay_api_base,
            mode=settings.easypay_mode,
        )
    return MockProvider()


__all__ = ["EasyPayProvider", "MockProvider", "PaymentProvider", "get_provider"]
