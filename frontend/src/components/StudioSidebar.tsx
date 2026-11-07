import type {ReactNode} from 'react'
import type {TaskDraft} from '../types'
import {slotTitle} from '../utils/slotTypes'
import {Icon} from './Icon'
import type {SlotPresentation} from '../utils/presentation'

export function StudioSidebar({task,selectedId,status,onSelect,children}:{task:TaskDraft;selectedId?:string;status:SlotPresentation|null;onSelect:(id:string)=>void;children:ReactNode}) {
 return <aside className="studio-sidebar"><p className="eyebrow">IMAGE SLOTS / 图位</p>
  <nav className="output-slots" aria-label="选择制作图位">{task.slots.filter(s=>!s.archived).map(s=>{
   const reference=task.assets.find(a=>a.id===s.reference_asset_id)
   const current=selectedId===s.id&&status?.slotId===s.id
   return <button className="slot-nav-button" aria-label={slotTitle(s.kind,task.slots.filter(x=>x.kind===s.kind).findIndex(x=>x.id===s.id)+1)} aria-describedby={`${s.id}-reference${current?` ${s.id}-status`:''}`} key={s.id} aria-pressed={selectedId===s.id} onClick={()=>onSelect(s.id)}>
    <span className="slot-nav-preview">{reference?<img src={reference.url} alt=""/>:<Icon name="image"/>}</span>
    <span><strong>{slotTitle(s.kind,task.slots.filter(x=>x.kind===s.kind).findIndex(x=>x.id===s.id)+1)}</strong><small id={`${s.id}-reference`}>{reference?'参考已提供':'待上传参考'}</small>{current&&<span id={`${s.id}-status`} className={`pill pill--${status.tone}`}>{status.label}</span>}</span>
   </button>
  })}</nav>{children}
 </aside>
}
