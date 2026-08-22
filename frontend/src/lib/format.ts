import { formatDistanceToNowStrict } from 'date-fns'

export function shortAddress(address: string, head = 8, tail = 6): string {
  if (!address) return ''
  if (address.length <= head + tail + 3) return address
  return `${address.slice(0, head)}...${address.slice(-tail)}`
}

export function shortHash(hash: string): string {
  return shortAddress(hash, 10, 8)
}

const usdFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
})

export function formatUsd(value: number | null | undefined): string {
  if (value == null) return '--'
  if (Math.abs(value) >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`
  if (Math.abs(value) >= 1_000) return `$${(value / 1_000).toFixed(1)}K`
  return usdFormatter.format(value)
}

const inrFormatter = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
})

export function formatInr(value: number | null | undefined): string {
  if (value == null) return '--'
  if (value >= 10_000_000) return `₹${(value / 10_000_000).toFixed(2)} Cr`
  if (value >= 100_000) return `₹${(value / 100_000).toFixed(2)} L`
  return inrFormatter.format(value)
}

export function formatAmount(amount: string | number | null | undefined, asset = ''): string {
  if (amount == null) return '--'
  const num = typeof amount === 'string' ? Number.parseFloat(amount) : amount
  if (Number.isNaN(num)) return String(amount)
  const digits = num >= 1000 ? 2 : num >= 1 ? 4 : 8
  const text = num.toLocaleString('en-US', { maximumFractionDigits: digits })
  return asset ? `${text} ${asset}` : text
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value == null) return '--'
  return `${(value * 100).toFixed(digits)}%`
}

export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return '--'
  const date = typeof value === 'string' ? new Date(value) : value
  if (Number.isNaN(date.getTime())) return '--'
  return date.toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

export function timeAgo(value: string | Date | null | undefined): string {
  if (!value) return '--'
  const date = typeof value === 'string' ? new Date(value) : value
  if (Number.isNaN(date.getTime())) return '--'
  return `${formatDistanceToNowStrict(date)} ago`
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '--'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ${minutes % 60}m`
  return `${Math.floor(hours / 24)}d ${hours % 24}h`
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim()
}
