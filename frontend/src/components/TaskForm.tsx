import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { createQuickADraft } from '../api/tasks'
import { useWriteAction } from '../hooks/useWriteAction'
import { WriteFeedback } from './WriteFeedback'

const intentKey = 'aigc.pending-task-creation'
type Intent = { title: string; operationId: string }
function previousIntent(): Intent | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(intentKey) || 'null') as Partial<Intent> | null
    return value && typeof value.title === 'string' && typeof value.operationId === 'string'
      ? value as Intent : null
  } catch { return null }
}
function remember(intent: Intent | null) {
  try {
    if (intent) sessionStorage.setItem(intentKey, JSON.stringify(intent))
    else sessionStorage.removeItem(intentKey)
  } catch { /* Storage unavailable: same-page retries still reuse the operation ID. */ }
}

export function TaskForm() {
  const [restored, setRestored] = useState(previousIntent)
  const [title, setTitle] = useState(restored?.title || '')
  const [operationId, setOperationId] = useState(() => restored?.operationId || crypto.randomUUID())
  const write = useWriteAction()
  const navigate = useNavigate()

  useEffect(() => {
    if (write.error && !write.unknown) {
      remember(null); setRestored(null); setOperationId(crypto.randomUUID())
    }
  }, [write.error, write.unknown])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    remember({ title, operationId })
    void write.run(() => createQuickADraft(title, operationId), task => {
      remember(null)
      navigate(`/workspace/${task.id}`)
    })
  }

  return <form onSubmit={submit} className="draft-form">
    {restored && <p className="notice">已恢复上次未确认的创建意图。提交会复用原操作身份；这不是已保存任务。</p>}
    <label htmlFor="task-title">任务名称</label>
    <input id="task-title" value={title} onChange={event => setTitle(event.target.value)}
      maxLength={120} required disabled={write.locked || Boolean(restored)}
      placeholder="为当前目标商品填写一个任务名称" />
    <div className="actions">
      <span className="muted">保存稳定任务身份，之后在原任务上上传素材。</span>
      <button type="submit" className="primary" disabled={write.locked}>
        {write.busy ? '正在保存草稿…' : '保存 Quick-A 草稿'}
      </button>
    </div>
    <WriteFeedback write={write} />
  </form>
}
