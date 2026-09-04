from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.paths import REPO_ROOT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    # 端口统一使用 39xxx 段，避开各服务默认端口，防止与本机已装的
    # PostgreSQL / Redis / 其他开发服务抢占端口
    database_url: str = "postgresql+asyncpg://aippt:aippt@localhost:39432/aippt"
    redis_url: str = "redis://localhost:39379/0"
    cors_origins: list[str] = ["http://localhost:39173"]
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    # 大纲默认关闭思考模式以满足首个结果 10 秒内返回的目标；
    # 遇到复杂主题时可通过环境变量开启，不把供应商参数写死在工作流里。
    llm_thinking_enabled: bool = False
    llm_timeout_seconds: float = 60
    # 单份 PPT 同时生成的页数。调高能缩短总时长，但容易触发供应商限流，
    # 且失败会成片出现；3 是延迟与稳定性之间比较稳妥的取值。
    slide_concurrency: int = 3
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    # 本项目不做 refresh token，access token 默认 7 天
    jwt_expire_minutes: int = 60 * 24 * 7

    # AI 生图与图库各自独立配置：两者是互相降级的关系，
    # 只配一个也要能正常工作，因此不共用 LLM 的凭证。
    # openai = OpenAI 兼容 /images/generations；bailian = 百炼 DashScope 原生接口
    image_provider: Literal["openai", "bailian"] = "openai"
    image_api_key: str = ""
    image_base_url: str = "https://api.openai.com/v1"
    image_model: str = "gpt-image-1"
    # 百炼业务空间 ID：填写后使用专属域名，覆盖 image_base_url
    image_workspace_id: str = ""
    unsplash_access_key: str = ""
    image_timeout_seconds: float = 60

    storage_driver: Literal["local", "cos"] = "local"
    storage_local_dir: str = str(REPO_ROOT / "backend" / "var" / "storage")
    cos_bucket: str = ""
    cos_region: str = ""
    cos_secret_id: str = ""
    cos_secret_key: str = ""

    # 上传体积上限。定得过大会让解析长时间占住请求线程
    max_upload_mb: int = 10
    max_image_mb: int = 8

    # ---- 钱包与支付（参考 sub2api：余额落在 users 表，充值走订单 + 幂等流水）----
    # 总开关关闭时充值接口返回 403，但余额查询仍可用，便于先上线钱包再开通道
    payment_enabled: bool = False
    # mock 只用于本地联调与测试：不调任何网关，用 /orders/{id}/mock-pay 模拟回调
    payment_provider: Literal["mock", "easypay"] = "mock"
    payment_currency: str = "CNY"
    # 对外可访问的站点根地址（含协议，不带末尾斜杠），用来拼网关的 notify / return 回跳
    payment_public_base_url: str = "http://localhost:39173"
    payment_min_amount: Decimal = Decimal("1")
    # 0 表示不限
    payment_max_amount: Decimal = Decimal("10000")
    payment_daily_limit: Decimal = Decimal("0")
    payment_order_timeout_minutes: int = 30
    payment_max_pending_orders: int = 3
    # 通道手续费率（百分比，向上取整到分）与到账倍率（充 100 送 10 即 1.1）
    payment_fee_rate: Decimal = Decimal("0")
    payment_recharge_multiplier: Decimal = Decimal("1")
    # 每生成一页扣多少余额；0 = 免费，不改变现有行为
    charge_per_page: Decimal = Decimal("0")

    # 易支付（EasyPay 协议）商户参数。api = mapi.php 服务端下单拿二维码；submit = 拼 submit.php 跳转
    easypay_pid: str = ""
    easypay_key: str = ""
    easypay_api_base: str = ""
    easypay_mode: Literal["api", "submit"] = "api"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_image_bytes(self) -> int:
        return self.max_image_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    # 缓存避免每个请求重复解析环境变量
    return Settings()
