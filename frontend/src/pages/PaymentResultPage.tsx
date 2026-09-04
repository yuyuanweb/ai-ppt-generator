import { CheckCircle2, Clock, Loader2, XCircle } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { Link, useSearchParams } from 'react-router'
import { Button } from '@/components/ui/Button'
import { useOrderByTradeNo, useSyncWalletOnSettle, useVerifyOrder } from '@/features/payment/api'
import { SUCCESS_STATUSES } from '@/features/payment/types'
import { errorMessage } from '@/lib/errors'
import { formatMoney } from '@/lib/money'

/**
 * 网关同步回跳落地页。回跳只说明用户从收银台回来了，不代表已付款，
 * 所以这里先主动向网关查一次单，再靠轮询等异步通知把状态推到终态。
 */
export default function PaymentResultPage() {
  const [params] = useSearchParams()
  const outTradeNo = params.get('out_trade_no') ?? ''
  const order = useOrderByTradeNo(outTradeNo)
  const verify = useVerifyOrder()
  const syncWallet = useSyncWalletOnSettle()
  const verifiedRef = useRef(false)
  const settledRef = useRef(false)

  useEffect(() => {
    if (order.data && order.data.status === 'PENDING' && !verifiedRef.current) {
      verifiedRef.current = true
      verify.mutate(order.data.id)
    }
  }, [order.data, verify])

  const success = order.data ? SUCCESS_STATUSES.has(order.data.status) : false
  useEffect(() => {
    if (success && !settledRef.current) {
      settledRef.current = true
      syncWallet()
    }
  }, [success, syncWallet])

  return (
    <div className="mx-auto max-w-md px-6 py-16">
      <div className="rounded-3xl border border-line bg-surface p-8 text-center shadow-card">
        {!outTradeNo || order.isError ? (
          <>
            <XCircle className="mx-auto size-12 text-negative" />
            <h1 className="mt-4 text-xl font-semibold tracking-tight">找不到这笔订单</h1>
            <p className="mt-2 text-sm text-ink-muted">
              {order.isError ? errorMessage(order.error, '订单查询失败') : '链接缺少订单号'}
            </p>
          </>
        ) : order.isPending || !order.data ? (
          <>
            <Loader2 className="mx-auto size-12 animate-spin text-accent" />
            <h1 className="mt-4 text-xl font-semibold tracking-tight">正在确认支付结果…</h1>
          </>
        ) : success ? (
          <>
            <CheckCircle2 className="mx-auto size-12 text-positive" />
            <h1 className="mt-4 text-xl font-semibold tracking-tight">充值成功</h1>
            <p className="mt-3 text-3xl font-semibold tracking-tight tabular-nums">
              +¥{formatMoney(order.data.amount)}
            </p>
            <p className="mt-2 text-sm text-ink-muted">
              {order.data.status === 'COMPLETED' ? '余额已更新' : '支付已确认，余额正在入账'}
            </p>
          </>
        ) : order.data.status === 'PENDING' ? (
          <>
            <Clock className="mx-auto size-12 text-warning" />
            <h1 className="mt-4 text-xl font-semibold tracking-tight">等待支付结果</h1>
            <p className="mt-2 text-sm text-ink-muted">
              如已完成付款，通常几秒内到账；页面会自动刷新。
            </p>
          </>
        ) : (
          <>
            <XCircle className="mx-auto size-12 text-ink-muted" />
            <h1 className="mt-4 text-xl font-semibold tracking-tight">订单未完成</h1>
            <p className="mt-2 text-sm text-ink-muted">
              {order.data.status === 'EXPIRED'
                ? '订单已过期，如已扣款请联系我们核实'
                : order.data.failed_reason ?? '订单已关闭'}
            </p>
          </>
        )}

        <div className="mt-8 flex justify-center gap-3">
          <Link to="/billing">
            <Button variant="ghost">返回余额页</Button>
          </Link>
          <Link to="/projects">
            <Button>去创作</Button>
          </Link>
        </div>
      </div>
    </div>
  )
}
