import { LogOut, Plus, Wallet } from 'lucide-react'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { BrandMark } from '@/components/BrandMark'
import { Button } from '@/components/ui/Button'
import { useAuthStore } from '@/features/auth/store'
import { useWallet } from '@/features/payment/api'
import { formatMoney } from '@/lib/money'

/** 工作区外壳：仅用于列表与创作页；编辑工作台自带全屏 chrome，不套这层。 */
export function AppShell({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const location = useLocation()
  const onCreate = location.pathname === '/create'

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 border-b border-line bg-surface/80 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-6">
          <Link to="/projects" className="flex items-center gap-2.5">
            <BrandMark className="size-7" />
            <span className="text-[15px] font-semibold tracking-tight">AI PPT</span>
          </Link>

          <div className="flex items-center gap-2">
            <BalancePill />
            {!onCreate && (
              <Button size="sm" onClick={() => navigate('/create')}>
                <Plus className="size-4" />
                新建 PPT
              </Button>
            )}
            <UserMenu />
          </div>
        </div>
      </header>

      <main className="flex-1">{children}</main>
    </div>
  )
}

/** 余额入口常驻头部：付费能力对用户可见，而不是藏在菜单里 */
function BalancePill() {
  const wallet = useWallet()
  return (
    <Link
      to="/billing"
      aria-label="余额与充值"
      className="hidden h-8 items-center gap-1.5 rounded-full border border-line bg-surface px-3 text-[13px] font-medium text-ink-soft tabular-nums transition-colors hover:border-line-strong hover:text-ink sm:inline-flex"
    >
      <Wallet className="size-3.5 text-accent" />
      {wallet.data ? `¥${formatMoney(wallet.data.balance)}` : '余额'}
    </Link>
  )
}

function UserMenu() {
  const user = useAuthStore((state) => state.user)
  const logout = useAuthStore((state) => state.logout)
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointer = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onPointer)
    return () => document.removeEventListener('mousedown', onPointer)
  }, [open])

  const initial = user?.email?.[0]?.toUpperCase() ?? '?'

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="账号菜单"
        onClick={() => setOpen((value) => !value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setOpen(false)
        }}
        className="grid size-8 place-items-center rounded-full bg-surface-soft text-[13px] font-semibold text-ink-soft ring-1 ring-line transition-colors hover:ring-line-strong"
      >
        {initial}
      </button>

      {open && (
        <div
          role="menu"
          className="absolute top-full right-0 z-40 mt-2 w-56 overflow-hidden rounded-2xl border border-line bg-surface shadow-pop"
        >
          <p className="truncate border-b border-line px-4 py-3 text-xs text-ink-muted">
            {user?.email}
          </p>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false)
              navigate('/billing')
            }}
            className="flex w-full items-center gap-2 px-4 py-3 text-sm text-ink-soft transition-colors hover:bg-surface-soft hover:text-ink"
          >
            <Wallet className="size-4" />
            余额与充值
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={logout}
            className="flex w-full items-center gap-2 px-4 py-3 text-sm text-ink-soft transition-colors hover:bg-surface-soft hover:text-ink"
          >
            <LogOut className="size-4" />
            退出登录
          </button>
        </div>
      )}
    </div>
  )
}
