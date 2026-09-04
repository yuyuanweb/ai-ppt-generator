import os
import tempfile

# 必须在导入任何读取配置的模块之前改环境变量：Settings 与 Storage 都是
# lru_cache 单例，一旦构造好就不会再看环境。否则测试会往真实的
# backend/var/storage 落文件。
_STORAGE_DIR = tempfile.mkdtemp(prefix="aippt-test-storage-")
os.environ["STORAGE_LOCAL_DIR"] = _STORAGE_DIR
# 支付用例走 mock 通道，且需要总开关打开；同样必须在 Settings 构造前设好
os.environ["PAYMENT_ENABLED"] = "true"
os.environ["PAYMENT_PROVIDER"] = "mock"
os.environ["PAYMENT_MAX_PENDING_ORDERS"] = "3"
os.environ["CHARGE_PER_PAGE"] = "0"

import pytest  # noqa: E402

from app.core.db import engine  # noqa: E402


@pytest.fixture(autouse=True)
async def _reset_async_engine() -> None:
    # 每个用例独立事件循环，用完后释放连接池，避免 asyncpg 绑到旧 loop
    yield
    await engine.dispose()
