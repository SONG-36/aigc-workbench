import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, deleteTask, listTasks, renameTask } from '../api/tasks'
import type { TaskDraft } from '../types'
import { Icon } from '../components/Icon'

/** 菜单只操作本任务；删除先确认，写入结果未知时只提示核对，不自动重发。 */
function TaskActions({task,locked,onBusy,onRenamed,onDeleted}:{task:TaskDraft;locked:boolean;
  onBusy:(id:string|null)=>void;onRenamed:(task:TaskDraft)=>void;onDeleted:(id:string)=>void}){
  const [open,setOpen]=useState(false),[mode,setMode]=useState<'rename'|'delete'|null>(null)
  const [title,setTitle]=useState(task.title),[error,setError]=useState(''),[busy,setBusy]=useState(false)
  const mounted=useRef(true),writing=useRef(false)
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[])
  async function save(){
    if(writing.current||locked||!mode||(mode==='delete'&&task.can_delete===false))return
    writing.current=true;setBusy(true);setError('');onBusy(task.id)
    try{
      if(mode==='rename'){
        const saved=await renameTask(task,title)
        if(mounted.current)onRenamed(saved)
      }else{
        await deleteTask(task.id)
        if(mounted.current)onDeleted(task.id)
      }
      if(mounted.current){setMode(null);setOpen(false)}
    }catch(cause){
      if(mounted.current)setError((cause instanceof Error?cause.message:'任务操作失败。')+
        (!(cause instanceof ApiError)||cause.outcome==='unknown'?' 请刷新列表核对结果；不会自动重发。':''))
    }finally{
      writing.current=false;onBusy(null)
      if(mounted.current)setBusy(false)
    }
  }
  return <div className="task-actions">
    <button type="button" aria-label={`任务操作：${task.title}`} aria-haspopup="menu" aria-expanded={open}
      disabled={locked||busy} onClick={()=>{setOpen(!open);setMode(null);setError('')}}>⋯ 操作</button>
    {open&&!mode&&<div role="menu" aria-label={`${task.title}的操作`}>
      <button role="menuitem" onClick={()=>{setTitle(task.title);setMode('rename');setError('')}}>重命名</button>
      <button role="menuitem" className="button-danger" disabled={task.can_delete===false}
        onClick={()=>{setMode('delete');setError('')}}>删除</button>
      {task.can_delete===false&&<p className="notice">{task.delete_blocked_reason||'任务正在生成，暂不能删除。'}</p>}
    </div>}
    {mode==='rename'&&<form aria-label={`重命名任务：${task.title}`} onSubmit={event=>{event.preventDefault();void save()}}>
      <label>任务新名称<input aria-label="任务新名称" value={title} maxLength={120} required disabled={busy}
        onChange={event=>setTitle(event.target.value)}/></label>
      <div className="actions"><button type="submit" disabled={busy||!title.trim()}>保存名称</button>
        <button type="button" disabled={busy} onClick={()=>setMode(null)}>取消重命名</button></div>
    </form>}
    {mode==='delete'&&<section role="dialog" aria-label="确认删除任务">
      <p>确认删除任务“{task.title}”？</p>
      <p className="muted">任务将从列表隐藏；素材、历史版本、Pass 和执行证据仍保留。</p>
      <div className="actions"><button className="button-danger" disabled={busy} onClick={()=>void save()}>确认删除</button>
        <button disabled={busy} onClick={()=>setMode(null)}>取消删除</button></div>
    </section>}
    {error&&<p role="alert" className="error">{error}</p>}
  </div>
}

export function TaskCenter() {
  const [tasks, setTasks] = useState<TaskDraft[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const [busyTask,setBusyTask]=useState<string|null>(null)

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
      <button type="button" onClick={() => setReload(value => value + 1)} disabled={loading||!!busyTask}><Icon name="refresh" className={loading ? 'is-spinning' : ''} />刷新列表</button>
    </div>
      {loading && <p role="status" className="loading-line"><Icon name="refresh" className="is-spinning" />正在读取任务…</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {!loading && !error && <div className="task-list">
        {tasks.length === 0 && <div className="empty"><Icon name="image" /><h3>尚无任务，开始你的第一张商品图。</h3><p>新建 Quick-A，上传商品与图位参考，所有资料都保存在对应任务中。</p></div>}
        {tasks.map(task => <article className="task-row" key={task.id}>
          <div className="task-thumbnail">{task.assets.find(a => a.role === 'product_image')
            ? <img src={task.assets.find(a => a.role === 'product_image')!.url} alt="" /> : <Icon name="image" />}</div>
          <div className="task-info"><div className="task-title-line"><h3><Link to={`/workspace/${task.id}`}>{task.title}</Link></h3><span className="pill pill--neutral">{task.status === 'draft' ? '任务草稿' : task.status}</span></div>
            <p className="task-meta">Quick-A · <time dateTime={task.created_at}>{new Date(task.created_at).toLocaleString()}</time></p>
            <p className="muted">{task.assets.length} 张已保存素材 · {task.slots.filter(s=>!s.archived).length} 个图位</p></div>
          <Link className="secondary button-link" to={`/workspace/${task.id}`}><span>打开 / 继续任务</span><Icon name="arrow" /></Link>
          <TaskActions task={task} locked={!!busyTask} onBusy={setBusyTask}
            onRenamed={saved=>setTasks(previous=>previous.map(t=>t.id===saved.id?saved:t))}
            onDeleted={id=>setTasks(previous=>previous.filter(t=>t.id!==id))}/>
        </article>)}
      </div>}
    </section>
  </div>
}
