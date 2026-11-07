import { useCallback, useEffect, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { getTask } from '../api/tasks'
import { TaskForm } from '../components/TaskForm'
import { WorkspaceShell } from '../components/WorkspaceShell'
import { AssetUploader } from '../components/AssetUploader'
import { SlotPanel, SlotTools } from '../components/SlotPanel'
import { WriteFeedback } from '../components/WriteFeedback'
import { useWriteAction } from '../hooks/useWriteAction'
import { OverallRequirements } from '../components/OverallRequirements'
import { GenerationWorkspace } from '../components/GenerationWorkspace'
import { StudioSidebar } from '../components/StudioSidebar'
import { Icon } from '../components/Icon'
import type {SlotPresentation} from '../utils/presentation'
import type { TaskDraft } from '../types'

function SavedWorkspace({ initial }: { initial: TaskDraft }) {
  const [task, setTask] = useState(initial)
  const [slotStatus,setSlotStatus] = useState<SlotPresentation|null>(null)
  const [search,setSearch] = useSearchParams()
  const selected = task.slots.find(s=>!s.archived&&s.id===search.get('slot')) || task.slots.find(s=>!s.archived)
  const selectSlot = (id:string) => setSearch(previous=>{const next=new URLSearchParams(previous);next.set('slot',id);return next},{replace:true})
  const [inputOpen,setInputOpen] = useState(()=>typeof window.matchMedia!=='function'||!window.matchMedia('(max-width:1100px)').matches)
  useEffect(()=>{
    if(typeof window.matchMedia!=='function')return
    const media=window.matchMedia('(max-width:1100px)'),change=()=>setInputOpen(!media.matches)
    media.addEventListener('change',change);return()=>media.removeEventListener('change',change)
  },[])
  // Serializes writes within this workspace; late snapshots cannot overwrite newer writes.
  const write = useWriteAction()
  const [overallDirty, setOverallDirty] = useState(false)
  const [unsavedSlots, setUnsavedSlots] = useState<Set<string>>(new Set())
  const onDirty = useCallback((slotId: string, dirty: boolean) => setUnsavedSlots(previous => {
    if (previous.has(slotId) === dirty) return previous
    const next = new Set(previous)
    if (dirty) next.add(slotId); else next.delete(slotId)
    return next
  }), [])
  return <WorkspaceShell task={task}
    feedback={<WriteFeedback write={write}/>}
    navigation={<StudioSidebar task={task} selectedId={selected?.id} status={slotStatus} onSelect={selectSlot}><SlotTools task={task} write={write} onSaved={setTask} onSelect={selectSlot}/></StudioSidebar>}
    output={<GenerationWorkspace task={task} slot={selected} onStatus={setSlotStatus} locked={write.locked || overallDirty} unsavedSlots={unsavedSlots} />}>
    <details id="input-panel" className="studio-settings" open={inputOpen} onToggle={event=>setInputOpen(event.currentTarget.open)}>
      <summary><Icon name="image"/><strong>素材与制作设置</strong><span className="settings-toggle">{inputOpen?'收起':'展开'}</span></summary>
      <div className="input-content">
        <AssetUploader task={task} role="product_image" write={write} onSaved={setTask}/>
        <SlotPanel task={task} selectedId={selected?.id} write={write} onSaved={setTask} onDirty={onDirty}/>
        <OverallRequirements task={task} write={write} onSaved={setTask} onDirty={setOverallDirty}/>
        <details className="metadata-details"><summary>任务资料</summary><dl><dt>Task ID</dt><dd data-testid="task-id">{task.id}</dd><dt>创建时间</dt><dd>{new Date(task.created_at).toLocaleString()}</dd></dl>
          <p>{task.materials_ready?'已有图位基础素材齐备；生成条件以当前图位的服务器检查为准。':'尚未提供商品图与营销参考，或图位绑定未齐备；不可生成。'}</p></details>
      </div>
    </details>
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
