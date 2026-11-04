import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { createQuickADraft } from '../api/tasks'

export function TaskForm() {
  const [title, setTitle] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (saving) return
    setSaving(true)
    setError(null)
    try {
      const task = await createQuickADraft(title)
      navigate(`/workspace/${task.id}`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存失败，请核对任务列表后再操作。')
    } finally {
      setSaving(false)
    }
  }

  return <form onSubmit={submit} className="draft-form">
    <label htmlFor="task-title">任务名称</label>
    <input id="task-title" value={title} onChange={event => setTitle(event.target.value)}
      maxLength={120} required disabled={saving} placeholder="为当前目标商品填写一个任务名称" />
    <div className="actions">
      <span className="muted">仅保存名称和稳定任务身份，不表示素材已齐备。</span>
      <button type="submit" className="primary" disabled={saving}>
        {saving ? '正在保存草稿…' : '保存 Quick-A 草稿'}
      </button>
    </div>
    {error && <p role="alert" className="error">{error}</p>}
  </form>
}
