import type { AssetRole, ImageSlot, SlotKind, TaskDraft } from '../types'
import { OUTPUT_SIZES } from '../types'

export class ApiError extends Error {
  constructor(message: string, readonly outcome: 'rejected' | 'unknown', readonly status?: number) { super(message) }
}

export async function request(path: string, options: RequestInit = {}): Promise<unknown> {
  const write = ['POST','PUT','PATCH','DELETE'].includes(options.method || '')
  let response: Response
  try { response = await fetch(`/api${path}`, options) }
  catch { throw new ApiError(write ? '连接中断，保存结果未知。' : '无法连接服务器。', 'unknown') }
  if (response.ok && response.status === 204) return null
  let body: unknown
  try { body = await response.json() }
  catch {
    throw new ApiError('服务未返回可读数据，请查询后再处理。',
      response.status >= 400 && response.status < 500 ? 'rejected' : 'unknown', response.status)
  }
  if (!response.ok) {
    const message = (body as { error?: { message?: string } })?.error?.message
    throw new ApiError(message || `请求失败（${response.status}）。`,
      response.status < 500 ? 'rejected' : 'unknown', response.status)
  }
  return body
}

function decodeTask(value: unknown): TaskDraft {
  const t = value as Partial<TaskDraft> | null
  if (!t || typeof t.id !== 'string' || typeof t.title !== 'string'
    || !['quick_a','quick_b'].includes(t.mode || '') || t.status !== 'draft' || t.can_generate !== false
    || typeof t.materials_ready !== 'boolean' || typeof t.readiness_reason !== 'string'
    || typeof t.created_at !== 'string' || !Array.isArray(t.assets) || !Array.isArray(t.slots)
    || (t.can_delete!==undefined&&typeof t.can_delete!=='boolean')
    || (t.delete_blocked_reason!=null&&typeof t.delete_blocked_reason!=='string')
    || !t.assets.every(a => a.task_id === t.id && typeof a.id === 'string'
      && ['product_image', 'marketing_reference'].includes(a.role)
      && a.url === `/api/tasks/${t.id}/assets/${a.id}/content`
      && typeof a.sha256 === 'string' && typeof a.filename === 'string')
    || !t.slots.every(s => s.task_id === t.id && typeof s.id === 'string'
      && ['main', 'dimensions', 'comparison', 'selling_point', 'scene'].includes(s.kind)
      && OUTPUT_SIZES.includes(s.output_size)
      && typeof s.inputs_ready === 'boolean' && (s.archived===undefined||typeof s.archived==='boolean')
      && (s.generation_blocked_reason==null||typeof s.generation_blocked_reason==='string')
      && (s.version===undefined||(typeof s.version==='number'&&Number.isInteger(s.version)&&s.version>=0)))) {
    throw new ApiError('服务器返回的任务格式不符合当前接口，未应用该状态。', 'unknown')
  }
  const r = t.overall_requirement
  if (r && (r.task_id !== t.id || r.scope !== 'TASK_ALL_SLOTS' || !Number.isInteger(r.version) || r.version < 0
    || typeof r.text !== 'string' || r.text.length > 2000 || typeof r.source !== 'string'
    || (r.version > 0 && (typeof r.id !== 'string' || typeof r.saved_at !== 'string')))) {
    throw new ApiError('整体要求的版本或作用范围不符合当前任务，未应用。', 'unknown')
  }
  return {...t, overall_requirement: r || {id: null, task_id: t.id, version: 0, text: '', scope: 'TASK_ALL_SLOTS',
    saved_at: null, source: 'NOT_PROVIDED'}} as TaskDraft
}

export async function createTaskDraft(title: string, operationId: string, overallRequirements = '', mode:TaskDraft['mode']='quick_a'): Promise<TaskDraft> {
  const task = decodeTask(await request('/tasks', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, operation_id: operationId, ...(mode==='quick_b'?{mode}:{}), ...(overallRequirements ? {overall_requirements: overallRequirements} : {}) }),
  }))
  if (task.mode!==mode || task.overall_requirement?.text !== overallRequirements) throw new ApiError('服务器未确认本次模式或整体要求已保存，请核对任务；输入仍保留。', 'unknown')
  return task
}

// 保留 Quick-A 现有调用名称及请求内容，任务创建本身仍复用同一个 API。
export const createQuickADraft = (title:string,operationId:string,overallRequirements='') =>
  createTaskDraft(title,operationId,overallRequirements,'quick_a')

export async function getTask(id: string, signal?: AbortSignal): Promise<TaskDraft> {
  return decodeTask(await request(`/tasks/${encodeURIComponent(id)}`, { signal }))
}

export async function listTasks(signal?: AbortSignal): Promise<TaskDraft[]> {
  const body = await request('/tasks', { signal })
  if (!Array.isArray(body)) throw new Error('任务列表格式不正确。')
  return body.map(decodeTask)
}

export async function renameTask(task:TaskDraft,title:string):Promise<TaskDraft> {
  title=title.trim()
  const saved=decodeTask(await request(`/tasks/${task.id}`,{
    method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,expected_title:task.title}),
  }))
  if(saved.id!==task.id||saved.title!==title)throw new ApiError('服务器未确认本次任务命名，请刷新列表核对。','unknown')
  return saved
}

export async function deleteTask(taskId:string):Promise<void> {
  await request(`/tasks/${taskId}`,{method:'DELETE'})
}

export async function uploadAsset(taskId: string, role: AssetRole, file: File, operationId: string) {
  const query = new URLSearchParams({ role, operation_id: operationId, filename: file.name })
  const raw = await request(`/tasks/${taskId}/assets?${query}`, {
    method: 'POST', headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file,
  })
  return Object.assign(decodeTask(raw), {uploaded_asset_id: (raw as {uploaded_asset_id?: string}).uploaded_asset_id})
}

export async function createSlot(taskId: string, kind: SlotKind, operationId: string) {
  const raw = await request(`/tasks/${taskId}/slots`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, operation_id: operationId }),
  })
  return Object.assign(decodeTask(raw), {created_slot_id: (raw as {created_slot_id?: string}).created_slot_id})
}

export async function configureSlot(taskId: string, slot: ImageSlot,
  values: Pick<ImageSlot, 'reference_asset_id' | 'output_size' | 'requirements'>, expectedVersion = slot.version ?? 0) {
  return decodeTask(await request(`/tasks/${taskId}/slots/${slot.id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({...values, expected_version: expectedVersion}),
  }))
}


export async function saveOverallRequirement(taskId: string, text: string, expectedVersion: number, operationId: string) {
  const task = decodeTask(await request(`/tasks/${taskId}/overall-requirement`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text, expected_version: expectedVersion, operation_id: operationId}),
  }))
  if (task.overall_requirement?.text !== text || task.overall_requirement.version <= expectedVersion) {
    throw new ApiError('服务器未确认本次要求版本，请重新读取核对；未应用为保存成功。', 'unknown')
  }
  return task
}


export async function removeSlot(taskId: string, slotId: string, operationId: string) {
  return decodeTask(await request(`/tasks/${taskId}/slots/${slotId}/remove`, {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({operation_id:operationId}),
  }))
}
