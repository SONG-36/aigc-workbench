import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getTask } from '../api/tasks'
import { TaskForm } from '../components/TaskForm'
import { WorkspaceShell } from '../components/WorkspaceShell'
import { AssetUploader } from '../components/AssetUploader'
import { SlotPanel } from '../components/SlotPanel'
import { WriteFeedback } from '../components/WriteFeedback'
import { useWriteAction } from '../hooks/useWriteAction'
import { GenerationWorkspace } from '../components/GenerationWorkspace'
import type { TaskDraft } from '../types'

function SavedWorkspace({ initial }: { initial: TaskDraft }) {
  const [task, setTask] = useState(initial)
  // Serializes writes within this workspace; late snapshots cannot overwrite newer writes.
  const write = useWriteAction()
  const [unsavedSlots, setUnsavedSlots] = useState<Set<string>>(new Set())
  const onDirty = useCallback((slotId: string, dirty: boolean) => setUnsavedSlots(previous => {
    if (previous.has(slotId) === dirty) return previous
    const next = new Set(previous)
    if (dirty) next.add(slotId); else next.delete(slotId)
    return next
  }), [])
  return <WorkspaceShell task={task} output={<GenerationWorkspace task={task} locked={write.locked} unsavedSlots={unsavedSlots} />}>
    <WriteFeedback write={write} />
    <div className="asset-columns">
      <AssetUploader task={task} role="product_image" write={write} onSaved={setTask} />
      <AssetUploader task={task} role="marketing_reference" write={write} onSaved={setTask} />
    </div>
    <SlotPanel task={task} write={write} onSaved={setTask} onDirty={onDirty} />
  </WorkspaceShell>
}

export function Workspace() {
  const { taskId } = useParams()
  const [task, setTask] = useState<TaskDraft | null>(null)
  const [loading, setLoading] = useState(Boolean(taskId))
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    setTask(null); setError(null); setLoading(Boolean(taskId))
    if (taskId) getTask(taskId, controller.signal).then(value => {
      if (!controller.signal.aborted) setTask(value)
    }).catch(cause => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '任务读取失败。')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [taskId])
  if (loading) return <p role="status">正在从服务器恢复任务…</p>
  if (error) return <p role="alert" className="error">{error} 请返回任务中心核对。</p>
  if (taskId) return task && task.id === taskId ? <SavedWorkspace key={task.id} initial={task} /> : null
  return <WorkspaceShell task={null}><TaskForm /></WorkspaceShell>
}
