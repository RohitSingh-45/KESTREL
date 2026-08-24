import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StageChecklist } from './StageChecklist'
import type { TraceEvent } from '@/types'

const STAGE_LABELS = [
  'Fetching transactions',
  'Building transaction graph',
  'Tracing fund flow',
  'Analysing behaviour',
]

describe('StageChecklist', () => {
  it('always renders all four stages', () => {
    render(<StageChecklist event={null} />)
    for (const label of STAGE_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })

  it('shows no progress detail before any event arrives', () => {
    render(<StageChecklist event={null} />)
    expect(screen.queryByText(/transactions found/)).not.toBeInTheDocument()
  })

  it('surfaces the running transaction count once fetching is underway', () => {
    const event = { seq: 1, job_id: 'x', event: 'hop_started', hop: 0, transfers_examined: 23 } as TraceEvent
    render(<StageChecklist event={event} />)
    expect(screen.getByText('✓ 23 transactions found')).toBeInTheDocument()
  })

  it('marks fetch and graph done and shows the hop count once tracing is past hop 0', () => {
    const event = {
      seq: 3,
      job_id: 'x',
      event: 'hop_completed',
      hop: 2,
      transfers_examined: 23,
      addresses_examined: 9,
      edges: 10,
    } as TraceEvent
    render(<StageChecklist event={event} />)
    expect(screen.getByText('✓ 23 transactions found')).toBeInTheDocument()
    expect(screen.getByText('✓ 9 addresses, 10 edges')).toBeInTheDocument()
    expect(screen.getByText('✓ 2 hops identified')).toBeInTheDocument()
    // Analysing hasn't started yet, so it gets no detail even if the field exists.
    expect(screen.queryByText(/exchange endpoint/)).not.toBeInTheDocument()
  })

  it('shows every detail, including the exchange-endpoint count, once completed', () => {
    const event = {
      seq: 9,
      job_id: 'x',
      event: 'completed',
      hop: 4,
      transfers_examined: 23,
      addresses_examined: 9,
      edges: 10,
      vasp_hits: 1,
    } as TraceEvent
    render(<StageChecklist event={event} />)
    expect(screen.getByText('✓ 1 exchange endpoint found')).toBeInTheDocument()
  })

  it('pluralises the endpoint count correctly', () => {
    const event = { seq: 9, job_id: 'x', event: 'completed', hop: 4, vasp_hits: 2 } as TraceEvent
    render(<StageChecklist event={event} />)
    expect(screen.getByText('✓ 2 exchange endpoints found')).toBeInTheDocument()
  })
})
