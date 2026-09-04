import {
  ArrowRight,
  Eye,
  EyeOff,
  FileText,
  Layers,
  Lock,
  ShieldCheck,
  Sparkles,
  User,
} from 'lucide-react'
import { type FormEvent, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'
import { BrandMark } from '@/components/BrandMark'
import { Button } from '@/components/ui/Button'
import { TextField } from '@/components/ui/TextField'
import { login, register } from '@/features/auth/api'
import { useAuthStore } from '@/features/auth/store'
import { errorMessage } from '@/lib/errors'
import { cn } from '@/lib/utils'

type Mode = 'login' | 'register'

const REMEMBER_KEY = 'aippt.rememberedEmail'

const COPY: Record<
  Mode,
  { title: string; subtitle: string; submit: string; switchHint: string; switchTo: string; to: string }
> = {
  login: {
    title: '欢迎回来',
    subtitle: '请登录以进入你的 PPT 工作台',
    submit: '登录',
    switchHint: '还没有账号？',
    switchTo: '注册一个',
    to: '/register',
  },
  register: {
    title: '创建账号',
    subtitle: '一个邮箱即可开始，注册后自动登录',
    submit: '注册并开始',
    switchHint: '已经有账号了？',
    switchTo: '去登录',
    to: '/login',
  },
}

function readRemembered(): string {
  try {
    return localStorage.getItem(REMEMBER_KEY) ?? ''
  } catch {
    return ''
  }
}

export default function AuthPage({ mode }: { mode: Mode }) {
  const [remembered] = useState(readRemembered)
  const [email, setEmail] = useState(remembered)
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [remember, setRemember] = useState(remembered !== '')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const user = useAuthStore((state) => state.user)
  const restoring = useAuthStore((state) => state.restoring)
  const applySession = useAuthStore((state) => state.applySession)
  const navigate = useNavigate()
  const location = useLocation()

  // 有本地 token 时先等 restore，避免已登录用户闪一下登录表单
  if (restoring) {
    return (
      <div className="grid min-h-screen place-items-center text-sm text-ink-muted">
        正在恢复登录状态…
      </div>
    )
  }

  if (user) {
    return <Navigate to="/projects" replace />
  }

  const copy = COPY[mode]

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (mode === 'register' && password !== confirm) {
      setError('两次输入的密码不一致')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const session = await (mode === 'login' ? login : register)({ email, password })
      try {
        if (remember) localStorage.setItem(REMEMBER_KEY, email.trim().toLowerCase())
        else localStorage.removeItem(REMEMBER_KEY)
      } catch {
        // 隐私模式下 localStorage 可能不可用，记住账号是锦上添花，不影响登录
      }
      applySession(session)
      const from = (location.state as { from?: string } | null)?.from ?? '/projects'
      navigate(from, { replace: true })
    } catch (cause) {
      setError(errorMessage(cause instanceof Error ? cause : null, '网络异常，请稍后重试'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="bg-aurora relative min-h-screen overflow-hidden">
      <div aria-hidden className="bg-grid-faint pointer-events-none absolute inset-0" />

      <div className="relative mx-auto grid min-h-screen max-w-7xl items-center gap-12 px-6 py-12 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] lg:px-12">
        <ShowcasePanel />

        <section className="mx-auto w-full max-w-md lg:ml-auto">
          <div className="rounded-3xl border border-white/70 bg-surface/80 p-8 shadow-pop backdrop-blur-xl sm:p-10">
            <div className="grid size-14 place-items-center rounded-2xl bg-accent-soft text-accent shadow-card">
              <ShieldCheck className="size-7" />
            </div>
            <h1 className="mt-6 text-[28px] font-semibold tracking-tight">{copy.title}</h1>
            <p className="mt-1.5 text-sm text-ink-muted">{copy.subtitle}</p>

            <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-5" noValidate>
              <TextField
                label="账号"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="请输入邮箱"
                autoComplete="email"
                leading={<User className="size-4" />}
                required
              />
              <TextField
                label="密码"
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder={mode === 'register' ? '至少 8 位' : '请输入密码'}
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                minLength={8}
                leading={<Lock className="size-4" />}
                trailing={
                  <button
                    type="button"
                    aria-label={showPassword ? '隐藏密码' : '显示密码'}
                    aria-pressed={showPassword}
                    onClick={() => setShowPassword((value) => !value)}
                    className="grid size-8 place-items-center rounded-lg text-ink-muted transition-colors hover:bg-surface-soft hover:text-ink"
                  >
                    {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                  </button>
                }
                required
              />
              {mode === 'register' && (
                <TextField
                  label="确认密码"
                  type={showPassword ? 'text' : 'password'}
                  value={confirm}
                  onChange={(event) => setConfirm(event.target.value)}
                  placeholder="再输入一次"
                  autoComplete="new-password"
                  minLength={8}
                  leading={<Lock className="size-4" />}
                  required
                />
              )}

              {mode === 'login' && (
                <label className="flex w-fit cursor-pointer items-center gap-2.5 text-sm text-ink-soft select-none">
                  <input
                    type="checkbox"
                    checked={remember}
                    onChange={(event) => setRemember(event.target.checked)}
                    className="size-4 rounded border-line-strong accent-accent"
                  />
                  记住账号
                </label>
              )}

              {error && (
                <p role="alert" className="rounded-xl bg-negative/8 px-3.5 py-2.5 text-[13px] text-negative">
                  {error}
                </p>
              )}

              <Button
                type="submit"
                size="lg"
                variant="accent"
                disabled={submitting}
                className="mt-1 w-full bg-gradient-to-r from-accent to-[#3b7cff] shadow-[0_12px_30px_-12px_rgba(47,75,255,0.7)]"
              >
                {submitting ? '处理中…' : copy.submit}
              </Button>
            </form>

            <p className="mt-6 text-center text-sm text-ink-muted">
              {copy.switchHint}
              <Link
                to={copy.to}
                replace
                state={location.state}
                className="ml-1 font-medium text-accent underline-offset-4 transition-colors hover:underline"
              >
                {copy.switchTo}
              </Link>
            </p>

            <p className="mt-8 flex items-center justify-center gap-1.5 border-t border-line pt-5 text-xs text-ink-muted">
              <Lock className="size-3.5 text-positive" />
              连接已加密 · 会话受签名保护
            </p>
          </div>
        </section>
      </div>
    </div>
  )
}

/** 左侧品牌展示区：桌面端才显示，移动端只留表单 */
function ShowcasePanel() {
  return (
    <section className="hidden lg:block">
      <div className="flex items-center gap-3">
        <BrandMark className="size-8 shadow-card" />
        <span className="text-[17px] font-semibold tracking-tight">AI PPT</span>
        <span aria-hidden className="h-4 w-px bg-line-strong" />
        <span className="text-[11px] font-semibold tracking-[0.22em] text-ink-muted uppercase">
          AI / Presentation
        </span>
      </div>

      <div className="mt-14 grid gap-12 xl:grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)]">
        <div>
          <span className="inline-flex items-center gap-2 rounded-full border border-accent/20 bg-accent-soft/70 px-3.5 py-1.5 text-[11px] font-semibold tracking-[0.18em] text-accent uppercase">
            <span className="size-1.5 rounded-full bg-positive" />
            AI Powered · 一句话到整份 PPT
          </span>
          <h2 className="mt-8 text-[52px] leading-[1.08] font-semibold tracking-tight">
            把一段想法
            <br />
            <span className="text-accent">变成可编辑的演示</span>
          </h2>
          <p className="mt-6 max-w-md text-[15px] leading-7 text-ink-soft">
            输入主题、粘贴长文或上传文档，先生成可修改的大纲，再并发生成每一页，
            最终导出原生可编辑的 16:9 PPTX。
          </p>

          <div className="mt-10 flex flex-wrap gap-2">
            {['主题', '长文本', 'PDF', 'Word', 'Markdown'].map((chip) => (
              <span
                key={chip}
                className="rounded-full border border-line bg-surface/80 px-3.5 py-1.5 text-xs font-medium text-ink-soft"
              >
                {chip}
              </span>
            ))}
          </div>

          <p className="mt-16 text-xs leading-6 text-ink-muted">
            © 2026 AI PPT · 内部系统，仅限授权人员访问
            <br />
            Powered by FastAPI · LangGraph · React
          </p>
        </div>

        <div className="flex flex-col gap-4 self-start">
          <LiveCard />
          <div className="grid grid-cols-3 gap-4">
            <StatCard value="16:9" label="固定画幅" tone="text-accent" />
            <StatCard value="3×" label="页级并发" tone="text-positive" />
            <StatCard value="PPTX" label="原生可编辑" tone="text-warning" />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <StepCard icon={<FileText className="size-4" />} label="大纲" />
            <StepCard icon={<Layers className="size-4" />} label="生成" />
            <StepCard icon={<Sparkles className="size-4" />} label="导出" />
          </div>
        </div>
      </div>
    </section>
  )
}

function LiveCard() {
  return (
    <div className="rounded-2xl border border-white/70 bg-surface/80 p-5 shadow-card backdrop-blur">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-sm font-medium text-ink-soft">
          <span className="size-2 rounded-full bg-accent" />
          实时生成流
        </span>
        <span className="rounded-full bg-positive/10 px-2.5 py-0.5 text-[11px] font-semibold tracking-wider text-positive">
          LIVE
        </span>
      </div>
      <svg viewBox="0 0 320 72" className="mt-4 h-[72px] w-full" aria-hidden>
        <defs>
          <linearGradient id="auth-spark" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#2f4bff" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#2f4bff" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path
          d="M0 50 C 30 44, 50 20, 80 24 S 130 58, 160 46 S 210 14, 240 26 S 290 46, 320 22 V 72 H 0 Z"
          fill="url(#auth-spark)"
        />
        <path
          d="M0 50 C 30 44, 50 20, 80 24 S 130 58, 160 46 S 210 14, 240 26 S 290 46, 320 22"
          fill="none"
          stroke="#2f4bff"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
      </svg>
    </div>
  )
}

function StatCard({ value, label, tone }: { value: string; label: string; tone: string }) {
  return (
    <div className="rounded-2xl border border-white/70 bg-surface/80 px-4 py-4 shadow-card backdrop-blur">
      <p className={cn('text-[24px] leading-none font-semibold tracking-tight tabular-nums', tone)}>
        {value}
      </p>
      <p className="mt-2 text-[11px] whitespace-nowrap text-ink-muted">{label}</p>
    </div>
  )
}

function StepCard({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="flex items-center justify-center gap-2 rounded-full border border-line bg-surface/80 py-2 text-xs font-medium text-ink-soft">
      <span className="text-accent">{icon}</span>
      {label}
      <ArrowRight className="size-3 text-ink-muted" />
    </div>
  )
}
