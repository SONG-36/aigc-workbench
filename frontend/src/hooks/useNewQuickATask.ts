import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, configureSlot, createQuickADraft, createSlot, getTask, uploadAsset } from '../api/tasks'
import type { OutputSize, SlotKind, TaskDraft } from '../types'
import { operationId } from '../utils/operationId'
import { newTaskFiles } from '../utils/newTaskFiles'

const key = 'aigc.pending-task-creation'
type Role = 'product' | 'reference'
type Files = Record<Role, File | null>
type FileInfo = Pick<File, 'name' | 'size' | 'type' | 'lastModified'>
type Draft = {
  title: string; overallRequirements: string; operationId: string; kind: SlotKind; size: OutputSize; requirements: string
  productOp: string; referenceOp: string; slotOp: string; started: boolean
  taskId: string | null; productId: string | null; referenceId: string | null; slotId: string | null
  fileInfo: Record<Role, FileInfo | null>
}
function restore(): Draft {
  const fresh: Draft = { title: '', overallRequirements: '', operationId: operationId(), kind: 'main', size: '1024x1536', requirements: '',
    productOp: operationId(), referenceOp: operationId(), slotOp: operationId(), started: false,
    taskId: null, productId: null, referenceId: null, slotId: null, fileInfo: { product: null, reference: null } }
  try {
    const old = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (typeof old?.title === 'string' && typeof old?.operationId === 'string') return { ...fresh, ...old }
  } catch { /* Never auto-submit an unreadable local intent. */ }
  return fresh
}
function remember(draft: Draft | null) {
  try {
    if (draft) sessionStorage.setItem(key, JSON.stringify(draft))
    else sessionStorage.removeItem(key)
  } catch { /* Memory retains this intent; UI warns before refresh. */ }
}
const fileInfo = (file: File | null): FileInfo | null => file && ({ name: file.name, size: file.size, type: file.type, lastModified: file.lastModified })

export function useNewQuickATask() {
  const [draft, setDraft] = useState(restore)
  const current = useRef(draft)
  const [files, setFiles] = useState<Files>({ product: null, reference: null })
  const selected = useRef(files)
  const [task, setTask] = useState<TaskDraft | null>(null)
  const [busy, setBusy] = useState(false)
  const [hydrating, setHydrating] = useState(true)
  const [stage, setStage] = useState('')
  const [error, setError] = useState<{ stage: string; message: string; unknown: boolean; role?: Role } | null>(null)
  const [cacheWarning, setCacheWarning] = useState('')
  const active = useRef(true), inFlight = useRef(false)
  const cacheQueue = useRef(Promise.resolve())
  const navigate = useNavigate()
  function checkpoint(patch: Partial<Draft>) {
    current.current = { ...current.current, ...patch }; remember(current.current)
    if (active.current) setDraft(current.current)
  }
  useEffect(() => {
    active.current = true
    const initial = current.current
    void (async () => {
      try {
        const cached = await newTaskFiles(initial.operationId)
        if (cached && active.current) {
          const restored: Files = { product: null, reference: null }
          for (const role of ['product', 'reference'] as const) {
            if (JSON.stringify(fileInfo(cached[role])) === JSON.stringify(initial.fileInfo[role])) restored[role] = cached[role]
          }
          selected.current = restored; setFiles(restored)
        }
      } catch { /* Missing files are explicitly shown; server checkpoints are never treated as local files. */ }
      if (initial.taskId) {
        try { const saved = await getTask(initial.taskId); if (active.current) setTask(saved) }
        catch { if (active.current) setCacheWarning('已保存任务暂时无法读取，请重试恢复；不会新建另一任务。') }
      }
      if (active.current) setHydrating(false)
    })()
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (selected.current.product || selected.current.reference || current.current.started) {
        event.preventDefault(); event.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', beforeUnload)
    return () => { active.current = false; window.removeEventListener('beforeunload', beforeUnload) }
  }, [])

  function edit(patch: Partial<Pick<Draft, 'title' | 'overallRequirements' | 'kind' | 'size' | 'requirements'>>) {
    if (!current.current.started && !inFlight.current) checkpoint(patch)
  }
  function canChoose(role: Role) {
    return !busy && !hydrating && !current.current[role === 'product' ? 'productId' : 'referenceId']
      && (!current.current.started || !selected.current[role] || (error?.role === role && !error.unknown))
  }
  function choose(role: Role, file: File | null) {
    if (!canChoose(role)) return
    const replacingRejected = current.current.started && error?.role === role && !error.unknown
    if (current.current.started && !replacingRejected
      && JSON.stringify(fileInfo(file)) !== JSON.stringify(current.current.fileInfo[role])) {
      setCacheWarning('请重新选择同一原始文件以恢复未确认操作；不能用新文件替换结果未知的上传。'); return
    }
    selected.current = { ...selected.current, [role]: file }; setFiles(selected.current)
    checkpoint({ fileInfo: { ...current.current.fileInfo, [role]: fileInfo(file) },
      ...(replacingRejected ? { [role === 'product' ? 'productOp' : 'referenceOp']: operationId() } : {}) })
    const pending = { ...selected.current }, id = current.current.operationId
    cacheQueue.current = cacheQueue.current.then(async () => { await newTaskFiles(id, pending) }).catch(() => {
      if (active.current) setCacheWarning('本页保留待上传文件；浏览器暂存不可用，刷新后需要重新选择未上传文件。已保存资料不会重复上传。')
    })
  }

  async function save() {
    if (inFlight.current || hydrating) return
    inFlight.current = true; setBusy(true); setError(null)
    let step = '检查输入', role: Role | undefined
    const progress = (value: string) => { step = value; if (active.current) setStage(value) }
    const publish = (saved: TaskDraft) => { if (active.current) setTask(saved) }
    try {
      for (const r of ['product', 'reference'] as const) {
        if (!current.current[r === 'product' ? 'productId' : 'referenceId'] && !selected.current[r])
          throw new ApiError(`请选择${r === 'product' ? '实际商品图片' : '营销参考图片'}，文件尚未保存。`, 'rejected')
      }
      await cacheQueue.current
      if (!active.current) return
      checkpoint({ started: true })
      progress(current.current.taskId ? '恢复已保存任务' : '创建任务')
      let saved = current.current.taskId ? await getTask(current.current.taskId)
        : await createQuickADraft(current.current.title, current.current.operationId, current.current.overallRequirements)
      checkpoint({ taskId: saved.id }); publish(saved)
      if (!active.current) return
      for (const r of ['product', 'reference'] as const) {
        role = r; progress(r === 'product' ? '保存商品图片' : '保存营销参考')
        const idKey = r === 'product' ? 'productId' : 'referenceId'
        const assetRole = r === 'product' ? 'product_image' : 'marketing_reference'
        if (current.current[idKey]) {
          if (!saved.assets.some(a => a.id === current.current[idKey] && a.role === assetRole))
            throw new ApiError('已保存素材身份与服务器不一致，请打开已保存部分核对。', 'unknown')
        } else {
          saved = await uploadAsset(saved.id, assetRole, selected.current[r]!, current.current[r === 'product' ? 'productOp' : 'referenceOp'])
          const assets = saved.assets.filter(a => a.role === assetRole)
          if (assets.length !== 1) throw new ApiError('本次素材身份无法唯一确认，请打开已保存部分核对。', 'unknown')
          checkpoint({ [idKey]: assets[0].id }); publish(saved)
        }
        if (!active.current) return
      }
      role = undefined; progress('创建图位')
      if (!current.current.slotId) {
        saved = await createSlot(saved.id, current.current.kind, current.current.slotOp)
        const slots = saved.slots.filter(s => s.kind === current.current.kind)
        if (slots.length !== 1) throw new ApiError('本次图位身份无法唯一确认，请核对已保存任务。', 'unknown')
        checkpoint({ slotId: slots[0].id }); publish(saved)
      }
      if (!active.current) return
      progress('保存图位配置')
      const slot = saved.slots.find(s => s.id === current.current.slotId)
      if (!slot) throw new ApiError('服务器未返回本次图位，不能报告完成。', 'unknown')
      saved = await configureSlot(saved.id, slot, { reference_asset_id: current.current.referenceId,
        output_size: current.current.size, requirements: current.current.requirements })
      const ready = saved.slots.find(s => s.id === current.current.slotId)
      if (saved.overall_requirement?.text !== current.current.overallRequirements
        || !ready?.inputs_ready || !saved.materials_ready || ready.reference_asset_id !== current.current.referenceId
        || ready.output_size !== current.current.size || ready.requirements !== current.current.requirements)
        throw new ApiError('服务器尚未确认资料和图位已完整保存，请核对后重试。', 'unknown')
      publish(saved); remember(null)
      await newTaskFiles(current.current.operationId, null).catch(() => undefined)
      if (active.current) navigate(`/workspace/${saved.id}`)
    } catch (cause) {
      if (active.current) setError({ stage: step, role, unknown: !(cause instanceof ApiError) || cause.outcome === 'unknown',
        message: cause instanceof Error ? cause.message : '保存失败，请核对已保存部分。' })
      if (step === '创建任务' && cause instanceof ApiError && cause.status === 422) checkpoint({ started: false })
    } finally { inFlight.current = false; if (active.current) setBusy(false) }
  }
  return { draft, files, task, busy, hydrating, stage, error, cacheWarning, edit, choose, canChoose, save }
}
