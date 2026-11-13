import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { TaskDraft } from '../types'
import { Icon } from './Icon'

export function WorkspaceShell({ task, children, output, navigation, feedback, mode='quick_a', onModeChange }: {
  task: TaskDraft | null; children?: ReactNode; output?: ReactNode; navigation?: ReactNode; feedback?: ReactNode;
  mode?:TaskDraft['mode'];onModeChange?:(mode:TaskDraft['mode'])=>void
}) {
  const quickB=(task?.mode||mode)==='quick_b',listing=(task?.mode||mode)==='listing'
  return <div className={task ? 'saved-workspace' : 'new-task-layout'}>
    {!task&&<div className="breadcrumb"><Link to="/" aria-label="返回任务中心">任务中心</Link><Icon name="chevron" /><span>新建 {listing?'Listing':quickB?'Quick-B':'Quick-A'}</span></div>}
    <div className="heading workspace-heading"><div>{!task&&<p className="eyebrow">CREATE A NEW TASK</p>}
      <h1>{task?.title || '从真实商品，开始创作。'}</h1><p className="muted">{task ? '素材、参考与每一版成果，在同一个工作台。' : listing?'上传商品和文字资料，设计参考可选，保存后进入工作台。':'上传商品，为每个图位提供参考，保存后进入工作台。'}</p>
    </div><span className="pill">{listing?'Listing · 商品资料制图':quickB?'Quick-B · 换主体快速改版':'Quick-A · 同款快速改版'}</span></div>
    {feedback}
    {task ? <div className="studio-layout">{navigation}{children}<section id="output" aria-label="逐图制作与审核">{output}</section></div>
      : <><div className="mode-bar"><label htmlFor="task-mode">工作模式</label><select id="task-mode" value={mode} disabled={!onModeChange}
        onChange={event=>onModeChange?.(event.target.value as TaskDraft['mode'])}>
        <option value="quick_a">Quick-A｜同款快速改版</option><option value="quick_b">Quick-B｜换主体快速改版</option><option value="listing">Listing｜商品资料制图</option>
      </select><span>{listing?'实际商品图 + 商品文字资料，设计参考可选。':quickB?'实际商品图 + 不同外观商品的营销参考，完整替换主体与配件。':'实际商品图 + 同款营销参考，无需 Listing。'}</span></div>{children}</>}
  </div>
}
