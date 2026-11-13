import {useEffect,useRef,useState} from 'react'
import {ApiError} from '../api/tasks'
import {generateImage,getSlotWorkspace,type ExecutionState} from '../api/generation'
import {operationId} from '../utils/operationId'
import {slotTitle} from '../utils/slotTypes'
import type {ImageSlot,TaskDraft} from '../types'

type Item={operation:string;state:ExecutionState|'SUBMITTING';error?:string;unconfirmed?:boolean}
type Items=Record<string,Item>
const active=(item:Item)=>item.unconfirmed||['SUBMITTING','QUEUED','RUNNING'].includes(item.state)
const labels:Record<Item['state'],string>={NOT_STARTED:'尚未开始',SUBMITTING:'正在提交',QUEUED:'排队中',
 RUNNING:'生成中',SUCCEEDED:'成功',FAILED:'失败',OUTCOME_UNKNOWN:'结果待核对'}

/** 只协调现有逐图 API。每个图位独立操作身份；轮询只读，绝不自动重发生成请求。 */
export function BatchGeneration({task,locked,onSelect,onBusy,onSubmitted}:{task:TaskDraft;locked:boolean;
 onSelect:(id:string)=>void;onBusy:(busy:boolean)=>void;onSubmitted:()=>void}){
 const key=`aigc.batch/${task.id}`
 const [items,setItems]=useState<Items>(()=>{try{
  const saved=JSON.parse(sessionStorage.getItem(key)||'{}')
  const restored:Items={}
  if(saved&&typeof saved==='object'&&!Array.isArray(saved))for(const [id,value] of Object.entries(saved)){
   const v=value as Item
   if(v&&typeof v.operation==='string'&&Object.hasOwn(labels,v.state))restored[id]=v
  }
  return restored
 }catch{return {}}})
 const current=useRef(items),mounted=useRef(true),submitting=useRef(false)
 const [epoch,setEpoch]=useState(0)
 const [selected,setSelected]=useState<Set<string>>(new Set())
 const [readiness,setReadiness]=useState<Record<string,{signature:string;canGenerate:boolean;reason:string}>>({})
 // 未就绪图位也必须可见；素材数量、可生成数量与后台并发数是不同概念。
 const slots=task.slots.filter(s=>!s.archived)
 const signature=slots.map(s=>`${s.id}/${s.version}/${s.reference_asset_id}/${s.requirements}/${s.output_size}`).join('|')+task.assets.length+task.overall_requirement?.version
 function blockedReason(slot:ImageSlot){
  if(slot.generation_blocked_reason)return slot.generation_blocked_reason
  if(!task.assets.some(a=>a.role==='product_image'))return '请先上传并保存商品图。'
  if(!slot.reference_asset_id)return '请为本图位上传并绑定营销参考图。'
  if(!slot.inputs_ready)return '本图位素材尚未就绪，请核对商品图及参考绑定。'
  const checked=readiness[slot.id]
  return checked?.signature===signature&&!checked.canGenerate?checked.reason:null
 }
 const valid=slots.filter(s=>!blockedReason(s))
 const selectedValid=valid.filter(s=>selected.has(s.id))
 const busy=Object.values(items).some(active)
 function publish(next:Items){current.current=next;try{sessionStorage.setItem(key,JSON.stringify(next))}catch{/* 页面内身份仍保留。 */}
  if(mounted.current)setItems(next)
 }
 function update(id:string,patch:Partial<Item>){const previous=current.current[id];if(previous)publish({...current.current,[id]:{...previous,...patch}})}
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[])
 useEffect(()=>onBusy(busy),[busy,onBusy])
 useEffect(()=>{
  let cancelled=false,timer:ReturnType<typeof setTimeout>|undefined
  async function read(){
   await Promise.allSettled(slots.map(async slot=>{
    const expected=current.current[slot.id]?.operation
    try{
     const data=await getSlotWorkspace(task.id,slot.id)
     if(cancelled||current.current[slot.id]?.operation!==expected)return
     setReadiness(previous=>({...previous,[slot.id]:{signature,canGenerate:data.readiness.can_generate&&!!data.readiness.quote_token,
      reason:data.readiness.reason||'当前图位尚未满足生成条件。'}}))
     const item=current.current[slot.id],execution=item?.operation?data.executions.find(e=>e.operation_id===item.operation):data.executions[0]
     if(execution){
      publish({...current.current,[slot.id]:{operation:execution.operation_id,state:execution.state,error:execution.error?.message}})
     }else if(!item){publish({...current.current,[slot.id]:{operation:'',state:data.state}})}
     // 未找到结果未知的提交时继续查询原身份，不创建另一次生成。
    }catch(cause){if(!cancelled&&current.current[slot.id]?.operation===expected){
     setReadiness(previous=>({...previous,[slot.id]:{signature,canGenerate:false,reason:'生成条件读取失败，请刷新页面后核对。'}}))
     update(slot.id,{error:cause instanceof Error?cause.message:'读取状态失败'})}}
   }))
   if(!cancelled&&Object.values(current.current).some(active))timer=setTimeout(()=>void read(),1500)
  }
  void read()
  return()=>{cancelled=true;clearTimeout(timer)}
 },[task.id,signature,epoch])
 async function start(onlySelected=false){
  // N 来自提交瞬间的有效图位；只选部分时，未选中的历史状态也必须保留。
  const targets=onlySelected?selectedValid:valid
  if(locked||submitting.current||Object.values(current.current).some(active)||!targets.length)return
  submitting.current=true
  publish({...current.current,...Object.fromEntries(targets.map(slot=>[slot.id,{operation:operationId(),state:'SUBMITTING' as const}]))})
  setEpoch(v=>v+1)
  await Promise.allSettled(targets.map(async slot=>{
   const operation=current.current[slot.id].operation
   let posted=false
   try{
    const data=await getSlotWorkspace(task.id,slot.id)
    if(!data.readiness.can_generate||!data.readiness.quote_token){
     const existing=data.executions[0]
     update(slot.id,{operation:existing?.operation_id||operation,state:existing?.state||'FAILED',
      error:data.readiness.reason,unconfirmed:false});return
    }
    posted=true
    await generateImage(task.id,slot.id,operation,data.readiness.quote_token)
    update(slot.id,{state:'QUEUED',error:undefined,unconfirmed:false})
   }catch(cause){
    const unknown=posted&&(!(cause instanceof ApiError)||cause.outcome==='unknown')
    update(slot.id,{state:unknown?'OUTCOME_UNKNOWN':'FAILED',unconfirmed:unknown,
     error:cause instanceof Error?cause.message:'提交失败，请查询状态'})
   }
  }))
  submitting.current=false
  if(mounted.current){setEpoch(v=>v+1);onSubmitted()}
 }
 return <section className="batch-generation" aria-label="全部图位生成">
  <p className="muted">共 {slots.length} 个图位 · 可生成 {valid.length} 个 · 暂不可生成 {slots.length-valid.length} 个。提交数量按图位条件计算，其余已提交图片会在后台排队执行。</p>
  <div className="output-controls"><button className="primary" disabled={locked||busy||!valid.length}
   onClick={event=>{if(event.detail<2)void start()}}>生成全部有效图位（{valid.length}）</button>
   <button disabled={locked||busy||!selectedValid.length}
    onClick={event=>{if(event.detail<2)void start(true)}}>生成选中图位（{selectedValid.length}）</button>
   <span>{busy?'后台逐图执行中，完成的图片可独立查看和审核。':'每个图位独立生成，单图失败不撤回其他结果。'}</span></div>
  {locked&&<p className="notice">请先保存并核对整体及各图位配置。</p>}
  <ul>{slots.map(slot=>{const item=items[slot.id],reason=blockedReason(slot),label=slotTitle(slot.kind,task.slots.filter(s=>s.kind===slot.kind).findIndex(s=>s.id===slot.id)+1)
   return <li key={slot.id}><label className="batch-slot-choice"><input type="checkbox" aria-label={`选择${label}`}
    disabled={locked||busy||!!reason} checked={selected.has(slot.id)} onChange={event=>{
     const checked=event.target.checked
     setSelected(previous=>{const next=new Set(previous);if(checked)next.add(slot.id);else next.delete(slot.id);return next})
    }}/><span>选择</span></label><button className="button-quiet" onClick={()=>onSelect(slot.id)}>查看{label}</button>
    <span role="status">{item?labels[item.state]:'读取中'}</span>{reason&&<span className="notice">暂不可生成：{reason}</span>}
    {item?.error&&<span className="error"> · {item.error}</span>}</li>})}</ul>
 </section>
}
