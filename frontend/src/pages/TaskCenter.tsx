import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listTasks } from '../api/tasks'
import type { TaskDraft } from '../types'

export function TaskCenter() {
  const [tasks, setTasks] = useState<TaskDraft[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    listTasks(controller.signal).then(data => {
      if (!controller.signal.aborted) setTasks(data)
    }).catch(cause => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '读取失败。')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [reload])

  return <>
    <div className="heading task-hero"><div><p className="eyebrow">COMMERCE IMAGE WORKSPACE</p>
      <h1>让商品图片，进入下一版。</h1><p className="muted">在独立任务中保存资料，继续下一步工作。</p></div>
      <Link className="primary button-link" to="/workspace/new">＋ 创建 Quick-A 草稿</Link></div>
    <section aria-labelledby="task-list-title"><div className="section-head task-list-heading">
      <h2 id="task-list-title">制作任务</h2><p className="muted">打开已有任务，恢复服务器保存的草稿。</p>
      <button type="button" onClick={() => setReload(value => value + 1)} disabled={loading}>刷新列表</button>
    </div>
      {loading && <p role="status">正在读取任务…</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {!loading && !error && <div className="task-list">
        {tasks.length === 0 && <p className="empty">尚无任务。可创建 Quick-A 草稿并保存素材；图片制作尚未开放。</p>}
        {tasks.map(task => <article className="task-row" key={task.id}>
          <div><h3>{task.title}</h3><p className="muted">Quick-A｜同款快速改版 · {new Date(task.created_at).toLocaleString()}</p>
            <p>{task.assets.length} 张已保存素材 · {task.slots.length} 个图位 · 图片生成未开放</p></div>
          <Link className="secondary button-link" to={`/workspace/${task.id}`}>打开 / 继续任务</Link>
        </article>)}
      </div>}
    </section>
  </>
}
