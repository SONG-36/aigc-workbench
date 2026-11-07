import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listTasks } from '../api/tasks'
import type { TaskDraft } from '../types'
import { Icon } from '../components/Icon'

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

  return <div className="task-center">
    <div className="heading task-hero"><div><p className="eyebrow">YOUR CREATIVE STUDIO</p>
      <h1>让商品图片，进入下一版。</h1><p className="muted">从真实商品素材到营销视觉，管理每一次创作与修改。</p></div>
      <Link className="primary button-link" to="/workspace/new">＋ 新建 Quick-A<Icon name="arrow" /></Link></div>
    <section className="task-list-section" aria-labelledby="task-list-title"><div className="section-head task-list-heading">
      <div><h2 id="task-list-title">制作任务</h2><p className="muted">打开任务，继续素材准备、制作与审核。</p></div>
      <button type="button" onClick={() => setReload(value => value + 1)} disabled={loading}><Icon name="refresh" className={loading ? 'is-spinning' : ''} />刷新列表</button>
    </div>
      {loading && <p role="status" className="loading-line"><Icon name="refresh" className="is-spinning" />正在读取任务…</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {!loading && !error && <div className="task-list">
        {tasks.length === 0 && <div className="empty"><Icon name="image" /><h3>尚无任务，开始你的第一张商品图。</h3><p>新建 Quick-A，上传商品与图位参考，所有资料都保存在对应任务中。</p></div>}
        {tasks.map(task => <article className="task-row" key={task.id}>
          <div className="task-thumbnail">{task.assets.find(a => a.role === 'product_image')
            ? <img src={task.assets.find(a => a.role === 'product_image')!.url} alt="" /> : <Icon name="image" />}</div>
          <div className="task-info"><h3>{task.title}</h3><span className="pill pill--neutral">Quick-A</span><span className="muted">{task.status === 'draft' ? '任务草稿' : task.status} · {new Date(task.created_at).toLocaleString()}</span>
            <p className="muted">{task.assets.length} 张已保存素材 · {task.slots.filter(s=>!s.archived).length} 个图位</p></div>
          <Link className="secondary button-link" to={`/workspace/${task.id}`}><span>打开 / 继续任务</span><Icon name="arrow" /></Link>
        </article>)}
      </div>}
    </section>
  </div>
}
