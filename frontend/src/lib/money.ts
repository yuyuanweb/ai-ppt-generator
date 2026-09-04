/** 后端金额是 Decimal，OpenAPI 里落成 string；展示前统一转数字并保留两位小数 */
export function toAmount(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return 0
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

export function formatMoney(value: string | number | null | undefined): string {
  return toAmount(value).toFixed(2)
}
