import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { ApiError, createQuickADraft } from '../api/tasks'
import type { Asset, TaskDraft } from '../types'

const task: TaskDraft = {
  id: '2e5c749d-99ce-4fd1-8433-bf6a4d21d931', title: 'S01',
  mode: 'quick_a', status: 'draft', can_generate: false, materials_ready: false,
  readiness_reason: 'missing_product_and_reference_assets', created_at: '2026-11-04T00:00:00Z',
  assets: [], slots: [],
}
const asset: Asset = {
  id: 'c7a7c617-d604-48bf-8a69-c48c25805144', task_id: task.id,
  role: 'product_image', filename: 'product.png', sha256: 'a'.repeat(64),
  size_bytes: 10, media_type: 'image/png', width: 12, height: 9,
  created_at: task.created_at, url: '',
}
asset.url = `/api/tasks/${task.id}/assets/${asset.id}/content`
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
const mount = (path: string) => render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>)
const nativeURL = URL
let revoke = vi.fn<(url: string) => void>()

beforeEach(() => {
  revoke = vi.fn<(url: string) => void>()
  vi.stubGlobal('URL', class extends nativeURL {
    static createObjectURL = vi.fn(() => 'blob:local-preview')
    static revokeObjectURL = revoke
  })
})

describe('S01 deterministic UI contracts with explicit HTTP mocks', () => {
  it.each(['success', 'failure'])('ignores late %s after leaving a submitting page', async result => {
    let resolve!: (response: Response) => void
    let reject!: (error: Error) => void
    const pending = new Promise<Response>((yes, no) => { resolve = yes; reject = no })
    const fetchMock = vi.fn().mockImplementation((_path, options) =>
      options?.method === 'POST' ? pending : Promise.resolve(json([])))
    vi.stubGlobal('fetch', fetchMock)
    mount('/workspace/new')
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('任务名称'), 'late')
    await user.click(screen.getByRole('button', { name: '保存 Quick-A 草稿' }))
    await user.click(screen.getByRole('link', { name: '任务中心' }))
    await screen.findByText(/尚无任务/)
    await act(async () => {
      if (result === 'success') resolve(json(task, 201))
      else reject(new Error('late failure'))
      await pending.catch(() => undefined)
    })
    expect(screen.getByRole('heading', { name: '制作任务' })).toBeInTheDocument()
    expect(screen.queryByTestId('task-id')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([path]) => path === `/api/tasks/${task.id}`)).toBe(false)
  })

  it('retries an unknown Task result with the exact same operation and content', async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new Error('lost response'))
      .mockResolvedValueOnce(json(task, 201)).mockResolvedValue(json(task))
    vi.stubGlobal('fetch', fetchMock)
    mount('/workspace/new')
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('任务名称'), 'unknown')
    await user.click(screen.getByRole('button', { name: '保存 Quick-A 草稿' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('保存结果未知')
    expect(screen.getByLabelText('任务名称')).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '安全重试本次保存' }))
    await screen.findByTestId('task-id')
    expect(fetchMock.mock.calls[0][1].body).toBe(fetchMock.mock.calls[1][1].body)
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).operation_id).toMatch(/^[0-9a-f-]{36}$/)
    expect(sessionStorage.getItem('aigc.pending-task-creation')).toBeNull()
  })

  it('restores only a pending creation intent after reload and never auto-submits', async () => {
    const operationId = crypto.randomUUID()
    sessionStorage.setItem('aigc.pending-task-creation', JSON.stringify({ title: 'pending', operationId }))
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(json(task)))
    vi.stubGlobal('fetch', fetchMock)
    mount('/workspace/new')
    expect(screen.getByLabelText('任务名称')).toHaveValue('pending')
    expect(fetchMock).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '保存 Quick-A 草稿' }))
    await screen.findByTestId('task-id')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ title: 'pending', operation_id: operationId })
  })

  it('a definite rejection remains editable and is not shown as unknown', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ error: { message: 'conflict' } }, 409)))
    mount('/workspace/new')
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('任务名称'), 'bad')
    await user.click(screen.getByRole('button', { name: '保存 Quick-A 草稿' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('conflict')
    expect(screen.getByLabelText('任务名称')).not.toBeDisabled()
    expect(screen.queryByRole('button', { name: '安全重试本次保存' })).not.toBeInTheDocument()
  })

  it('treats an unreadable success response as unknown, not a rejected write', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('broken', { status: 201 })))
    try {
      await createQuickADraft('sample', crypto.randomUUID())
      throw new Error('expected rejection')
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      expect((error as ApiError).outcome).toBe('unknown')
    }
  })

  it('separates pending preview from persisted asset and revokes its Object URL', async () => {
    let finish!: (response: Response) => void
    const fetchMock = vi.fn().mockImplementation((_path, options) => options?.method === 'POST'
      ? new Promise<Response>(resolve => { finish = resolve }) : Promise.resolve(json(task)))
    vi.stubGlobal('fetch', fetchMock)
    mount(`/workspace/${task.id}`)
    await screen.findByTestId('task-id')
    const user = userEvent.setup()
    const file = new File(['png-fixture'], 'product.png', { type: 'image/png' })
    await user.upload(screen.getByLabelText('选择商品图片'), file)
    expect(screen.getByAltText('待上传预览：product.png')).toHaveAttribute('src', 'blob:local-preview')
    expect(screen.queryByText(/服务端已保存/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '上传并保存商品图片' }))
    expect(screen.queryByText(/服务端已保存/)).not.toBeInTheDocument()
    await act(async () => { finish(json({ ...task, assets: [asset] }, 201)) })
    expect(await screen.findByAltText('product.png')).toHaveAttribute('src', asset.url)
    expect(screen.queryByAltText('待上传预览：product.png')).not.toBeInTheDocument()
    expect(revoke).toHaveBeenCalledWith('blob:local-preview')
    expect(fetchMock.mock.calls[1][1].body).toBe(file)
    expect(screen.getByRole('button', { name: '生成一张候选' })).toBeDisabled()
  })

  it('releases local preview on unmount and ignores a late upload response', async () => {
    let finish!: (response: Response) => void
    const fetchMock = vi.fn().mockImplementation((_path, options) => {
      if (options?.method === 'POST') return new Promise<Response>(resolve => { finish = resolve })
      return Promise.resolve(json(_path === '/api/tasks' ? [] : task))
    })
    vi.stubGlobal('fetch', fetchMock)
    mount(`/workspace/${task.id}`)
    await screen.findByTestId('task-id')
    const user = userEvent.setup()
    await user.upload(screen.getByLabelText('选择商品图片'), new File(['x'], 'product.png', { type: 'image/png' }))
    await user.click(screen.getByRole('button', { name: '上传并保存商品图片' }))
    await user.click(screen.getByRole('link', { name: '任务中心' }))
    await screen.findByText(/尚无任务/)
    expect(revoke).toHaveBeenCalledWith('blob:local-preview')
    await act(async () => { finish(json({ ...task, assets: [asset] }, 201)) })
    expect(screen.queryByTestId('task-id')).not.toBeInTheDocument()
  })

  it('keeps a failed upload pending and retries the same bytes and operation ID', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json(task))
      .mockResolvedValueOnce(json({ error: { message: 'storage unavailable' } }, 503))
      .mockResolvedValueOnce(json({ ...task, assets: [asset] }, 201))
    vi.stubGlobal('fetch', fetchMock)
    mount(`/workspace/${task.id}`)
    await screen.findByTestId('task-id')
    const user = userEvent.setup()
    await user.upload(screen.getByLabelText('选择商品图片'), new File(['x'], 'product.png', { type: 'image/png' }))
    await user.click(screen.getByRole('button', { name: '上传并保存商品图片' }))
    await screen.findByRole('alert')
    expect(screen.queryByAltText('product.png')).not.toBeInTheDocument()
    expect(screen.getByAltText('待上传预览：product.png')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '安全重试本次保存' }))
    await screen.findByAltText('product.png')
    expect(fetchMock.mock.calls[1][0]).toBe(fetchMock.mock.calls[2][0])
    expect(fetchMock.mock.calls[1][1].body).toBe(fetchMock.mock.calls[2][1].body)
  })

  it('persists a reference binding instead of treating the selection as saved', async () => {
    const reference = { ...asset, id: 'a2a940a8-35e4-4516-a783-f0a10e80e8bf', role: 'marketing_reference' as const,
      filename: 'reference.png', url: `/api/tasks/${task.id}/assets/a2a940a8-35e4-4516-a783-f0a10e80e8bf/content` }
    const slot = { id: 'bb38f83b-0fda-4c77-b559-fbc089ef413d', task_id: task.id, kind: 'main' as const,
      position: 0, reference_asset_id: null, output_size: '1024x1536', requirements: '', inputs_ready: false }
    const current = { ...task, assets: [asset, reference], slots: [slot] }
    const updated = { ...current, materials_ready: true, slots: [{ ...slot, reference_asset_id: reference.id, inputs_ready: true }] }
    const fetchMock = vi.fn().mockImplementation((path, options) => {
      if (path.endsWith('/workspace')) return Promise.resolve(json({
        task_id: task.id, slot_id: slot.id, state: 'NOT_STARTED', candidates: [], executions: [],
        readiness: { can_generate: false, reason: '无有效制作决定' },
      }))
      return Promise.resolve(json(options?.method === 'PUT' ? updated : current))
    })
    vi.stubGlobal('fetch', fetchMock)
    mount(`/workspace/${task.id}`)
    await screen.findByTestId('task-id')
    const user = userEvent.setup()
    await user.selectOptions(screen.getByLabelText('主图主要营销参考'), reference.id)
    expect(screen.getByText(/有未保存配置/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '保存主图配置' }))
    expect(await screen.findByText(/图位配置已保存 · 本图基础素材齐备/)).toBeInTheDocument()
    const saved = fetchMock.mock.calls.find(([, options]) => options?.method === 'PUT')!
    expect(saved[0]).toBe(`/api/tasks/${task.id}/slots/${slot.id}`)
    expect(JSON.parse(saved[1].body).reference_asset_id).toBe(reference.id)
    expect(screen.getByRole('button', { name: '生成一张候选' })).toBeDisabled()
  })
})
