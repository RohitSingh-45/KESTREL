import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  fetchAgents,
  fetchHealth,
  fetchState,
  initAgent,
  relativeTime,
  type HealthInfo,
} from './api'
import type { AgentSummary, DashboardState, PipelineNode } from './types'

type Section =
  | 'overview'
  | 'pipeline'
  | 'posts'
  | 'memory'
  | 'intelligence'
  | 'decision'
  | 'evaluation'

const NAV: { id: Section; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'pipeline', label: 'Pipeline' },
  { id: 'posts', label: 'Posts' },
  { id: 'memory', label: 'Memory' },
  { id: 'intelligence', label: 'Intelligence' },
  { id: 'decision', label: 'Decision' },
  { id: 'evaluation', label: 'Quality' },
]

function mark(status: PipelineNode['status']) {
  if (status === 'done') return '✓'
  if (status === 'processing') return '◉'
  if (status === 'blocked') return '✕'
  if (status === 'skipped') return '–'
  return '○'
}

function pct(value?: number | null) {
  if (value == null || Number.isNaN(value)) return null
  const n = value <= 1 ? value * 100 : value
  return Math.round(n)
}

export default function App() {
  const [agents, setAgents] = useState<AgentSummary[]>([])
  const [agentId, setAgentId] = useState<string>('')
  const [state, setState] = useState<DashboardState | null>(null)
  const [section, setSection] = useState<Section>('overview')
  const [selectedNode, setSelectedNode] = useState<PipelineNode | null>(null)
  const [selectedGate, setSelectedGate] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [health, setHealth] = useState<HealthInfo | null>(null)

  const load = useCallback(async (id?: string) => {
    setLoading(true)
    setError(null)
    try {
      try {
        setHealth(await fetchHealth())
      } catch {
        setHealth(null)
      }
      const list = await fetchAgents()
      setAgents(list)
      const chosen = id || agentId || list[0]?.agentId || ''
      setAgentId(chosen)
      if (!chosen) {
        setState(null)
        return
      }
      const next = await fetchState(chosen)
      setState(next)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard')
      setState(null)
    } finally {
      setLoading(false)
    }
  }, [agentId])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 15000)
    return () => window.clearInterval(timer)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const onInit = async () => {
    setBusy(true)
    setError(null)
    try {
      const id = await initAgent()
      await load(id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Init failed')
    } finally {
      setBusy(false)
    }
  }

  const hero = state?.hero
  const live = hero?.liveStatus?.toLowerCase() || 'idle'

  const show = useMemo(() => {
    if (section === 'overview') return true
    return true
  }, [section])

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand-mark">
          <div className="brand-orb" aria-hidden />
          <div>
            <strong>KESTREL</strong>
            <div><span>AI Security control</span></div>
          </div>
        </div>
        <nav className="nav">
          {NAV.map((item) => (
            <button
              key={item.id}
              className={section === item.id ? 'active' : ''}
              onClick={() => setSection(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          Operator audit layer
          <br />
          Modules 1–17 authoritative
          <br />
          UI is read-only
        </div>
      </aside>

      <main className="main">
        <div className="hero">
          <div className="hero-top">
            <div>
              <h1>{hero?.brand || 'KESTREL'}</h1>
              <p className="tagline">{hero?.tagline || 'Autonomous AI Security Agent'}</p>
              <p className="support">
                {hero?.supporting || 'Researching. Reasoning. Learning. Publishing.'}
              </p>
            </div>
            <div className="toolbar">
              <span className="status-pill">
                <i className={`status-dot ${live}`} />
                {hero?.liveStatus || 'IDLE'}
              </span>
              <select
                className="agent-select"
                value={agentId}
                onChange={(e) => {
                  setAgentId(e.target.value)
                  void load(e.target.value)
                }}
              >
                {agents.length === 0 && <option value="">No agents yet</option>}
                {agents.map((a) => (
                  <option key={a.agentId} value={a.agentId}>
                    {a.name} · {a.agentId.slice(0, 8)}
                  </option>
                ))}
              </select>
              <button onClick={() => void load(agentId)} disabled={busy}>Refresh</button>
              <button className="primary" onClick={() => void onInit()} disabled={busy}>
                {busy ? 'Booting…' : 'Init Kestrel'}
              </button>
            </div>
          </div>

          <div className="meta-grid">
            <div className="meta-card">
              <label>Agent ID</label>
              <strong>{hero?.agentId || '—'}</strong>
            </div>
            <div className="meta-card">
              <label>Current cycle</label>
              <strong>{hero?.currentCycleId?.slice(0, 13) || 'Waiting…'}</strong>
            </div>
            <div className="meta-card">
              <label>Last activity</label>
              <strong>{relativeTime(hero?.lastActivity)}</strong>
            </div>
            <div className="meta-card">
              <label>LLM status</label>
              <strong>
                {(() => {
                  const raw =
                    hero?.llmStatus
                    || health?.llm
                    || (error ? 'unreachable' : 'unknown')
                  const key = String(raw).toLowerCase().replace(/\s+/g, '_')
                  if (key === 'ready' || key === 'available') return 'LLM READY'
                  if (key === 'authentication_failed') return 'LLM AUTHENTICATION FAILED'
                  if (key === 'unstable') return 'LLM UNSTABLE'
                  if (key === 'unavailable') return 'LLM UNAVAILABLE'
                  return String(raw).toUpperCase()
                })()}
              </strong>
            </div>
          </div>
        </div>

        {error && (
          <div className="section panel" style={{ borderColor: 'rgba(248,113,113,0.4)' }}>
            {error}
          </div>
        )}

        {loading && !state && <div className="section skeleton" />}

        {!loading && !state && (
          <div className="section panel">
            <div className="section-head">
              <div>
                <h2>Waiting for the next cycle.</h2>
                <p>Initialize Kestrel to start the autonomous loop.</p>
              </div>
            </div>
          </div>
        )}

        {show && state && (
          <>
            {(section === 'overview' || section === 'pipeline') && (
              <section className="section">
                <div className="section-head">
                  <div>
                    <h2>Pipeline</h2>
                    <p>What's Kestrel thinking through right now?</p>
                  </div>
                </div>
                <div className="panel">
                  <div className="pipeline">
                    {state.pipeline.map((node) => (
                      <button
                        key={node.id}
                        className={`pipe-node ${node.status}`}
                        onClick={() => setSelectedNode(node)}
                      >
                        <span className="mark">{mark(node.status)}</span>
                        <span className="label">{node.label}</span>
                        <span className="state">{node.status}</span>
                      </button>
                    ))}
                  </div>
                  {selectedNode && (
                    <div className="drawer">
                      <div className="mono">{selectedNode.label} · {selectedNode.status}</div>
                      <div style={{ marginTop: '0.35rem' }}>
                        {String(selectedNode.detail?.reason || 'Stage detail from latest cycle.')}
                      </div>
                    </div>
                  )}
                </div>
              </section>
            )}

            {section === 'overview' && (
              <section className="section grid-2">
                <div className="panel">
                  <div className="section-head">
                    <div>
                      <h2>Kestrel Activity</h2>
                      <p>Latest signal from the autonomous loop</p>
                    </div>
                  </div>
                  <div className="feed">
                    {state.activity.length === 0 && (
                      <div className="empty">No signals yet.</div>
                    )}
                    {state.activity.map((item, idx) => (
                      <div className="feed-item" key={`${item.id || idx}`}>
                        <i />
                        <div>
                          <strong>{item.text}</strong>
                          <span>
                            {item.stage || 'cycle'}
                            {item.timestamp ? ` · ${relativeTime(item.timestamp)}` : ''}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="panel">
                  <div className="section-head">
                    <div>
                      <h2>Decision Trace</h2>
                      <p>Final action from the latest run</p>
                    </div>
                  </div>
                  <DecisionBlock
                    state={state}
                    selectedGate={selectedGate}
                    setSelectedGate={setSelectedGate}
                  />
                </div>
              </section>
            )}

            {(section === 'overview' || section === 'posts') && (
              <section className="section">
                <div className="section-head">
                  <div>
                    <h2>Published</h2>
                    <p>Posts that made it through the full loop</p>
                  </div>
                </div>
                {state.posts.length === 0 ? (
                  <div className="panel empty">Nothing published yet.</div>
                ) : (
                  <div className="grid-2">
                    {state.posts.slice(0, section === 'posts' ? 12 : 2).map((post) => (
                      <article className="panel post-card" key={post.id}>
                        <div className="byline">
                          <span>
                            <b>Kestrel</b> · AI Security · {relativeTime(post.createdAt)}
                          </span>
                          {post.quality != null && (
                            <span className="quality">
                              {post.quality}% · {(post.qualityLabel || 'GOOD').toUpperCase()}
                            </span>
                          )}
                        </div>
                        <p className="body">{post.text}</p>
                        <hr className="divider" />
                        <div>
                          <strong>Why this topic?</strong>
                          <p style={{ margin: '0.35rem 0 0', color: 'var(--muted)' }}>
                            {post.rationale || 'Rationale unavailable.'}
                          </p>
                        </div>
                        <div>
                          <strong>Sources</strong>
                          <div className="chips" style={{ marginTop: '0.4rem' }}>
                            {post.domains.length === 0 && (
                              <span className="chip">No domains</span>
                            )}
                            {post.domains.map((d) => (
                              <span className="chip" key={`${post.id}-${d}`}>{d}</span>
                            ))}
                          </div>
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </section>
            )}

            {(section === 'overview' || section === 'memory') && (
              <section className="section">
                <div className="section-head">
                  <div>
                    <h2>Memory snapshot</h2>
                    <p>What Kestrel is holding onto</p>
                  </div>
                </div>
                {state.memory.length === 0 ? (
                  <div className="panel empty">Memory is still forming.</div>
                ) : (
                  <div className="grid-3">
                    {state.memory.slice(0, section === 'memory' ? 12 : 3).map((m) => (
                      <div className="panel memory-card" key={m.id}>
                        <div className="byline">
                          <b>{m.key}</b>
                          <span className="chip">{m.kind}</span>
                        </div>
                        <div>
                          Confidence {pct(m.confidence) ?? 0}%
                          <div className="bar">
                            <span style={{ width: `${pct(m.confidence) ?? 0}%` }} />
                          </div>
                        </div>
                        <div className="kv">
                          <div>Stance<br /><b>{m.stance || '—'}</b></div>
                          <div>Evidence<br /><b>{m.supporting} supporting · {m.contradicting} contradicting</b></div>
                        </div>
                        {m.text && (
                          <p style={{ margin: '0.6rem 0 0', color: 'var(--muted)', fontSize: '0.86rem' }}>
                            {m.text}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </section>
            )}

            {(section === 'overview' || section === 'intelligence') && (
              <section className="section grid-3">
                <div className="panel">
                  <h2 style={{ marginTop: 0, fontFamily: 'var(--display)', fontSize: '1.05rem' }}>
                    Source mix
                  </h2>
                  {state.intelligence.sourceDiversity.domains.length === 0 ? (
                    <div className="empty">No signals yet.</div>
                  ) : (
                    state.intelligence.sourceDiversity.domains.map((d) => (
                      <div key={d.domain} className="kv" style={{ marginBottom: '0.4rem' }}>
                        <span>{d.domain}</span>
                        <b style={{ textAlign: 'right' }}>{d.count}</b>
                      </div>
                    ))
                  )}
                </div>
                <div className="panel">
                  <h2 style={{ marginTop: 0, fontFamily: 'var(--display)', fontSize: '1.05rem' }}>
                    Story progression
                  </h2>
                  {Object.entries(state.intelligence.storyTracking).map(([k, v]) => (
                    <div key={k} className="kv" style={{ marginBottom: '0.4rem' }}>
                      <span style={{ textTransform: 'uppercase', letterSpacing: '0.06em' }}>{k}</span>
                      <b style={{ textAlign: 'right' }}>{v}</b>
                    </div>
                  ))}
                  <p style={{ color: 'var(--muted)', marginBottom: 0 }}>
                    {state.intelligence.contradictions.summary}
                  </p>
                </div>
                <div className="panel">
                  <h2 style={{ marginTop: 0, fontFamily: 'var(--display)', fontSize: '1.05rem' }}>
                    Learning from recent runs
                  </h2>
                  <p style={{ color: 'var(--muted)' }}>Adaptive signals</p>
                  {(state.intelligence.adaptiveSignals.overused.length === 0 &&
                    state.intelligence.adaptiveSignals.undercovered.length === 0) && (
                    <div className="empty">No adaptive signals yet.</div>
                  )}
                  {state.intelligence.adaptiveSignals.overused.map((t) => (
                    <div key={`o-${t}`} className="chip" style={{ marginRight: 6, marginBottom: 6 }}>
                      {t} — overused
                    </div>
                  ))}
                  {state.intelligence.adaptiveSignals.undercovered.map((t) => (
                    <div key={`u-${t}`} className="chip" style={{ marginRight: 6, marginBottom: 6 }}>
                      {t} — undercovered
                    </div>
                  ))}
                </div>
              </section>
            )}

            {section === 'decision' && (
              <section className="section panel">
                <div className="section-head">
                  <div>
                    <h2>Decision</h2>
                    <p>Gate-by-gate reasoning from Module 16</p>
                  </div>
                </div>
                <DecisionBlock
                  state={state}
                  selectedGate={selectedGate}
                  setSelectedGate={setSelectedGate}
                />
              </section>
            )}

            {(section === 'overview' || section === 'evaluation') && (
              <section className="section panel">
                <div className="section-head">
                  <div>
                    <h2>Kestrel Quality</h2>
                    <p>Quality check on the latest publish</p>
                  </div>
                </div>
                {state.evaluation.score == null ? (
                  <div className="empty">Waiting for the next evaluated post.</div>
                ) : (
                  <>
                    <div className="eval-score">
                      <div className="num">{state.evaluation.score}</div>
                      <div className="lbl">{(state.evaluation.label || 'good').toUpperCase()}</div>
                    </div>
                    {Object.entries(state.evaluation.dimensions).map(([key, value]) => {
                      const p = pct(value as number | null)
                      if (p == null) return null
                      return (
                        <div className="dim" key={key}>
                          <span style={{ textTransform: 'capitalize' }}>{key}</span>
                          <div className="track"><i style={{ width: `${p}%` }} /></div>
                          <span className="pct">{p}%</span>
                        </div>
                      )
                    })}
                    <div className="grid-2" style={{ marginTop: '1rem' }}>
                      <div>
                        <strong>What to improve</strong>
                        {state.evaluation.improve.length === 0 && (
                          <div className="empty">No improvement signals.</div>
                        )}
                        {state.evaluation.improve.map((item) => (
                          <div key={item} style={{ marginTop: 6, color: 'var(--warn)' }}>↗ {item}</div>
                        ))}
                      </div>
                      <div>
                        <strong>Keep doing</strong>
                        {state.evaluation.keep.length === 0 && (
                          <div className="empty">No strengths recorded.</div>
                        )}
                        {state.evaluation.keep.map((item) => (
                          <div key={item} style={{ marginTop: 6, color: 'var(--ok)' }}>✓ {item}</div>
                        ))}
                      </div>
                    </div>
                  </>
                )}
              </section>
            )}
          </>
        )}
      </main>
    </div>
  )
}

function DecisionBlock({
  state,
  selectedGate,
  setSelectedGate,
}: {
  state: DashboardState
  selectedGate: number | null
  setSelectedGate: (n: number | null) => void
}) {
  const gates = state.decision.gates
  return (
    <>
      <div className="decision-list">
        {gates.length === 0 && <div className="empty">Decision trace not available yet.</div>}
        {gates.map((g, idx) => (
          <button
            key={`${g.gate}-${idx}`}
            className={`gate ${g.status === 'blocked' ? 'blocked' : ''}`}
            onClick={() => setSelectedGate(selectedGate === idx ? null : idx)}
          >
            <span className="g">{g.status === 'passed' ? '✓' : '✕'}</span>
            <span className="name">{g.gate}</span>
            <span className="mono" style={{ color: 'var(--faint)' }}>{g.action}</span>
          </button>
        ))}
      </div>
      {selectedGate != null && gates[selectedGate] && (
        <div className="drawer">
          <div className="mono">
            Gate {gates[selectedGate].gate} · {gates[selectedGate].status}
          </div>
          <div style={{ marginTop: '0.35rem' }}>
            {gates[selectedGate].reason || 'No reason recorded.'}
          </div>
        </div>
      )}
      <div className="final-action">
        <span>Final action</span>
        <strong>{(state.decision.finalAction || '—').toUpperCase()}</strong>
        {state.publication.why?.explanation ? (
          <p style={{ margin: '0.45rem 0 0', color: 'var(--muted)' }}>
            {String(state.publication.why.explanation)}
          </p>
        ) : null}
      </div>
    </>
  )
}
