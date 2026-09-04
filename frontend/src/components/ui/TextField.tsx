import { type InputHTMLAttributes, type ReactNode, useId } from 'react'
import { cn } from '@/lib/utils'

interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string
  hint?: string
  /** 输入框内左侧图标；给了就自动让出内边距 */
  leading?: ReactNode
  /** 输入框内右侧插槽（如密码可见切换按钮） */
  trailing?: ReactNode
}

export function TextField({
  label,
  hint,
  leading,
  trailing,
  className,
  id,
  ...props
}: TextFieldProps) {
  const generatedId = useId()
  const inputId = id ?? generatedId
  const hintId = hint ? `${inputId}-hint` : undefined

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={inputId} className="text-[13px] font-medium text-ink-soft">
        {label}
      </label>
      <div className="relative">
        {leading && (
          <span className="pointer-events-none absolute inset-y-0 left-3.5 grid place-items-center text-ink-muted">
            {leading}
          </span>
        )}
        <input
          {...props}
          id={inputId}
          aria-describedby={hintId}
          className={cn(
            'h-11 w-full rounded-xl border border-line bg-surface px-3.5 text-[15px] text-ink',
            'placeholder:text-ink-muted/70 focus:border-accent focus:outline-none',
            'transition-colors duration-150 disabled:bg-surface-soft disabled:text-ink-muted',
            leading && 'pl-10',
            trailing && 'pr-11',
            className,
          )}
        />
        {trailing && (
          <span className="absolute inset-y-0 right-2 grid place-items-center">{trailing}</span>
        )}
      </div>
      {hint && (
        <span id={hintId} className="text-xs text-ink-muted">
          {hint}
        </span>
      )}
    </div>
  )
}
