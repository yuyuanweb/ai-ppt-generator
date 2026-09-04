import { useEffect } from 'react'
import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router'
import { AppShell } from '@/components/AppShell'
import { useAuthStore } from '@/features/auth/store'
import AuthPage from '@/pages/AuthPage'
import BillingPage from '@/pages/BillingPage'
import CreatePage from '@/pages/CreatePage'
import PaymentResultPage from '@/pages/PaymentResultPage'
import ProjectDetailPage from '@/pages/ProjectDetailPage'
import ProjectsPage from '@/pages/ProjectsPage'
import { RequireAuth } from '@/routes/RequireAuth'

export default function App() {
  const restore = useAuthStore((state) => state.restore)

  useEffect(() => {
    void restore()
  }, [restore])

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<AuthPage mode="login" />} />
        <Route path="/register" element={<AuthPage mode="register" />} />

        <Route
          element={
            <RequireAuth>
              <AppShell>
                <Outlet />
              </AppShell>
            </RequireAuth>
          }
        >
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/create" element={<CreatePage />} />
          <Route path="/billing" element={<BillingPage />} />
          {/* 网关同步回跳落地页：只读订单，状态由异步通知推进 */}
          <Route path="/payment/result" element={<PaymentResultPage />} />
        </Route>

        {/* 大纲与编辑工作台自带全屏 chrome，不进工作区外壳 */}
        <Route
          element={
            <RequireAuth>
              <Outlet />
            </RequireAuth>
          }
        >
          <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
        </Route>

        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
