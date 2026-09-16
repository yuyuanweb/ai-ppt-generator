import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { request } from '@/api/client'
import type {
  CreateOrderRequest,
  CreateOrderResponse,
  LedgerEntry,
  PaymentConfig,
  PaymentOrder,
  Wallet,
} from '@/features/payment/types'
import { TERMINAL_STATUSES } from '@/features/payment/types'

export const walletKey = ['payment', 'wallet'] as const
const configKey = ['payment', 'config'] as const
const ordersKey = ['payment', 'orders'] as const
const ledgerKey = ['payment', 'ledger'] as const
const orderKey = (id: string) => ['payment', 'orders', id] as const
const orderByTradeNoKey = (no: string) => ['payment', 'orders', 'by-trade-no', no] as const

/** 与 sub2api 一致：状态页每 3 秒读一次库，终态即停 */
const POLL_INTERVAL = 3000

export function usePaymentConfig() {
  return useQuery({
    queryKey: configKey,
    queryFn: () => request<PaymentConfig>('/payment/config'),
    staleTime: 5 * 60 * 1000,
  })
}

export function useWallet() {
  return useQuery({
    queryKey: walletKey,
    queryFn: () => request<Wallet>('/payment/wallet'),
  })
}

export function useOrders() {
  return useQuery({
    queryKey: ordersKey,
    queryFn: () => request<PaymentOrder[]>('/payment/orders'),
  })
}

export function useLedger() {
  return useQuery({
    queryKey: ledgerKey,
    queryFn: () => request<LedgerEntry[]>('/payment/ledger'),
  })
}

function pollWhilePending(data: PaymentOrder | undefined) {
  return data && !TERMINAL_STATUSES.has(data.status) ? POLL_INTERVAL : false
}

export function useOrder(id: string | null) {
  return useQuery({
    queryKey: orderKey(id ?? ''),
    queryFn: () => request<PaymentOrder>(`/payment/orders/${id}`),
    enabled: id !== null,
    refetchInterval: (query) => pollWhilePending(query.state.data),
  })
}

export function useOrderByTradeNo(outTradeNo: string) {
  return useQuery({
    queryKey: orderByTradeNoKey(outTradeNo),
    queryFn: () => request<PaymentOrder>(`/payment/orders/by-trade-no/${outTradeNo}`),
    enabled: outTradeNo.length > 0,
    refetchInterval: (query) => pollWhilePending(query.state.data),
  })
}

/** 订单到终态后钱包、流水、列表都可能变了，一起刷 */
function useInvalidateWallet() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: walletKey })
    void queryClient.invalidateQueries({ queryKey: ordersKey })
    void queryClient.invalidateQueries({ queryKey: ledgerKey })
  }
}

export function useCreateOrder() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateOrderRequest) =>
      request<CreateOrderResponse>('/payment/orders', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: (created) => {
      queryClient.setQueryData(orderKey(created.order.id), created.order)
      void queryClient.invalidateQueries({ queryKey: ordersKey })
    },
  })
}

function useOrderAction(action: 'cancel' | 'verify' | 'mock-pay') {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateWallet()
  return useMutation({
    mutationFn: (id: string) =>
      request<PaymentOrder>(`/payment/orders/${id}/${action}`, { method: 'POST' }),
    onSuccess: (order) => {
      queryClient.setQueryData(orderKey(order.id), order)
      queryClient.setQueryData(orderByTradeNoKey(order.out_trade_no), order)
      invalidate()
    },
  })
}

export function useCancelOrder() {
  return useOrderAction('cancel')
}

export function useVerifyOrder() {
  return useOrderAction('verify')
}

export function useMockPay() {
  return useOrderAction('mock-pay')
}

export function useSyncWalletOnSettle() {
  return useInvalidateWallet()
}
