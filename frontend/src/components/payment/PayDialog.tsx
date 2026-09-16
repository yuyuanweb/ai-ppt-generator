import { CheckCircle2, ExternalLink, Loader2, ScanLine } from 'lucide-react'
import QRCode from 'qrcode'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import {
  useMockPay,
  useOrder,
  usePaymentConfig,
  useSyncWalletOnSettle,
  useVerifyOrder,
} from '@/features/payment/api'
import type { CreateOrderResponse, PaymentOrder } from '@/features/payment/types'
import { PAYMENT_TYPE_LABEL, SUCCESS_STATUSES, TERMINAL_STATUSES } from '@/features/payment/types'
import { errorMessage } from '@/lib/errors'
import { formatMoney } from '@/lib/money'

const BRAND_RING: Record<string, string> = {
  alipay: 'ring-[#1677ff]/40',
  wxpay: 'ring-[#07c160]/40',
}

/**
 * 收银台弹层：二维码 / 跳转两种形态，3 秒轮询订单，倒计时到 0 视为过期。
 * 状态判定只认服务端订单状态，前端不自行推断「已支付」。
 */
export function PayDialog({
  created,
  onClose,
}: {
  created: CreateOrderResponse
  onClose: () => void
}) {
  const config = usePaymentConfig()
  const polled = useOrder(created.order.id)
  const order: PaymentOrder = polled.data ?? created.order
  const verify = useVerifyOrder()
  const mockPay = useMockPay()
  const syncWallet = useSyncWalletOnSettle()
  const settledRef = useRef(false)

  const success = SUCCESS_STATUSES.has(order.status)
  const terminal = TERMINAL_STATUSES.has(order.status)
  const remaining = useCountdown(order.expires_at, !terminal && !success)

  useEffect(() => {
    if (success && !settledRef.current) {
      settledRef.current = true
      syncWallet()
    }
  }, [success, syncWallet])

  const method = PAYMENT_TYPE_LABEL[order.payment_type as keyof typeof PAYMENT_TYPE_LABEL]
  const isMock = config.data?.provider === 'mock'

  return (
    <Dialog
      title={success ? '支付成功' : '扫码支付'}
      description={
        success
          ? `已到账 ¥${formatMoney(order.amount)}`
          : `订单 ${order.out_trade_no} · 实付 ¥${formatMoney(order.pay_amount)}`
      }
      onClose={onClose}
      className="max-w-md"
      footer={
        success || terminal ? (
          <Button onClick={onClose}>完成</Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose}>
              稍后支付
            </Button>
            <Button
              variant="soft"
              disabled={verify.isPending}
              onClick={() => verify.mutate(order.id)}
            >
              {verify.isPending ? '查询中…' : '我已完成支付'}
            </Button>
          </>
        )
      }
    >
      {success ? (
        <SuccessBody order={order} />
      ) : terminal ? (
        <TerminalBody order={order} />
      ) : (
        <div className="flex flex-col items-center gap-4 py-2">
          {created.payment_mode === 'qrcode' && created.qr_code ? (
            <QrCanvas value={created.qr_code} ring={BRAND_RING[order.payment_type] ?? ''} />
          ) : (
            <RedirectBody payUrl={created.pay_url} />
          )}

          <div className="flex items-center gap-2 text-sm text-ink-soft">
            {method && <span className={`size-2 rounded-full ${method.dot}`} />}
            <span>请使用{method?.label ?? '支付应用'}扫码</span>
            <span aria-hidden className="text-ink-muted">
              ·
            </span>
            <span className="tabular-nums text-ink-muted">{formatCountdown(remaining)} 后过期</span>
          </div>

          <p className="flex items-center gap-1.5 text-xs text-ink-muted">
            <Loader2 className="size-3.5 animate-spin" />
            支付完成后会自动到账，无需刷新
          </p>

          {isMock && (
            <Button
              size="sm"
              variant="soft"
              disabled={mockPay.isPending}
              onClick={() => mockPay.mutate(order.id)}
            >
              模拟支付成功（本地联调）
            </Button>
          )}

          {(verify.isError || mockPay.isError) && (
            <p role="alert" className="text-[13px] text-negative">
              {errorMessage(verify.error ?? mockPay.error, '查询失败，请稍后重试')}
            </p>
          )}
        </div>
      )}
    </Dialog>
  )
}

function QrCanvas({ value, ring }: { value: string; ring: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    QRCode.toCanvas(canvas, value, { width: 220, margin: 2, errorCorrectionLevel: 'M' }).catch(
      () => setFailed(true),
    )
  }, [value])

  return (
    <div className={`rounded-2xl bg-surface p-3 ring-2 ${ring} shadow-card`}>
      {failed ? (
        <p className="grid size-[220px] place-items-center text-center text-xs text-ink-muted">
          二维码生成失败
          <br />
          {value}
        </p>
      ) : (
        <canvas ref={canvasRef} className="block size-[220px] rounded-lg" />
      )}
    </div>
  )
}

function RedirectBody({ payUrl }: { payUrl: string | null }) {
  const open = () => {
    if (!payUrl) return
    const popup = window.open(payUrl, 'aippt-pay', 'width=1000,height=760')
    if (!popup) window.location.href = payUrl
  }
  return (
    <div className="flex flex-col items-center gap-3 py-4">
      <div className="grid size-16 place-items-center rounded-2xl bg-accent-soft text-accent">
        <ScanLine className="size-7" />
      </div>
      <p className="text-sm text-ink-soft">该渠道需要跳转到收银台完成支付</p>
      <Button onClick={open} disabled={!payUrl}>
        <ExternalLink className="size-4" />
        打开收银台
      </Button>
    </div>
  )
}

function SuccessBody({ order }: { order: PaymentOrder }) {
  return (
    <div className="flex flex-col items-center gap-3 py-6 text-center">
      <div className="grid size-16 place-items-center rounded-full bg-positive/10 text-positive">
        <CheckCircle2 className="size-8" />
      </div>
      <p className="text-3xl font-semibold tracking-tight tabular-nums">
        +¥{formatMoney(order.amount)}
      </p>
      <p className="text-sm text-ink-muted">
        {order.status === 'COMPLETED' ? '余额已更新' : '支付已确认，余额正在入账'}
      </p>
    </div>
  )
}

function TerminalBody({ order }: { order: PaymentOrder }) {
  const copy: Record<string, string> = {
    EXPIRED: '订单已过期，请重新发起充值',
    CANCELLED: '订单已取消',
    FAILED: order.failed_reason ?? '支付失败，请稍后重试',
  }
  return (
    <p className="rounded-xl bg-surface-soft px-4 py-6 text-center text-sm text-ink-soft">
      {copy[order.status] ?? '订单已结束'}
    </p>
  )
}

function useCountdown(expiresAt: string, active: boolean) {
  const [remaining, setRemaining] = useState(() => secondsUntil(expiresAt))
  useEffect(() => {
    if (!active) return
    setRemaining(secondsUntil(expiresAt))
    const timer = setInterval(() => setRemaining(secondsUntil(expiresAt)), 1000)
    return () => clearInterval(timer)
  }, [expiresAt, active])
  return remaining
}

function secondsUntil(iso: string): number {
  return Math.max(0, Math.floor((new Date(iso).getTime() - Date.now()) / 1000))
}

function formatCountdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
}
