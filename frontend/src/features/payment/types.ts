import type { components } from '@/api/schema'

type Schemas = components['schemas']

export type PaymentConfig = Schemas['PaymentConfigPublic']
export type Wallet = Schemas['WalletPublic']
export type PaymentOrder = Schemas['PaymentOrderPublic']
export type CreateOrderRequest = Schemas['CreateOrderRequest']
export type CreateOrderResponse = Schemas['CreateOrderResponse']
export type LedgerEntry = Schemas['LedgerEntryPublic']

export type PaymentType = CreateOrderRequest['payment_type']

export const PAYMENT_TYPE_LABEL: Record<PaymentType, { label: string; dot: string }> = {
  alipay: { label: '支付宝', dot: 'bg-[#1677ff]' },
  wxpay: { label: '微信支付', dot: 'bg-[#07c160]' },
}

/** 状态一律用用户语言表达，不暴露后端枚举 */
export const ORDER_STATUS: Record<string, { label: string; className: string; pulse?: boolean }> = {
  PENDING: { label: '待支付', className: 'bg-warning/10 text-warning', pulse: true },
  PAID: { label: '已支付', className: 'bg-accent-soft text-accent', pulse: true },
  RECHARGING: { label: '到账中', className: 'bg-accent-soft text-accent', pulse: true },
  COMPLETED: { label: '已到账', className: 'bg-positive/10 text-positive' },
  EXPIRED: { label: '已过期', className: 'bg-surface-soft text-ink-muted' },
  CANCELLED: { label: '已取消', className: 'bg-surface-soft text-ink-muted' },
  FAILED: { label: '失败', className: 'bg-negative/10 text-negative' },
}

export const LEDGER_TYPE_LABEL: Record<string, string> = {
  recharge: '充值',
  consume: '消费',
  refund: '退回',
  adjust: '调整',
}

export const TERMINAL_STATUSES = new Set(['COMPLETED', 'EXPIRED', 'CANCELLED', 'FAILED'])
export const SUCCESS_STATUSES = new Set(['COMPLETED', 'PAID', 'RECHARGING'])
