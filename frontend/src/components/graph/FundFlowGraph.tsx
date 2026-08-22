import * as React from 'react'
import cytoscape from 'cytoscape'
import dagre from 'cytoscape-dagre'
import type { GraphEdge, GraphNode } from '@/types'
import { entityColor } from '@/lib/labels'
import { formatUsd, shortAddress } from '@/lib/format'

cytoscape.use(dagre as unknown as cytoscape.Ext)

export function FundFlowGraph({
  nodes,
  edges,
  onSelect,
  height = 520,
}: {
  nodes: GraphNode[]
  edges: GraphEdge[]
  onSelect?: (node: GraphNode | null) => void
  height?: number
}) {
  const container = React.useRef<HTMLDivElement>(null)
  const cyRef = React.useRef<cytoscape.Core | null>(null)

  React.useEffect(() => {
    if (!container.current) return
    const elements: cytoscape.ElementDefinition[] = [
      ...nodes.map((node) => ({
        data: {
          ...node,
          id: node.id,
          label: node.entity_name || shortAddress(node.address),
        },
      })),
      ...edges.map((edge) => ({
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          label: edge.amount_usd ? formatUsd(edge.amount_usd) : edge.asset,
          primary: edge.is_on_primary_path,
          inferred: edge.edge_kind !== 'transfer',
        },
      })),
    ]

    const cy = cytoscape({
      container: container.current,
      elements,
      layout: {
        name: 'dagre',
        rankDir: 'LR',
        nodeSep: 28,
        rankSep: 90,
        padding: 24,
      } as cytoscape.LayoutOptions,
      style: [
        {
          selector: 'node',
          style: {
            label: 'data(label)',
            color: '#e6edf7',
            'font-size': 10,
            'font-family': 'Inter, sans-serif',
            'text-valign': 'bottom',
            'text-margin-y': 6,
            'background-color': (ele: cytoscape.NodeSingular) =>
              entityColor(String(ele.data('entity_type') ?? '')),
            width: (ele: cytoscape.NodeSingular) =>
              18 + Math.min(36, Math.sqrt(Number(ele.data('value_in_usd') || 0)) / 8),
            height: (ele: cytoscape.NodeSingular) =>
              18 + Math.min(36, Math.sqrt(Number(ele.data('value_in_usd') || 0)) / 8),
            'border-width': (ele: cytoscape.NodeSingular) =>
              ele.data('is_seed') || ele.data('is_terminal') ? 3 : 1,
            'border-color': (ele: cytoscape.NodeSingular) =>
              ele.data('is_seed') ? '#60a5fa' : ele.data('is_terminal') ? '#22c55e' : '#22304a',
          },
        },
        {
          selector: 'edge',
          style: {
            width: (ele: cytoscape.EdgeSingular) => (ele.data('primary') ? 2.6 : 1.1),
            'line-color': (ele: cytoscape.EdgeSingular) =>
              ele.data('inferred') ? '#a855f7' : ele.data('primary') ? '#38bdf8' : '#334155',
            'target-arrow-color': (ele: cytoscape.EdgeSingular) =>
              ele.data('inferred') ? '#a855f7' : ele.data('primary') ? '#38bdf8' : '#334155',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            label: 'data(label)',
            'font-size': 8,
            color: '#93a3bd',
            'text-rotation': 'autorotate',
          },
        },
      ],
      minZoom: 0.3,
      maxZoom: 2.4,
    })

    cy.on('tap', 'node', (event) => {
      const id = event.target.id()
      onSelect?.(nodes.find((n) => n.id === id) ?? null)
    })
    cy.on('tap', (event) => {
      if (event.target === cy) onSelect?.(null)
    })

    cyRef.current = cy
    return () => {
      cy.destroy()
      cyRef.current = null
    }
  }, [nodes, edges, onSelect])

  return (
    <div
      ref={container}
      className="w-full overflow-hidden rounded-xl border border-[--color-line] bg-[#070b14]"
      style={{ height }}
    />
  )
}
