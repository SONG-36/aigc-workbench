import {operationId} from '../utils/operationId'
import {useEffect,useState} from 'react'
import {ApiError,configureSlot,createSlot,getTask,removeSlot} from '../api/tasks'
import type {WriteAction} from '../hooks/useWriteAction'
import {useSlotReference} from '../hooks/useSlotReference'
import {OUTPUT_SIZES,type ImageSlot,type OutputSize,type SlotKind,type TaskDraft} from '../types'
import {SLOT_KINDS,fixedSlot,slotTitle} from '../utils/slotTypes'
import {ReferenceImage} from './ReferenceImage'
import {FilePicker} from './FilePicker'
export {SLOT_KINDS} from '../utils/slotTypes'
function AddSlot({task,kind,label,write,onSaved,onSelect}:{task:TaskDraft;kind:SlotKind;label:string;write:WriteAction;onSaved:(t:TaskDraft)=>void;onSelect?:(id:string)=>void}){
 const [operation,setOperation]=useState(operationId)
 return <button type="button" className="add-slot" disabled={write.locked} onClick={()=>void write.run(()=>createSlot(task.id,kind,operation),next=>{onSaved(next);const added=(next as TaskDraft&{created_slot_id?:string}).created_slot_id;if(added&&next.slots.some(s=>s.id===added))onSelect?.(added);setOperation(operationId())})}>＋ 添加{label}</button>
}
function SlotEditor({task,slot,label,write,onSaved,onDirty,hidden}:{hidden?:boolean;task:TaskDraft;slot:ImageSlot;label:string;write:WriteAction;onSaved:(t:TaskDraft)=>void;onDirty?:(id:string,dirty:boolean)=>void}){
 const [size,setSize]=useState(slot.output_size),[requirements,setRequirements]=useState(slot.requirements),[preview,setPreview]=useState<string|null>(null)
 const reference=useSlotReference(task,slot,write,onSaved)
 const [conflict,setConflict]=useState(false)
 async function save(values:Pick<ImageSlot,'reference_asset_id'|'output_size'|'requirements'>){
  try{return await configureSlot(task.id,slot,values)}catch(cause){if(cause instanceof ApiError&&cause.status===409)setConflict(true);throw cause}
 }
 const [removeOperation]=useState(operationId),[confirmRemoval,setConfirmRemoval]=useState(false)
 useEffect(()=>{setSize(slot.output_size);setRequirements(slot.requirements)},[slot.reference_asset_id,slot.output_size,slot.requirements,slot.version])
 useEffect(()=>{if(!reference.file||typeof URL.createObjectURL!=='function'){setPreview(null);return};const url=URL.createObjectURL(reference.file);setPreview(url);return()=>URL.revokeObjectURL(url)},[reference.file])
 const selected=task.assets.find(a=>a.id===slot.reference_asset_id),dirty=size!==slot.output_size||requirements!==slot.requirements||!!reference.pending
 useEffect(()=>{onDirty?.(slot.id,dirty)},[slot.id,dirty,onDirty]);useEffect(()=>()=>onDirty?.(slot.id,false),[slot.id,onDirty])
 return <form className="slot-shell slot-editor" hidden={hidden} data-slot-id={slot.id} data-slot-kind={slot.kind} onSubmit={e=>{e.preventDefault();void write.run(()=>save({reference_asset_id:slot.reference_asset_id,output_size:size,requirements}),onSaved)}}>
  <div className="slot-card-header"><h3>{label} · 制作设置</h3>
   {!fixedSlot(slot.kind)&&<button className="button-quiet button-danger" type="button" disabled={write.locked||!!reference.pending} onClick={()=>setConfirmRemoval(true)}>删除{label}</button>}</div>
  {confirmRemoval&&<div className="notice"><p>移除{label}？原素材、历史图片和审核仍保留，其他图位不受影响。</p><div className="actions"><button className="button-danger" type="button" disabled={write.locked} onClick={()=>void write.run(()=>removeSlot(task.id,slot.id,removeOperation),onSaved)}>确认删除{label}</button><button type="button" onClick={()=>setConfirmRemoval(false)}>保留图位</button></div></div>}
  <p className="muted">{dirty?'有未保存配置':'图位配置已保存'} · {slot.reference_asset_id?'参考已保存并绑定到当前图位':'尚未上传本图位参考'}</p>
  {(conflict||reference.conflict)&&<div role="alert" className="notice"><p>图位数据已变化，本页修改未覆盖服务器。请重新读取并核对。</p><button type="button" disabled={write.busy} onClick={()=>void write.run(()=>getTask(task.id),next=>{reference.abandon();setConflict(false);onSaved(next)})}>重新读取服务器配置（放弃本页未保存修改）</button></div>}
  {slot.kind==='dimensions'&&<p className="notice">{task.mode==='listing'?'尺寸图需运营确认、已标注真实尺寸的专用白底商品素材；不继承设计参考的尺寸，也不能由像素推断。':'尺寸图使用商品图和本图位营销参考直接生成；保留参考已有的尺寸数值、单位、文字和标注关系，不编造新尺寸。'}</p>}
  {selected?<ReferenceImage url={selected.url} label={label}/>:<div className="reference-empty">{task.mode==='listing'?'设计参考可选；未提供时按商品资料直接设计。':'直接在本图位上传参考，无需到其他区域绑定'}</div>}
  <FilePicker label={`${label}参考图片`} action={selected?'替换参考图':'上传本图位参考'} compact={!!selected} disabled={write.locked||!!reference.pending} onSelect={file=>reference.choose(file,size,requirements)}/>
  {reference.message&&<p role="status">{reference.message}</p>}
  {reference.pending&&<div className="pending-asset"><p>{reference.pending.assetId?'图片已上传，但尚未确认绑定成功。':'本地图像待确认；当前绑定仍以服务器为准。'}</p>
   {preview&&<ReferenceImage url={preview} label={`${label}待绑定`}/>}
   {reference.rejected&&<button type="button" disabled={write.locked} onClick={reference.reset}>重新选择{label}参考</button>}
   {!reference.file&&!reference.pending.assetId&&<FilePicker label={`${label}恢复参考图片`} action="恢复原始文件" disabled={write.locked} compact onSelect={reference.resume}/>}
   <button type="button" disabled={write.locked} onClick={reference.retry}>继续上传并绑定{label}</button></div>}
  {selected&&<button className="remove-reference button-quiet" type="button" disabled={write.locked||!!reference.pending} onClick={()=>void write.run(()=>save({reference_asset_id:null,output_size:size,requirements}),onSaved)}>移除{label}参考</button>}
  <fieldset disabled={write.locked||!!reference.pending}><label>输出尺寸<select value={size} onChange={e=>setSize(e.target.value as OutputSize)}>
   {OUTPUT_SIZES.map(value=><option key={value} value={value}>{value==='1536x1536'?'1536 × 1536（正方形）':value}</option>)}
  </select></label>
  {size==='1536x1536'&&<p className="muted">以 1024×1024 原生方图生成，再用 LANCZOS 等比例放大至 1536×1536；不裁切。</p>}
  <label>补充制作要求<textarea maxLength={2000} rows={2} value={requirements} onChange={e=>setRequirements(e.target.value)}/></label>
  <button type="submit">保存{label}配置</button></fieldset>
 </form>
}
export function SlotPanel({task,write,onSaved,onDirty,selectedId}:{task:TaskDraft;write:WriteAction;onSaved:(t:TaskDraft)=>void;onDirty?:(id:string,dirty:boolean)=>void;selectedId?:string}){
 return <section aria-label="营销参考和图位配置区域">
  {task.slots.filter(s=>!s.archived).map(s=><SlotEditor key={s.id} task={task} slot={s} hidden={s.id!==selectedId}
   label={slotTitle(s.kind,task.slots.filter(x=>x.kind===s.kind).findIndex(x=>x.id===s.id)+1)} write={write} onSaved={onSaved} onDirty={onDirty}/>)}
  {!selectedId&&<p className="muted">先添加图位，再直接上传这个图位的参考。</p>}
 </section>
}

export function SlotTools({task,write,onSaved,onSelect}:{task:TaskDraft;write:WriteAction;onSaved:(t:TaskDraft)=>void;onSelect:(id:string)=>void}){
 return <div className="slot-tools" aria-label="图位管理">
  {SLOT_KINDS.filter(([kind])=>!fixedSlot(kind)||!task.slots.some(s=>s.kind===kind)).map(([kind,label])=>
   <AddSlot key={kind} task={task} kind={kind} label={label} write={write} onSaved={onSaved} onSelect={onSelect}/>)}
  {task.slots.some(s=>s.archived)&&<details><summary>已移除图位（历史保留）</summary>{task.slots.filter(s=>s.archived).map(s=><p key={s.id}>{slotTitle(s.kind,task.slots.filter(x=>x.kind===s.kind).findIndex(x=>x.id===s.id)+1)} · 历史图片、审核与交付保留</p>)}</details>}
 </div>
}
