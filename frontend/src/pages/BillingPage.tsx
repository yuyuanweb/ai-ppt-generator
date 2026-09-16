import { Wallet } from 'lucide-react'
import { type FormEvent, useState } from 'react'
import { OrderStatusPill } from '@/components/payment/OrderStatusPill'
import { PayDialog } from '@/components/payment/PayDialog'
import { Button } from '@/components/ui/Button'
import {
  useCancelOrder,
  useCreateOrder,
  useLedger,
  useOrders,
  usePaymentConfig,
  useVerifyOrder,
  useWallet,
} from '@/features/payment/api'
import type { CreateOrderResponse, PaymentOrder, PaymentType } from '@/features/payment/types'
import { LEDGER_TYPE_LABEL, PAYMENT_TYPE_LABEL } from '@/features/payment/types'
import { relativeTime } from '@/lib/datetime'
import { errorMessage } from '@/lib/errors'
import { formatMoney, toAmount } from '@/lib/money'
import { cn } from '@/lib/utils'

export default function BillingPage() {
  const wallet = useWallet()
  const config = usePaymentConfig()
  const [paying, setPaying] = useState<CreateOrderResponse | null>(null)

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <div className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">余额与充值</h1>
        <p className="mt-1.5 text-sm text-ink-muted">
          {config.data && toAmount(config.data.charge_per_page) > 0
            ? `每生成一页扣 ¥${formatMoney(config.data.charge_per_page)}，重试失败页不重复扣费`
            : '当前生成免费，余额可留作后续付费功能使用'}
        </p>
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <BalanceCard
          balance={wallet.data?.balance}
          currency={wallet.data?.currency ?? config.data?.currency ?? 'CNY'}
          loading={wallet.isPending}
        />
        <RechargeCard onCreated={setPaying} />
      </div>

      <section className="mt-10">
        <h2 className="mb-3 text-[15px] font-semibold tracking-tight">充值订单</h2>
        <OrdersTable onContinue={setPaying} />
      </section>

      <section className="mt-10">
        <h2 className="mb-3 text-[15px] font-semibold tracking-tight">余额明细</h2>
        <LedgerList />
      </section>

      {paying && <PayDialog created={paying} onClose={() => setPaying(null)} />}
    </div>
  )
}

function BalanceCard({
  balance,
  currency,
  loading,
}: {
  balance: string | undefined
  currency: string
  loading: boolean
}) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-line bg-surface p-6 shadow-card">
      <div
        aria-hidden
        className="pointer-events-none absolute -top-16 -right-16 size-48 rounded-full bg-accent-soft/70 blur-2xl"
      />
      <div className="flex items-center gap-2 text-[13px] font-medium text-ink-muted">
        <Wallet className="size-4" />
        账户余额
      </div>
      <p className="mt-4 flex items-baseline gap-1.5 tabular-nums">
        <span className="text-lg text-ink-soft">¥</span>
        <span className="text-[44px] leading-none font-semibold tracking-tight">
          {loading ? '—' : formatMoney(balance)}
        </span>
        <span className="ml-1 text-xs text-ink-muted">{currency}</span>
      </p>
      <p className="mt-5 text-xs text-ink-muted">
        余额只用于本站内消费，暂不支持提现；每一笔变动都可在下方明细中核对。
      </p>
    </div>
  )
}

function RechargeCard({ onCreated }: { onCreated: (created: CreateOrderResponse) => void }) {
  const config = usePaymentConfig()
  const create = useCreateOrder()
  const [amount, setAmount] = useState('')
  const [paymentType, setPaymentType] = useState<PaymentType>('alipay')

  const presets = config.data?.preset_amounts ?? []
  const enabled = config.data?.enabled ?? false
  const numeric = toAmount(amount)
  const feeRate = toAmount(config.data?.fee_rate)
  const multiplier = toAmount(config.data?.recharge_multiplier) || 1
  const fee = feeRate > 0 ? Math.ceil(numeric * feeRate) / 100 : 0
  const credited = numeric * multiplier
  const min = toAmount(config.data?.min_amount)
  const max = toAmount(config.data?.max_amount)
  const valid = numeric >= min && (max <= 0 || numeric <= max)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!valid) return
    create.mutate(
      { amount: numeric.toFixed(2), payment_type: paymentType, is_mobile: false },
      { onSuccess: onCreated },
    )
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-2xl border border-line bg-surface p-6 shadow-card"
      noValidate
    >
      <div className="flex items-center justify-between">
        <h2 className="text-[15px] font-semibold tracking-tight">充值</h2>
        {!enabled && config.data && (
          <span className="rounded-full bg-surface-soft px-2.5 py-0.5 text-[11px] text-ink-muted">
            暂未开放
          </span>
        )}
      </div>

      <fieldset disabled={!enabled || create.isPending} className="mt-4 flex flex-col gap-5">
        <div>
          <p className="mb-2 text-[13px] font-medium text-ink-soft">金额</p>
          <div className="flex flex-wrap gap-2">
            {presets.map((preset) => {
              const active = toAmount(preset) === numeric
              return (
                <button
                  key={preset}
                  type="button"
                  onClick={() => setAmount(formatMoney(preset))}
                  className={cn(
                    'h-10 min-w-20 rounded-xl border px-4 text-sm font-medium tabular-nums transition-colors',
                    active
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line bg-surface text-ink-soft hover:border-line-strong hover:text-ink',
                  )}
                >
                  ¥{toAmount(preset)}
                </button>
              )
            })}
          </div>
          <div className="relative mt-3">
            <span className="pointer-events-none absolute inset-y-0 left-3.5 grid place-items-center text-sm text-ink-muted">
              ¥
            </span>
            <input
              inputMode="decimal"
              value={amount}
              onChange={(event) => setAmount(event.target.value.replace(/[^\d.]/g, ''))}
              placeholder={
                max > 0 ? `自定义金额，${min} ~ ${max}` : `自定义金额，不低于 ${min}`
              }
              aria-label="充值金额"
              className="h-11 w-full rounded-xl border border-line bg-surface pl-8 pr-3.5 text-[15px] tabular-nums placeholder:text-ink-muted/70 focus:border-accent focus:outline-none"
            />
          </div>
        </div>

        <div>
          <p className="mb-2 text-[13px] font-medium text-ink-soft">支付方式</p>
          <div className="grid grid-cols-2 gap-2">
            {(config.data?.payment_types ?? []).map((type) => {
              const meta = PAYMENT_TYPE_LABEL[type as PaymentType]
              if (!meta) return null
              const active = paymentType === type
              return (
                <button
                  key={type}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setPaymentType(type as PaymentType)}
                  className={cn(
                    'flex h-11 items-center gap-2.5 rounded-xl border px-4 text-sm font-medium transition-colors',
                    active
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line bg-surface text-ink-soft hover:border-line-strong hover:text-ink',
                  )}
                >
                  <span className={cn('size-2.5 rounded-full', meta.dot)} />
                  {meta.label}
                </button>
              )
            })}
          </div>
        </div>

        {numeric > 0 && (
          <dl className="grid grid-cols-2 gap-y-1 rounded-xl bg-surface-soft px-4 py-3 text-[13px] tabular-nums">
            <dt className="text-ink-muted">实付</dt>
            <dd className="text-right font-medium">¥{formatMoney(numeric + fee)}</dd>
            {fee > 0 && (
              <>
                <dt className="text-ink-muted">含通道手续费 {feeRate}%</dt>
                <dd className="text-right text-ink-muted">¥{formatMoney(fee)}</dd>
              </>
            )}
            <dt className="text-ink-muted">到账余额</dt>
            <dd className="text-right font-medium text-positive">¥{formatMoney(credited)}</dd>
          </dl>
        )}

        {create.isError && (
          <p role="alert" className="rounded-xl bg-negative/8 px-3.5 py-2.5 text-[13px] text-negative">
            {errorMessage(create.error, '下单失败，请稍后重试')}
          </p>
        )}

        <Button type="submit" size="lg" variant="accent" disabled={!valid} className="w-full">
          {create.isPending ? '正在创建订单…' : numeric > 0 ? `立即充值 ¥${formatMoney(numeric + fee)}` : '立即充值'}
        </Button>
      </fieldset>
    </form>
  )
}

function OrdersTable({ onContinue }: { onContinue: (created: CreateOrderResponse) => void }) {
  const orders = useOrders()
  const cancel = useCancelOrder()
  const verify = useVerifyOrder()

  if (orders.isPending) {
    return <p className="text-sm text-ink-muted">加载中…</p>
  }
  if (orders.isError) {
    return (
      <p role="alert" className="text-sm text-negative">
        {errorMessage(orders.error, '订单加载失败')}
      </p>
    )
  }
  if (!orders.data || orders.data.length === 0) {
    return (
      <p className="rounded-2xl border border-dashed border-line px-5 py-8 text-center text-sm text-ink-muted">
        还没有充值记录
      </p>
    )
  }

  const resume = (order: PaymentOrder) =>
    onContinue({
      order,
      payment_mode: order.qr_code ? 'qrcode' : 'redirect',
      pay_url: order.pay_url,
      qr_code: order.qr_code,
    })

  return (
    <div className="overflow-hidden rounded-2xl border border-line bg-surface">
      <table className="w-full text-sm">
        <thead className="bg-surface-soft text-left text-xs text-ink-muted">
          <tr>
            <th className="px-4 py-2.5 font-medium">订单号</th>
            <th className="px-4 py-2.5 font-medium">渠道</th>
            <th className="px-4 py-2.5 text-right font-medium">实付</th>
            <th className="px-4 py-2.5 text-right font-medium">到账</th>
            <th className="px-4 py-2.5 font-medium">状态</th>
            <th className="px-4 py-2.5 font-medium">时间</th>
            <th className="px-4 py-2.5" />
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {orders.data.map((order) => {
            const method = PAYMENT_TYPE_LABEL[order.payment_type as PaymentType]
            const busy =
              (cancel.isPending && cancel.variables === order.id) ||
              (verify.isPending && verify.variables === order.id)
            return (
              <tr key={order.id} className="tabular-nums">
                <td className="px-4 py-3 font-mono text-xs text-ink-soft">{order.out_trade_no}</td>
                <td className="px-4 py-3">
                  <span className="inline-flex items-center gap-1.5 text-ink-soft">
                    {method && <span className={cn('size-2 rounded-full', method.dot)} />}
                    {method?.label ?? order.payment_type}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">¥{formatMoney(order.pay_amount)}</td>
                <td className="px-4 py-3 text-right">¥{formatMoney(order.amount)}</td>
                <td className="px-4 py-3">
                  <OrderStatusPill status={order.status} />
                </td>
                <td className="px-4 py-3 text-ink-muted">{relativeTime(order.created_at)}</td>
                <td className="px-4 py-3 text-right whitespace-nowrap">
                  {order.status === 'PENDING' && (
                    <span className="inline-flex gap-1">
                      <Button size="sm" variant="soft" disabled={busy} onClick={() => resume(order)}>
                        继续支付
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        onClick={() => cancel.mutate(order.id)}
                      >
                        取消
                      </Button>
                    </span>
                  )}
                  {order.status === 'EXPIRED' && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => verify.mutate(order.id)}
                    >
                      查单
                    </Button>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {(cancel.isError || verify.isError) && (
        <p role="alert" className="border-t border-line px-4 py-2.5 text-[13px] text-negative">
          {errorMessage(cancel.error ?? verify.error, '操作失败，请稍后重试')}
        </p>
      )}
    </div>
  )
}

function LedgerList() {
  const ledger = useLedger()
  if (ledger.isPending) return <p className="text-sm text-ink-muted">加载中…</p>
  if (ledger.isError) {
    return (
      <p role="alert" className="text-sm text-negative">
        {errorMessage(ledger.error, '明细加载失败')}
      </p>
    )
  }
  if (!ledger.data || ledger.data.length === 0) {
    return (
      <p className="rounded-2xl border border-dashed border-line px-5 py-8 text-center text-sm text-ink-muted">
        还没有余额变动
      </p>
    )
  }
  return (
    <ul className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface">
      {ledger.data.map((entry) => {
        const delta = toAmount(entry.amount)
        return (
          <li key={entry.id} className="flex items-center gap-4 px-4 py-3 text-sm tabular-nums">
            <span className="w-12 shrink-0 rounded-full bg-surface-soft px-2 py-0.5 text-center text-[11px] font-medium text-ink-soft">
              {LEDGER_TYPE_LABEL[entry.type] ?? entry.type}
            </span>
            <span className="min-w-0 flex-1 truncate text-ink-soft">{entry.notes ?? entry.code}</span>
            <span className="text-xs text-ink-muted">{relativeTime(entry.created_at)}</span>
            <span className={cn('w-24 text-right font-medium', delta >= 0 ? 'text-positive' : 'text-ink')}>
              {delta >= 0 ? '+' : '−'}¥{formatMoney(Math.abs(delta))}
            </span>
            <span className="w-28 text-right text-xs text-ink-muted">余 ¥{formatMoney(entry.balance_after)}</span>
          </li>
        )
      })}
    </ul>
  )
}
