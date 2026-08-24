import { describe, expect, it } from 'vitest'
import {
  formatAmount,
  formatDuration,
  formatInr,
  formatPercent,
  formatUsd,
  shortAddress,
  shortHash,
  titleCase,
} from './format'

describe('shortAddress', () => {
  it('truncates a long address with an ellipsis', () => {
    expect(shortAddress('TGMkKG6YRdJ6Cgnt8czbtrMXAQWQcV7Whq')).toBe('TGMkKG6Y...cV7Whq')
  })

  it('leaves a short address untouched', () => {
    expect(shortAddress('0xabc')).toBe('0xabc')
  })

  it('handles empty input', () => {
    expect(shortAddress('')).toBe('')
  })
})

describe('shortHash', () => {
  it('uses wider head/tail than shortAddress', () => {
    const hash = '0x9d1f86f36bd6b847899706a85992c795fb699ce116dc079b52ee272329148ac5'
    expect(shortHash(hash)).toBe('0x9d1f86f3...29148ac5')
  })
})

describe('formatUsd', () => {
  it('returns a dash for nullish values', () => {
    expect(formatUsd(null)).toBe('--')
    expect(formatUsd(undefined)).toBe('--')
  })

  it('abbreviates millions and thousands', () => {
    expect(formatUsd(2_500_000)).toBe('$2.50M')
    expect(formatUsd(38_400)).toBe('$38.4K')
  })

  it('formats sub-thousand values as plain currency', () => {
    expect(formatUsd(42)).toBe('$42')
  })
})

describe('formatInr', () => {
  it('returns a dash for nullish values', () => {
    expect(formatInr(null)).toBe('--')
  })

  it('abbreviates crore and lakh', () => {
    expect(formatInr(18_790_000)).toBe('₹1.88 Cr')
    expect(formatInr(179_000)).toBe('₹1.79 L')
  })
})

describe('formatAmount', () => {
  it('returns a dash for nullish values', () => {
    expect(formatAmount(null)).toBe('--')
  })

  it('appends the asset symbol when given', () => {
    expect(formatAmount('11500', 'USDT')).toBe('11,500 USDT')
  })

  it('uses more decimal precision for small amounts', () => {
    expect(formatAmount(0.0085, 'BTC')).toBe('0.0085 BTC')
  })
})

describe('formatPercent', () => {
  it('returns a dash for nullish values', () => {
    expect(formatPercent(null)).toBe('--')
  })

  it('converts a 0-1 fraction to a percentage string', () => {
    expect(formatPercent(0.9)).toBe('90.0%')
    expect(formatPercent(0.827, 0)).toBe('83%')
  })
})

describe('formatDuration', () => {
  it('returns a dash for nullish values', () => {
    expect(formatDuration(null)).toBe('--')
  })

  it('formats seconds, minutes, hours and days progressively', () => {
    expect(formatDuration(45)).toBe('45s')
    expect(formatDuration(125)).toBe('2m 5s')
    expect(formatDuration(3 * 3600 + 90)).toBe('3h 1m')
    expect(formatDuration(2 * 86400 + 3600 * 4)).toBe('2d 4h')
  })
})

describe('titleCase', () => {
  it('replaces underscores/dashes with spaces and capitalises each word', () => {
    expect(titleCase('deposit_consolidation')).toBe('Deposit Consolidation')
    expect(titleCase('cex-deposit')).toBe('Cex Deposit')
  })
})
