/**
 * The chat agent pop-up offers templates only unless the catalog reports
 * `member_choices: true` (the gateway's `dashboard.crewmates_in_agent_picker`
 * config). Either way the folded `agents` list is NOT filtered -- cron,
 * channel and project bindings still see every name, and a bare name still
 * resolves member-first -- so the picker setting can never change what a
 * name-only consumer dispatches.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { waitFor } from '@testing-library/react'
import { renderHookWithProviders } from './helpers'
import { useAgents } from '../hooks/useAgents'
import { api } from '../api/client'

vi.mock('../api/client', () => ({
  api: {
    agentCatalog: vi.fn(),
  },
}))

const catalog = [
  { name: 'reviewer', kiro_agent: 'reviewer', workspace: 'default', memory_store: 'member-reviewer', description: 'My reviewer', source: 'kirocrew', selection_kind: 'member' },
  { name: 'reviewer', kiro_agent: 'reviewer', workspace: 'default', memory_store: 'default', description: 'Shared reviewer', source: 'package', selection_kind: 'template' },
  { name: 'default', kiro_agent: 'kirocrew', workspace: 'default', memory_store: 'default', description: 'built-in', source: 'kirocrew', selection_kind: 'member' },
  { name: 'atlas', kiro_agent: 'atlas', workspace: 'default', memory_store: 'default', description: 'package agent', source: 'package', selection_kind: 'template' },
]

const agentsApi = vi.mocked(api.agentCatalog)

describe('useAgents keeps crewmates out of the picker unless the catalog allows them', () => {
  beforeEach(() => {
    agentsApi.mockReset()
  })

  it.each([
    ['absent', {}],
    ['false', { member_choices: false }],
  ])('withholds member rows from `choices` when member_choices is %s', async (_label, flag) => {
    agentsApi.mockResolvedValue({ agents: catalog, default_agent: 'default', ...flag } as never)
    const { result } = renderHookWithProviders(() => useAgents(0))
    await waitFor(() => expect(result.current.choices).toHaveLength(2))

    expect(result.current.choices.map(c => [c.selection_kind, c.name])).toEqual([
      ['template', 'reviewer'],
      ['template', 'atlas'],
    ])
  })

  it('offers members and templates in `choices` when member_choices is true', async () => {
    agentsApi.mockResolvedValue({ agents: catalog, default_agent: 'default', member_choices: true } as never)
    const { result } = renderHookWithProviders(() => useAgents(0))
    await waitFor(() => expect(result.current.choices).toHaveLength(4))

    expect(result.current.choices.map(c => [c.selection_kind, c.name])).toEqual([
      ['member', 'reviewer'],
      ['template', 'reviewer'],
      ['member', 'default'],
      ['template', 'atlas'],
    ])
  })

  it.each([false, true])('leaves the folded name-only `agents` list member-first and complete (member_choices=%s)', async (memberChoices) => {
    agentsApi.mockResolvedValue({ agents: catalog, default_agent: 'default', member_choices: memberChoices } as never)
    const { result } = renderHookWithProviders(() => useAgents(0))
    await waitFor(() => expect(result.current.agents).toHaveLength(3))

    // One row per name; the member still wins the fold for a shared name, so a
    // cron or channel binding to `reviewer` dispatches exactly what it did before.
    const reviewer = result.current.agents.find(a => a.name === 'reviewer')
    expect(reviewer?.selection_kind).toBe('member')
    expect(result.current.agents.map(a => a.name)).toEqual(['reviewer', 'default', 'atlas'])
    expect(result.current.defaultAgent).toBe('default')
  })
})
