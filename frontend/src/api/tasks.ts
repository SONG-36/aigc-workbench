import type { TaskDraft } from '../types'

async function request(path: string, options: RequestInit = {}): Promise<unknown> {
  const response = await fetch(`/api${path}`, options)
  let body: unknown
  try {
    body = await response.json()
  } catch {
    throw new Error('服务未返回可读数据，请检查后端连接。')
  }
  if (!response.ok) {
    const message = (body as { error?: { message?: string } })?.error?.message
    throw new Error(message || `请求失败（${response.status}）。`)
  }
  return body
}

function decodeTask(value: unknown): TaskDraft {
  const task = value as Partial<TaskDraft> | null
  if (!task || typeof task.id !== 'string' || typeof task.title !== 'string'
    || task.mode !== 'quick_a' || task.status !== 'draft' || task.can_generate !== false
    || task.readiness_reason !== 'missing_product_and_reference_assets'
    || typeof task.created_at !== 'string') {
    throw new Error('服务器返回的任务格式不符合当前草稿接口，未应用该状态。')
  }
  return task as TaskDraft
}

export async function createQuickADraft(title: string): Promise<TaskDraft> {
  return decodeTask(await request('/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  }))
}

export async function getTask(id: string, signal?: AbortSignal): Promise<TaskDraft> {
  return decodeTask(await request(`/tasks/${encodeURIComponent(id)}`, { signal }))
}

export async function listTasks(signal?: AbortSignal): Promise<TaskDraft[]> {
  const body = await request('/tasks', { signal })
  if (!Array.isArray(body)) throw new Error('任务列表格式不正确。')
  return body.map(decodeTask)
}
