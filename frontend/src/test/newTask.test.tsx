import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import { App } from '../App'
import type { TaskDraft } from '../types'
import { chooseImages, json, taskHttp } from './newTaskFixture'

const cache = vi.hoisted(() => new Map())
vi.mock('../utils/newTaskFiles', () => ({newTaskFiles: async (id: string, files?: unknown) => {
  if (files === undefined) return cache.get(id)
  if (files === null) cache.delete(id)
  else cache.set(id, files)
}}))
const task: TaskDraft = {id: '72cf854c-83e8-4534-88af-8a29b7e04605', title: 'Integrated task',
  mode: 'quick_a', status: 'draft', can_generate: false, materials_ready: false, assets: [], slots: [],
  readiness_reason: 'missing_product_and_reference_assets', created_at: '2026-11-06T00:00:00Z'}
const mount = () => render(<MemoryRouter initialEntries={['/workspace/new']}><App /></MemoryRouter>)
beforeEach(() => cache.clear())

it('saves two sources and a configured slot once, then restores server state without generating', async () => {
  const http = taskHttp(task), fetchMock = vi.fn(http.handle); vi.stubGlobal('fetch', fetchMock)
  mount(); const user = userEvent.setup()
  await user.type(screen.getByLabelText('任务名称'), task.title); await chooseImages(user)
  await user.selectOptions(screen.getByLabelText('新建输出尺寸'), '1536x1536')
  await user.type(screen.getByLabelText('新建图位要求'), '保留商品附件')
  expect(fetchMock).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', {name: '保存任务和资料，进入工作台'}))
  await screen.findByTestId('task-id')
  expect(http.saved().assets.map(a => a.role)).toEqual(['product_image', 'marketing_reference'])
  expect(http.saved().slots[0]).toMatchObject({reference_asset_id: http.saved().assets[1].id,
    output_size: '1536x1536', requirements: '保留商品附件', inputs_ready: true})
  expect(screen.getByLabelText('输出尺寸')).toHaveValue('1536x1536')
  expect(fetchMock.mock.calls.filter(([, o]) => o?.method === 'POST')).toHaveLength(4)
  expect(fetchMock.mock.calls.some(([p]) => /generations|revisions/.test(p))).toBe(false)
  expect(sessionStorage.getItem('aigc.pending-task-creation')).toBeNull(); expect(cache.size).toBe(0)
})

it('retains unsaved files after an upload rejection, resumes after reload, and skips saved uploads', async () => {
  const http = taskHttp(task); let reject = true
  const fetchMock = vi.fn(async (path: string, options?: RequestInit) => {
    if (reject && path.includes('/assets?role=marketing_reference')) return json({error: {message: 'reference rejected'}}, 422)
    return http.handle(path, options)
  }); vi.stubGlobal('fetch', fetchMock)
  const first = mount(); const user = userEvent.setup()
  await user.type(screen.getByLabelText('任务名称'), task.title); await chooseImages(user)
  await user.click(screen.getByRole('button', {name: '保存任务和资料，进入工作台'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('保存营销参考失败')
  expect(screen.getByText(/reference.png · 待保存/)).toBeInTheDocument()
  expect(http.saved().assets).toHaveLength(1)
  const before = fetchMock.mock.calls.length; first.unmount(); mount()
  await screen.findByText(/reference.png · 待保存/)
  await waitFor(() => expect(screen.getByRole('button', {name: '保存任务和资料，进入工作台'})).not.toBeDisabled())
  expect(fetchMock.mock.calls.slice(before).every(([, o]) => !o?.method)).toBe(true)
  reject = false
  await user.click(screen.getByRole('button', {name: '保存任务和资料，进入工作台'}))
  await screen.findByTestId('task-id')
  const productCalls = fetchMock.mock.calls.filter(([p]) => p.includes('/assets?role=product_image'))
  const referenceCalls = fetchMock.mock.calls.filter(([p]) => p.includes('/assets?role=marketing_reference'))
  expect(productCalls).toHaveLength(1); expect(referenceCalls).toHaveLength(2)
  expect(referenceCalls[0][0]).toBe(referenceCalls[1][0]); expect(referenceCalls[0][1]?.body).toBe(referenceCalls[1][1]?.body)
})

it('replays a lost upload response with its original identity, without a duplicate server asset', async () => {
  const http = taskHttp(task); let lose = true
  const fetchMock = vi.fn(async (path: string, options?: RequestInit) => {
    const response = await http.handle(path, options)
    if (lose && path.includes('/assets?role=product_image')) {lose = false; throw new Error('lost upload response')}
    return response
  }); vi.stubGlobal('fetch', fetchMock)
  mount(); const user = userEvent.setup(); await user.type(screen.getByLabelText('任务名称'), task.title); await chooseImages(user)
  await user.click(screen.getByRole('button', {name: '保存任务和资料，进入工作台'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('结果尚未确认')
  await user.click(screen.getByRole('button', {name: '继续保存任务和资料'})); await screen.findByTestId('task-id')
  expect(http.saved().assets).toHaveLength(2)
  const calls = fetchMock.mock.calls.filter(([p]) => p.includes('/assets?role=product_image'))
  expect(calls).toHaveLength(2); expect(calls[0][0]).toBe(calls[1][0]); expect(calls[0][1]?.body).toBe(calls[1][1]?.body)
})
