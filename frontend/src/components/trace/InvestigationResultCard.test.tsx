import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { InvestigationResultCard } from './InvestigationResultCard'
import type { TraceJob } from '@/types'

function job(overrides: Partial<TraceJob['summary']> = {}, jobOverrides: Partial<TraceJob> = {}): TraceJob {
  return {
    id: 'job-1',
    case_id: null,
    chain: 'tron',
    seed_address: 'TGMkKG6YRdJ6Cgnt8czbtrMXAQWQcV7Whq',
    status: 'completed',
    params: {},
    progress: {},
    summary: { ...overrides },
    data_source: 'synthetic',
    error: null,
    created_at: null,
    started_at: null,
    finished_at: null,
    duration_ms: 26,
    ...jobOverrides,
  }
}

function renderCard(j: TraceJob) {
  return render(
    <MemoryRouter>
      <InvestigationResultCard job={j} />
    </MemoryRouter>,
  )
}

describe('InvestigationResultCard', () => {
  it('shows the siren status label for high risk', () => {
    renderCard(job({ risk: { score: 78, level: 'high' } }))
    expect(screen.getByText('HIGH RISK 🚨')).toBeInTheDocument()
  })

  it('shows the siren status label for critical risk too', () => {
    renderCard(job({ risk: { score: 97, level: 'critical' } }))
    expect(screen.getByText('CRITICAL RISK 🚨')).toBeInTheDocument()
  })

  it('does not add a siren to medium or low risk', () => {
    renderCard(job({ risk: { score: 40, level: 'medium' } }))
    expect(screen.getByText('MEDIUM RISK')).toBeInTheDocument()
    expect(screen.queryByText(/🚨/)).not.toBeInTheDocument()
  })

  it('labels a found endpoint "Likely exchange" with the honesty disclaimer', () => {
    renderCard(
      job({
        attribution: {
          found: true,
          entity_name: 'Binance',
          confidence: 0.9,
          address: 'TLKf3KARKmsVczYWyZRPs7whN2uDwhtWLr',
        },
        risk: { score: 37, level: 'medium' },
      }),
    )
    expect(screen.getByText('Likely exchange')).toBeInTheDocument()
    expect(screen.getByText('Binance')).toBeInTheDocument()
    expect(screen.getByText(/does not prove identity/)).toBeInTheDocument()
  })

  it('falls back to "Not yet identified" when no endpoint was found', () => {
    renderCard(job({ attribution: { found: false } }))
    expect(screen.getByText('Not yet identified')).toBeInTheDocument()
    expect(screen.queryByText('Likely exchange')).not.toBeInTheDocument()
  })

  it('lists the behavioural reasons behind the risk score', () => {
    renderCard(
      job({
        risk: {
          score: 91,
          level: 'critical',
          reasons: ['Traced funds touched a sanctioned address'],
        },
      }),
    )
    expect(screen.getByText('• Traced funds touched a sanctioned address')).toBeInTheDocument()
  })

  it('links to the parent case when the trace belongs to one', () => {
    renderCard(job({}, { case_id: 'case-123' }))
    expect(screen.getByRole('link', { name: 'Open parent case' })).toHaveAttribute(
      'href',
      '/cases/case-123',
    )
  })

  it('omits the case link when the trace is not attached to a case', () => {
    renderCard(job({}))
    expect(screen.queryByText('Open parent case')).not.toBeInTheDocument()
  })
})
