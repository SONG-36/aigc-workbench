import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import type { TaskDraft } from '../types'
import type { SlotWorkspace } from '../api/generation'

const task: TaskDraft = {
  id: 'task-fixture', title: 'Workspace fixture', mode: 'quick_a', status: 'draft', can_generate: false,
  materials_ready: true, readiness_reason: 'assets_prepared_generation_disabled', created_at: '2026-11-05T00:00:00Z',
  assets: ['product_image', 'marketing_reference'].map((role, index) => ({
    id: `asset-${index}`, task_id: 'task-fixture', role: role as 'product_image' | 'marketing_reference',
    filename: `${role}.png`, sha256: 'a'.repeat(64), size_bytes: 100, width: 1024, height: 1536,
    media_type: 'image/png', created_at: '2026-11-05T00:00:00Z', url: `/api/tasks/task-fixture/assets/asset-${index}/content`,
  })),
  slots: [{ id: 'main-slot', task_id: 'task-fixture', kind: 'main', position: 0,
    reference_asset_id: 'asset-1', output_size: '1024x1536', requirements: 'saved requirement', inputs_ready: true }],
}
const initial: SlotWorkspace = {
  task_id: task.id, slot_id: 'main-slot', state: 'NOT_STARTED', candidates: [], executions: [],
  readiness: { can_generate: true, reason: '来源已核验', quote_token: 'a'.repeat(64), model: 'gpt-image-2.6-sunburst',
    max_cost_usd: '1.00', estimate_usd: '0.585503', decision_snapshot_id: 'decision-fixture',
    product_asset_id: 'asset-0', reference_asset_id: 'asset-1' },
}
const candidate = {
  image_version_id: 'version-fixture', candidate_id: 'candidate-fixture', task_id: task.id, slot_id: 'main-slot',
  run_id: 'run-fixture', created_at: task.created_at, product_asset_id: 'asset-0', reference_asset_id: 'asset-1',
  status: 'CANDIDATE_REVIEW_PENDING' as const, operator_approved: false as const, pass: false as const,
  url: `/api/tasks/${task.id}/slots/main-slot/versions/version-fixture/content`,
  image: { width: 1024, height: 1536, size_bytes: 100, sha256: 'b'.repeat(64) },
}
const execution = (state: 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'OUTCOME_UNKNOWN', operation = 'operation-fixture') => ({
  operation_id: operation, run_id: 'run-fixture', attempt_id: 'attempt-fixture', state, created_at: task.created_at,
  finished_at: state === 'RUNNING' ? null : task.created_at, model: 'gpt-image-2.6-sunburst',
  execution_mode: 'REAL' as const, candidate_ids: state === 'SUCCEEDED' ? [candidate.image_version_id] : [],
  http_status: state === 'FAILED' ? 403 : null, duration_ms: state === 'RUNNING' ? null : 500,
  error: state === 'FAILED' ? { code: 'MODEL_HTTP_ERROR', message: '供应商 HTTP 403；不重试。' } : null,
})
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'Content-Type': 'application/json' },
})
const mount = () => render(<MemoryRouter initialEntries={[`/workspace/${task.id}`]}><App /></MemoryRouter>)

function server(start: SlotWorkspace = initial) {
  let state = structuredClone(start)
  const fetch = vi.fn().mockImplementation((path: string, options?: RequestInit) => {
    if (path.endsWith('/generations')) {
      const body = JSON.parse(String(options?.body))
      state = { ...state, state: 'RUNNING', readiness: { ...state.readiness, can_generate: false },
        executions: [execution('RUNNING', body.operation_id)] }
      return Promise.resolve(json({ operation_id: body.operation_id, state: 'QUEUED' }, 202))
    }
    if (path.endsWith('/workspace')) return Promise.resolve(json(state))
    return Promise.resolve(json(task))
  })
  vi.stubGlobal('fetch', fetch)
  return { fetch, set: (next: SlotWorkspace) => { state = next }, posts: () => fetch.mock.calls.filter(([, o]) => o?.method === 'POST') }
}

describe('Workspace candidate review and controlled C06 requests (no paid API)', () => {
  it('restores a real-version identity, opens PNG and source previews, and remounts with GET only', async () => {
    const api = server({ ...initial, state: 'SUCCEEDED', candidates: [candidate], executions: [execution('SUCCEEDED')] })
    const first = mount()
    expect(await screen.findByAltText('当前图位的已保存生成候选')).toHaveAttribute('src', candidate.url)
    expect(screen.getByAltText('实售商品来源')).toHaveAttribute('src', task.assets[0].url)
    await userEvent.click(screen.getByRole('button', { name: '放大查看候选原图' }))
    expect(within(screen.getByRole('dialog')).getByAltText('生成候选 · 原始 PNG')).toHaveAttribute('src', candidate.url)
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    first.unmount(); mount()
    await screen.findByAltText('当前图位的已保存生成候选')
    expect(api.posts()).toHaveLength(0)
    expect(screen.queryByRole('button', { name: 'Pass' })).not.toBeInTheDocument()
  })

  it('requires explicit cost acknowledgement, sends one operation and polls server completion', async () => {
    const api = server(); mount(); const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '生成一张候选（付费）' }))
    let dialog = screen.getByRole('dialog', { name: '确认一次付费生图' })
    expect(within(dialog).getByText(/0.585503/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '确认付费，生成 1 张' })).toBeDisabled()
    await user.click(within(dialog).getByRole('button', { name: '关闭' }))
    expect(api.posts()).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: '生成一张候选（付费）' }))
    dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('checkbox'))
    await user.dblClick(within(dialog).getByRole('button', { name: '确认付费，生成 1 张' }))
    await screen.findByText('执行中', { selector: '[role=status]' })
    expect(api.posts()).toHaveLength(1)
    expect(JSON.parse(api.posts()[0][1].body)).toEqual({ operation_id: expect.any(String),
      quote_token: 'a'.repeat(64), confirm_paid: true, max_cost_usd: '1.00' })
    api.set({ ...initial, state: 'SUCCEEDED', candidates: [candidate], executions: [execution('SUCCEEDED')] })
    await screen.findByAltText('当前图位的已保存生成候选', {}, { timeout: 3000 })
    expect(api.posts()).toHaveLength(1)
  })

  it('never resends a lost POST response and resolves the receipt using reads', async () => {
    const api = server(); const original = api.fetch.getMockImplementation()!
    api.fetch.mockImplementation(async (path: string, options?: RequestInit) => {
      const result = await original(path, options)
      if (path.endsWith('/generations')) throw new Error('lost response')
      return result
    })
    mount(); const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '生成一张候选（付费）' }))
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: '确认付费，生成 1 张' }))
    await screen.findByText('执行中', { selector: '[role=status]' })
    await user.click(screen.getByRole('button', { name: '刷新执行状态' }))
    expect(api.posts()).toHaveLength(1)
  })

  it.each(['FAILED', 'OUTCOME_UNKNOWN'] as const)('shows server %s without success or automatic retry', async state => {
    const api = server({ ...initial, state, readiness: { ...initial.readiness, can_generate: false },
      executions: [execution(state)] })
    mount()
    await screen.findByText(state === 'FAILED' ? '执行失败' : '结果未知', { selector: '[role=status]' })
    expect(screen.getByRole('button', { name: '生成一张候选（付费）' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: '刷新执行状态' }))
    expect(api.posts()).toHaveLength(0)
  })

  it('blocks missing decisions and unsaved requirements', async () => {
    const api = server(); mount()
    const button = await screen.findByRole('button', { name: '生成一张候选（付费）' })
    await waitFor(() => expect(button).toBeEnabled())
    await userEvent.type(screen.getByLabelText('补充制作要求'), 'changed')
    expect(button).toBeDisabled()
    expect(api.posts()).toHaveLength(0)
  })

  it('rejects a candidate from a different slot instead of displaying its image', async () => {
    server({ ...initial, candidates: [{ ...candidate, slot_id: 'foreign-slot' }] }); mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('未应用')
    expect(screen.queryByAltText('当前图位的已保存生成候选')).not.toBeInTheDocument()
  })

  it('keeps slot selection local and ignores a late candidate response from the previous slot', async () => {
    const scene = { ...task.slots[0], id: 'scene-slot', kind: 'scene' as const, position: 1 }
    let resolve!: (value: Response) => void
    const pending = new Promise<Response>(yes => { resolve = yes })
    const fetch = vi.fn().mockImplementation((path: string, _options?: RequestInit) => {
      if (path.includes('/main-slot/workspace')) return pending
      if (path.includes('/scene-slot/workspace')) return Promise.resolve(json({ ...initial,
        slot_id: scene.id, readiness: { ...initial.readiness, can_generate: false, reason: '无有效制作决定' } }))
      return Promise.resolve(json({ ...task, slots: [task.slots[0], scene] }))
    })
    vi.stubGlobal('fetch', fetch); mount()
    await userEvent.click(await screen.findByRole('button', { name: '场景图 1' }))
    await screen.findByText('无有效制作决定')
    await act(async () => { resolve(json({ ...initial, state: 'SUCCEEDED', candidates: [candidate] })); await pending })
    expect(screen.queryByAltText('当前图位的已保存生成候选')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '场景图 1' })).toHaveAttribute('aria-pressed', 'true')
    expect(fetch.mock.calls.every(([, options]) => !options?.method)).toBe(true)
  })
})

it('keeps modification drafts separate across slots in the existing Workspace', async () => {
  const second = { ...task.slots[0], id: 'selling-slot', kind: 'selling_point' as const, position: 1 }
  const multi = { ...task, slots: [...task.slots, second] }
  const fetch = vi.fn().mockImplementation((path: string) => {
    if (path.endsWith('/workspace')) {
      const slot = path.includes('/selling-slot/') ? second.id : task.slots[0].id
      return Promise.resolve(json({ ...initial, slot_id: slot, can_request_revision: true,
        candidates: [{ ...candidate, slot_id: slot, image_version_id: `version-${slot}`,
          url: `/api/tasks/${task.id}/slots/${slot}/versions/version-${slot}/content` }] }))
    }
    return Promise.resolve(json(multi))
  }); vi.stubGlobal('fetch', fetch)
  mount(); const user = userEvent.setup()
  await user.click(await screen.findByText('以当前查看版本为修改基础'))
  await user.type(screen.getByLabelText('本轮修改意见'), 'Main draft stays with main')
  await user.click(screen.getByRole('button', { name: '卖点图 1' }))
  await user.click(await screen.findByText('以当前查看版本为修改基础'))
  expect(screen.getByLabelText('本轮修改意见')).toHaveValue('')
  await user.type(screen.getByLabelText('本轮修改意见'), 'Selling draft stays with selling')
  await user.click(screen.getByRole('button', { name: '主图' }))
  await screen.findByText('以当前查看版本为修改基础')
  expect(screen.getByLabelText('本轮修改意见')).toHaveValue('Main draft stays with main')
  expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(0)
})
