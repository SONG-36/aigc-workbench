import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getTask } from '../api/tasks'
import { TaskForm } from '../components/TaskForm'
import { WorkspaceShell } from '../components/WorkspaceShell'
import type { TaskDraft } from '../types'

export function Workspace() {
  const { taskId } = useParams()
  const [task, setTask] = useState<TaskDraft | null>(null)
  const [loading, setLoading] = useState(Boolean(taskId))
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setTask(null)
    setError(null)
    setLoading(Boolean(taskId))
    if (taskId) getTask(taskId, controller.signal).then(value => {
      if (!controller.signal.aborted) setTask(value)
    }).catch(cause => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '草稿读取失败。')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [taskId])

  if (loading) return <p role="status">正在从服务器恢复任务草稿…</p>
  if (error) return <p role="alert" className="error">{error} 请返回任务中心核对。</p>
  if (taskId && !task) return null
  return <WorkspaceShell task={task}>{!taskId && <TaskForm />}</WorkspaceShell>
}
