export type LiveStatus = 'ONLINE' | 'PROCESSING' | 'IDLE'

export type PipelineStatus = 'done' | 'processing' | 'waiting' | 'blocked' | 'skipped'

export interface AgentSummary {
  agentId: string
  name: string
  domain: string
  status: string
  cycleCount: number
  lastCycleAt?: string | null
}

export interface PipelineNode {
  id: string
  label: string
  status: PipelineStatus
  detail?: Record<string, unknown>
}

export interface ActivityItem {
  id?: string
  stage?: string
  type?: string
  text: string
  status?: string
  timestamp?: string
}

export interface PostCard {
  id: string
  text: string
  rationale: string
  topic?: string | null
  createdAt?: string | null
  sources: string[]
  domains: string[]
  quality?: number | null
  qualityLabel?: string | null
}

export interface MemoryCard {
  id: string
  kind: string
  key: string
  text: string
  stance?: string | null
  confidence: number
  supporting: number
  contradicting: number
  updatedAt?: string | null
}

export interface DashboardState {
  hero: {
    brand: string
    tagline: string
    supporting: string
    liveStatus: LiveStatus
    agentId: string
    domain: string
    cycleCount: number
    currentCycleId?: string | null
    lastActivity?: string | null
    llmStatus: string
    agentStatus: string
  }
  pipeline: PipelineNode[]
  activity: ActivityItem[]
  posts: PostCard[]
  memory: MemoryCard[]
  intelligence: {
    sourceDiversity: {
      topicsAnnotated: number
      domains: { domain: string; count: number }[]
    }
    storyTracking: Record<string, number>
    contradictions: {
      detected: boolean
      summary: string
      memoryKey?: string
    }
    adaptiveSignals: {
      overused: string[]
      undercovered: string[]
      status?: string
    }
  }
  decision: {
    finalAction?: string
    status?: string
    gatesPassed?: number
    gatesBlocked?: number
    gates: {
      gate?: string
      action?: string
      status?: string
      reason?: string
      confidence?: number
      evidence?: Record<string, unknown>
    }[]
    why?: Record<string, unknown>
  }
  evaluation: {
    status?: string
    score?: number | null
    label?: string | null
    dimensions: Record<string, number | null | undefined>
    improve: string[]
    keep: string[]
  }
  learning: {
    adaptive: {
      status?: string
      overusedTopics: string[]
      undercoveredTopics: string[]
    }
    controlled: {
      status?: string
      strategies: string[]
      recommendations: string[]
    }
  }
  publication: {
    status?: string
    reason?: string
    why?: Record<string, unknown>
  }
  latestRun: {
    id?: string | null
    status?: string | null
    stage?: string | null
    startedAt?: string | null
    finishedAt?: string | null
  }
}
