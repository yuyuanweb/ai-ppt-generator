from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.worker.context import shutdown, startup
from app.worker.deck_tasks import generate_deck
from app.worker.payment_tasks import expire_payment_orders
from app.worker.retry import MAX_TRIES
from app.worker.tasks import generate_outline


class WorkerSettings:
    functions = [generate_outline, generate_deck]
    # 充值订单过期兜底：每分钟一次；unique 保证多 worker 只跑一份
    cron_jobs = [cron(expire_payment_orders, second=15, timeout=120)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 3
    # 整份生成在任务内部并发，耗时随页数增长，超时需要比大纲宽松得多
    job_timeout = 15 * 60
    max_tries = MAX_TRIES
    allow_abort_jobs = True
