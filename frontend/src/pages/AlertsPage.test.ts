import { describe, expect, it } from 'vitest'
import { plainSummary, slaPill } from './AlertsPage'
import type { Alert } from '@/types'

function alert(overrides: Partial<Alert> = {}): Alert {
  return {
    id: 'a1',
    case_id: null,
    case_number: null,
    job_id: null,
    alert_type: 'vasp_identified',
    severity: 'high',
    status: 'new',
    title: 'CoinDCX deposit address identified at hop 3',
    message: 'long technical message',
    chain: 'tron',
    address: null,
    vasp_slug: 'coindcx',
    vasp_name: 'CoinDCX',
    recommended_action: null,
    sla_due_at: null,
    sla_seconds_remaining: null,
    is_overdue: false,
    acknowledged_at: null,
    acknowledged_by_name: null,
    created_at: '2026-08-23T09:30:00Z',
    ...overrides,
  }
}

describe('plainSummary', () => {
  it('names the exchange when a VASP was identified', () => {
    expect(plainSummary(alert({ alert_type: 'vasp_identified', vasp_name: 'CoinDCX' }))).toBe(
      'Money traced to a CoinDCX account.',
    )
  })

  it('falls back to a generic sentence when the VASP name is missing', () => {
    expect(plainSummary(alert({ alert_type: 'vasp_identified', vasp_name: null }))).toBe(
      'Money traced to a named exchange.',
    )
  })

  it('gives every known alert type its own plain sentence', () => {
    expect(plainSummary(alert({ alert_type: 'sanctions_hit' }))).toMatch(/sanctioned address/)
    expect(plainSummary(alert({ alert_type: 'mixer_detected' }))).toMatch(/mixing service/)
    expect(plainSummary(alert({ alert_type: 'bridge_detected' }))).toMatch(/another blockchain/)
    expect(plainSummary(alert({ alert_type: 'high_value_flow' }))).toMatch(/unusually large/)
    expect(plainSummary(alert({ alert_type: 'rapid_movement' }))).toMatch(/within minutes/)
    expect(plainSummary(alert({ alert_type: 'trace_failed' }))).toMatch(/could not finish/)
  })
})

describe('slaPill', () => {
  it('reads "No deadline" when there is no SLA', () => {
    expect(slaPill(null, false)).toEqual({ text: 'No deadline', tone: 'default' })
  })

  it('phrases a live deadline as time left, not a bare duration', () => {
    expect(slaPill(3600, false)).toEqual({ text: '1h 0m left to respond', tone: 'default' })
  })

  it('flags an overdue alert with critical tone and an "Overdue by" phrasing', () => {
    expect(slaPill(-7200, true)).toEqual({ text: 'Overdue by 2h 0m', tone: 'critical' })
  })
})
