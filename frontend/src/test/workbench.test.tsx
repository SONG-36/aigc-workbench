import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { vi, describe, it, expect } from 'vitest'
import { App } from '../App'
import { createQuickADraft } from '../api/tasks'
import type { TaskDraft } from '../types'

const task: TaskDraft = {
  id: '5a4c3265-6094-434b-aa85-e64ae8e244a1', title: 'F01 sample',
  mode: 'quick_a', status: 'draft', can_generate: false,
  readiness_reason: 'missing_product_and_reference_assets', created_at: '2026-11-04T00:00:00Z',
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
const mount = (path: string) => render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>)

describe('F01 frontend with explicit HTTP mocks (not browser or AI acceptance)', () => {
  it('creates only on submit, then reads the persisted task by stable identity', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json(task, 201)).mockResolvedValue(json(task))
    vi.stubGlobal('fetch', fetchMock)
    mount('/workspace/new')
    expect(fetchMock).not.toHaveBeenCalled()
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('任务名称'), task.title)
    await user.click(screen.getByRole('button', { name: '保存 Quick-A 草稿' }))
    expect(await screen.findByTestId('task-id')).toHaveTextContent(task.id)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/tasks')
    expect(fetchMock.mock.calls[0][1].body).toBe(JSON.stringify({ title: task.title }))
    expect(fetchMock.mock.calls[1][0]).toBe(`/api/tasks/${task.id}`)
  })

  it('restores from server using the URL, without localStorage or a write', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(task))
    vi.stubGlobal('fetch', fetchMock)
    mount(`/workspace/${task.id}`)
    expect(await screen.findByTestId('task-id')).toHaveTextContent(task.id)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][1].method).toBeUndefined()
    expect(screen.getByRole('button', { name: '生成图片（未开放）' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Inspector/ })).toBeDisabled()
    expect(screen.getByText('尚未提供商品图与营销参考，不可生成。')).toBeInTheDocument()
  })

  it('keeps form content and displays a failed save instead of fake success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ error: { message: '任务名称无效。' } }, 422)))
    mount('/workspace/new')
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('任务名称'), '  ')
    await user.click(screen.getByRole('button', { name: '保存 Quick-A 草稿' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('任务名称无效。')
    expect(screen.queryByTestId('task-id')).not.toBeInTheDocument()
    expect(screen.getByLabelText('任务名称')).toHaveValue('  ')
  })

  it('disables duplicate submission while the actual request is pending', async () => {
    let finish!: (response: Response) => void
    const fetchMock = vi.fn().mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve }))
      .mockResolvedValue(json(task))
    vi.stubGlobal('fetch', fetchMock)
    mount('/workspace/new')
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('任务名称'), 'pending')
    await user.dblClick(screen.getByRole('button', { name: '保存 Quick-A 草稿' }))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: '正在保存草稿…' })).toBeDisabled()
    finish(json(task, 201))
    await screen.findByTestId('task-id')
  })

  it('shows server tasks and a functional recovery link', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json([task])))
    mount('/')
    expect(await screen.findByText(task.title)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '打开 / 继续任务' })).toHaveAttribute('href', `/workspace/${task.id}`)
  })

  it('distinguishes a failed list from an empty list and can retry the read', async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new Error('connection unavailable')).mockResolvedValue(json([]))
    vi.stubGlobal('fetch', fetchMock)
    mount('/')
    expect(await screen.findByRole('alert')).toHaveTextContent('connection unavailable')
    await userEvent.click(screen.getByRole('button', { name: '刷新列表' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(await screen.findByText(/尚无任务/)).toBeInTheDocument()
  })

  it('keeps unavailable shell controls disabled and other modes unavailable', () => {
    mount('/workspace/new')
    expect(screen.getByRole('option', { name: /Quick-B/ })).toBeDisabled()
    expect(screen.getByRole('option', { name: /Listing/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /添加商品图片/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /添加卖点图/ })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Pass' })).not.toBeInTheDocument()
  })

  it('does not apply an unexpected production-ready server result to this draft UI', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ ...task, can_generate: true }, 201)))
    await expect(createQuickADraft('sample')).rejects.toThrow('未应用该状态')
  })
})
