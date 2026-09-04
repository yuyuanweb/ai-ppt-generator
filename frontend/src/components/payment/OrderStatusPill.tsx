import { ORDER_STATUS } from '@/features/payment/types'
import { cn } from '@/lib/utils'

export function OrderStatusPill({ status, className }: { status: string; className?: string }) {
  const meta = ORDER_STATUS[status] ?? { label: status, className: 'bg-surface-soft text-ink-muted' }
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium',
        meta.className,
        className,
      )}
    >
      {meta.pulse && <span className="size-1.5 animate-pulse rounded-full bg-current" />}
      {meta.label}
    </span>
  )
}
