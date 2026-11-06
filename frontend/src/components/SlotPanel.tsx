import {operationId} from '../utils/operationId'
import {useEffect,useState} from 'react'
import {configureSlot,createSlot,removeSlot} from '../api/tasks'
import type {WriteAction} from '../hooks/useWriteAction'
import {useSlotReference} from '../hooks/useSlotReference'
import {OUTPUT_SIZES,type ImageSlot,type OutputSize,type SlotKind,type TaskDraft} from '../types'
import {SLOT_KINDS,fixedSlot,slotTitle} from '../utils/slotTypes'
import {ReferenceImage} from './ReferenceImage'
export {SLOT_KINDS} from '../utils/slotTypes'
function AddSlot({task,kind,label,write,onSaved}:{task:TaskDraft;kind:SlotKind;label:string;write:WriteAction;onSaved:(t:TaskDraft)=>void}){
 const [operation,setOperation]=useState(operationId)
 return <button type="button" className="add-slot" disabled={write.locked} onClick={()=>void write.run(()=>createSlot(task.id,kind,operation),next=>{onSaved(next);setOperation(operationId())})}>＋ 添加{label}</button>
}
function SlotEditor({task,slot,label,write,onSaved,onDirty}:{task:TaskDraft;slot:ImageSlot;label:string;write:WriteAction;onSaved:(t:TaskDraft)=>void;onDirty?:(id:string,dirty:boolean)=>void}){
 const [size,setSize]=useState(slot.output_size),[requirements,setRequirements]=useState(slot.requirements),[preview,setPreview]=useState<string|null>(null)
 const reference=useSlotReference(task,slot,write,onSaved)
 const [removeOperation]=useState(operationId)
 useEffect(()=>{setSize(slot.output_size);setRequirements(slot.requirements)},[slot.reference_asset_id,slot.output_size,slot.requirements])
 useEffect(()=>{if(!reference.file||typeof URL.createObjectURL!=='function'){setPreview(null);return};const url=URL.createObjectURL(reference.file);setPreview(url);return()=>URL.revokeObjectURL(url)},[reference.file])
 const selected=task.assets.find(a=>a.id===slot.reference_asset_id),dirty=size!==slot.output_size||requirements!==slot.requirements||!!reference.pending
 useEffect(()=>{onDirty?.(slot.id,dirty)},[slot.id,dirty,onDirty]);useEffect(()=>()=>onDirty?.(slot.id,false),[slot.id,onDirty])
 return <form className="slot-shell" data-slot-id={slot.id} data-slot-kind={slot.kind} onSubmit={e=>{e.preventDefault();void write.run(()=>configureSlot(task.id,slot,{reference_asset_id:slot.reference_asset_id,output_size:size,requirements}),onSaved)}}>
  <h4>{label}</h4>
  {!fixedSlot(slot.kind)&&<button type="button" disabled={write.locked||!!reference.pending} onClick={()=>void write.run(()=>removeSlot(task.id,slot.id,removeOperation),onSaved)}>删除{label}</button>}
  <p className="muted">{dirty?'有未保存配置':'图位配置已保存'} · {slot.reference_asset_id?'参考已保存并绑定到当前图位':'尚未上传本图位参考'}</p>
  {slot.kind==='dimensions'&&<p className="notice">请上传已标注真实物理尺寸的专用白底素材。当前仅保存，专项确认与制作未开放；不会由输出像素尺寸推断商品尺寸。</p>}
  {selected?<ReferenceImage url={selected.url} label={label}/>:<div className="reference-empty">直接在本图位上传参考，无需到其他区域绑定</div>}
  <label className="upload-label">{selected?'替换参考图':'上传本图位参考'}<input type="file" aria-label={`${label}参考图片`} accept="image/png,image/jpeg,image/webp" disabled={write.locked||!!reference.pending}
   onChange={e=>{const file=e.target.files?.[0];if(file)reference.choose(file,size,requirements);e.target.value=''}}/></label>
  {reference.message&&<p role="status">{reference.message}</p>}
  {reference.pending&&<div className="pending-asset"><p>{reference.pending.assetId?'图片已上传，但尚未确认绑定成功。':'本地图像待确认；当前绑定仍以服务器为准。'}</p>
   {preview&&<ReferenceImage url={preview} label={`${label}待绑定`}/>}
   {reference.rejected&&<button type="button" disabled={write.locked} onClick={reference.reset}>重新选择{label}参考</button>}
   {!reference.file&&!reference.pending.assetId&&<label>恢复原始文件<input type="file" aria-label={`${label}恢复参考图片`} accept="image/png,image/jpeg,image/webp" disabled={write.locked}
    onChange={e=>{if(e.target.files?.[0])reference.resume(e.target.files[0]);e.target.value=''}}/></label>}
   <button type="button" disabled={write.locked} onClick={reference.retry}>继续上传并绑定{label}</button></div>}
  {selected&&<button type="button" disabled={write.locked||!!reference.pending} onClick={()=>void write.run(()=>configureSlot(task.id,slot,{reference_asset_id:null,output_size:size,requirements}),onSaved)}>移除{label}参考</button>}
  <fieldset disabled={write.locked||!!reference.pending}><label>输出尺寸<select value={size} onChange={e=>setSize(e.target.value as OutputSize)}>
   {OUTPUT_SIZES.map(value=><option key={value} value={value}>{value==='1536x1536'?'1536 × 1536（正方形）':value}</option>)}
  </select></label>
  {size==='1536x1536'&&<p className="muted">以 1024×1024 原生方图生成，再用 LANCZOS 等比例放大至 1536×1536；不裁切。</p>}
  <label>补充制作要求<textarea maxLength={2000} rows={2} value={requirements} onChange={e=>setRequirements(e.target.value)}/></label>
  <button type="submit">保存{label}配置</button></fieldset>
 </form>
}
export function SlotPanel({task,write,onSaved,onDirty}:{task:TaskDraft;write:WriteAction;onSaved:(t:TaskDraft)=>void;onDirty?:(id:string,dirty:boolean)=>void}){
 function group(kind:SlotKind,label:string){const all=task.slots.filter(s=>s.kind===kind),slots=all.filter(s=>!s.archived)
  return <div className="slot-group" key={kind}><h4>{label} · {slots.length} 个</h4><div className={fixedSlot(kind)?'fixed-slot':'slot-grid'}>
   {slots.map(s=><SlotEditor key={s.id} task={task} slot={s} label={slotTitle(kind,all.findIndex(x=>x.id===s.id)+1)} write={write} onSaved={onSaved} onDirty={onDirty}/>)}
   {(!fixedSlot(kind)||!all.length)&&<AddSlot task={task} kind={kind} label={label} write={write} onSaved={onSaved}/>}</div></div>}
 return <section aria-label="营销参考和图位配置区域"><h3>按图位直接上传营销参考</h3><p className="muted">上传或替换后自动保存并绑定当前图位；原素材、历史版本及 Pass 保留。移除仅取消当前绑定。</p>
  <div className="fixed-slots">{SLOT_KINDS.filter(([kind])=>fixedSlot(kind)).map(([kind,label])=>group(kind,label))}</div>
  {SLOT_KINDS.filter(([kind])=>!fixedSlot(kind)).map(([kind,label])=>group(kind,label))}
  {task.slots.some(s=>s.archived)&&<details><summary>已移除图位（历史保留）</summary>{task.slots.filter(s=>s.archived).map(s=><p key={s.id}>{slotTitle(s.kind,task.slots.filter(x=>x.kind===s.kind).findIndex(x=>x.id===s.id)+1)} · {s.id} · 历史图片、审核与交付保留</p>)}</details>}
 </section>
}
