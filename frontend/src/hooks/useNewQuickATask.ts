import {useEffect,useRef,useState} from 'react'
import {useNavigate} from 'react-router-dom'
import {ApiError,configureSlot,createTaskDraft,createSlot,getTask,uploadAsset} from '../api/tasks'
import type {OutputSize,SlotKind,TaskDraft} from '../types'
import {operationId} from '../utils/operationId'
import {newTaskFiles,type CachedFiles} from '../utils/newTaskFiles'
import {fixedSlot,slotTitle} from '../utils/slotTypes'
type Info=Pick<File,'name'|'size'|'type'|'lastModified'>
export type DraftSlot={key:string;kind:SlotKind;ordinal:number;active:boolean;size:OutputSize;requirements:string;
 uploadOp:string;slotOp:string;slotId:string|null;referenceId:string|null;fileInfo:Info|null;configVersion?:number}
type Draft={title:string;overallRequirements:string;listingText:string;supplementalText:string;operationId:string;productOp:string;started:boolean;
 taskId:string|null;productId:string|null;productInfo:Info|null;slots:DraftSlot[]}
const info=(f:File|null):Info|null=>f&&({name:f.name,size:f.size,type:f.type,lastModified:f.lastModified})
function makeSlot(kind:SlotKind,ordinal=1):DraftSlot{return {key:operationId(),kind,ordinal,active:!fixedSlot(kind),size:'1024x1536',requirements:'',
 uploadOp:operationId(),slotOp:operationId(),slotId:null,referenceId:null,fileInfo:null}}
function restore(key:string,mode:TaskDraft['mode']):Draft {
 const fresh:Draft={title:'',overallRequirements:'',listingText:'',supplementalText:'',operationId:operationId(),productOp:operationId(),started:false,
 taskId:null,productId:null,productInfo:null,slots:[makeSlot('main'),makeSlot('dimensions'),makeSlot('comparison')]}
 if(mode==='listing')fresh.slots[0].active=true
 try {
  const old=JSON.parse(sessionStorage.getItem(key)||'null')
  if(typeof old?.title==='string'&&typeof old?.operationId==='string'&&(old.mode??'quick_a')===mode) {
   if(Array.isArray(old.slots))return {...fresh,...old}
   // Resume the earlier single-slot intent with its original operation identities.
   const slot={...makeSlot(old.kind||'main'),key:old.slotOp||fresh.slots[0].key,active:!!old.started||!!old.fileInfo?.reference,
    size:old.size||'1024x1536',requirements:old.requirements||'',uploadOp:old.referenceOp||operationId(),
    slotOp:old.slotOp||operationId(),slotId:old.slotId||null,referenceId:old.referenceId||null,fileInfo:old.fileInfo?.reference||null}
   return {...fresh,...old,productInfo:old.fileInfo?.product||null,slots:[slot,...fresh.slots.filter(s=>s.kind!==slot.kind)]}
  }
 }catch{/* Unreadable intents never auto-submit. */}
 return fresh
}
export function useNewQuickATask(mode:TaskDraft['mode']='quick_a'){
 // 保存模式分开暂存；切换新建入口不能把另一模式的待上传文件或 Operation 带入。
 const key=mode==='quick_a'?'aigc.pending-task-creation':mode==='quick_b'?'aigc.pending-quick-b-task-creation':'aigc.pending-listing-task-creation'
 function remember(d:Draft|null){try {d?sessionStorage.setItem(key,JSON.stringify({...d,mode})):sessionStorage.removeItem(key)}catch{/* Keep current page state and unload warning. */}}
 const [draft,setDraft]=useState(()=>restore(key,mode)),current=useRef(draft)
 const [files,setFiles]=useState<CachedFiles>({product:null,reference:null,references:{}}),selected=useRef(files)
 const [task,setTask]=useState<TaskDraft|null>(null),[busy,setBusy]=useState(false),[hydrating,setHydrating]=useState(true)
 const [stage,setStage]=useState(''),[warning,setWarning]=useState('')
 const [error,setError]=useState<{stage:string;message:string;unknown:boolean;target?:string}|null>(null)
 const active=useRef(true),inFlight=useRef(false),queue=useRef(Promise.resolve());const navigate=useNavigate()
 function checkpoint(patch:Partial<Draft>){current.current={...current.current,...patch};remember(current.current);if(active.current)setDraft(current.current)}
 function changeSlot(id:string,patch:Partial<DraftSlot>){checkpoint({slots:current.current.slots.map(s=>s.key===id?{...s,...patch}:s)})}
 useEffect(()=>{
  active.current=true;const initial=current.current
  void(async()=>{
   try {
    const cached=await newTaskFiles(initial.operationId)
    if(cached&&active.current){const references:Record<string,File|null>={}
     for(const s of initial.slots){const file=cached.references?.[s.key]||(!cached.references?cached.reference:null)
      if(JSON.stringify(info(file))===JSON.stringify(s.fileInfo))references[s.key]=file}
     const restored={product:JSON.stringify(info(cached.product))===JSON.stringify(initial.productInfo)?cached.product:null,reference:null,references}
     selected.current=restored;setFiles(restored)
    }
   }catch{/* Missing files remain explicitly marked unsaved. */}
   if(initial.taskId){try{const saved=await getTask(initial.taskId);if(active.current)setTask(saved)}catch{if(active.current)setWarning('已保存任务暂时无法读取；继续保存会恢复同一 Task，不创建另一任务。')}}
   if(active.current)setHydrating(false)
  })()
  const guard=(event:BeforeUnloadEvent)=>{if(selected.current.product||Object.values(selected.current.references||{}).some(Boolean)||current.current.started){event.preventDefault();event.returnValue=''}}
  window.addEventListener('beforeunload',guard);return()=>{active.current=false;window.removeEventListener('beforeunload',guard)}
 },[])
 function edit(patch:Partial<Pick<Draft,'title'|'overallRequirements'|'listingText'|'supplementalText'>>){if(!current.current.started&&!inFlight.current)checkpoint(patch)}
 function editSlot(id:string,patch:Partial<Pick<DraftSlot,'size'|'requirements'>>){if(!current.current.started&&!inFlight.current)changeSlot(id,{...patch,active:true})}
 function enableSlot(id:string,enabled:boolean){if(mode==='listing'&&!current.current.started&&!inFlight.current)changeSlot(id,{active:enabled})}
 function addSlot(kind:SlotKind){if(current.current.started||inFlight.current)return;checkpoint({slots:[...current.current.slots,makeSlot(kind,Math.max(0,...current.current.slots.filter(s=>s.kind===kind).map(s=>s.ordinal))+1)]})}
 function removeSlot(id:string){if(current.current.started||inFlight.current)return;checkpoint({slots:current.current.slots.filter(s=>s.key!==id)});selected.current={...selected.current,references:{...selected.current.references,[id]:null}};setFiles(selected.current);cache()}
 function cache(){const data={...selected.current,references:{...selected.current.references}},id=current.current.operationId
  queue.current=queue.current.then(async()=>{await newTaskFiles(id,data)}).catch(()=>{if(active.current)setWarning('本页保留待上传文件；浏览器暂存不可用，刷新后须重新选择未保存的原文件。')})}
 function canChoose(target:string){const s=current.current.slots.find(s=>s.key===target),saved=target==='product'?current.current.productId:s?.referenceId
  return !busy&&!hydrating&&!saved&&(!current.current.started||!(target==='product'?selected.current.product:selected.current.references?.[target])||(error?.target===target&&!error.unknown))}
 function choose(target:string,file:File|null){if(!canChoose(target))return
  const s=current.current.slots.find(s=>s.key===target),oldInfo=target==='product'?current.current.productInfo:s?.fileInfo
  const replacing=current.current.started&&error?.target===target&&!error.unknown
  if(current.current.started&&!replacing&&JSON.stringify(info(file))!==JSON.stringify(oldInfo)){setWarning('请重新选择同一原始文件以恢复未确认的上传。');return}
  if(target==='product'){selected.current={...selected.current,product:file};checkpoint({productInfo:info(file),...(replacing?{productOp:operationId()}:{})})}
  else{selected.current={...selected.current,references:{...selected.current.references,[target]:file}};changeSlot(target,{fileInfo:info(file),active:true,...(replacing?{uploadOp:operationId()}:{})})}
  setFiles(selected.current);cache()
 }
 function clearReference(id:string){
  if(current.current.started||inFlight.current)return
  const slot=current.current.slots.find(s=>s.key===id)!
  changeSlot(id,{fileInfo:null,active:(mode==='listing'&&slot.kind==='main')||!fixedSlot(slot.kind)||!!slot.requirements||slot.size!=='1024x1536'})
  selected.current={...selected.current,references:{...selected.current.references,[id]:null}}
  setFiles(selected.current);cache()
 }
 async function save(){if(inFlight.current||hydrating)return;inFlight.current=true;setBusy(true);setError(null);let step='检查输入',target:string|undefined
  const progress=(s:string)=>{step=s;if(active.current)setStage(s)};const publish=(t:TaskDraft)=>{if(active.current)setTask(t)}
  try{
   if(!current.current.productId&&!selected.current.product)throw new ApiError('请选择实际商品图片。','rejected')
   if(mode==='listing'&&!current.current.listingText.trim())throw new ApiError('请填写 Listing 商品资料。','rejected')
   if(!current.current.slots.some(s=>s.active&&(mode==='listing'||s.referenceId||selected.current.references?.[s.key])))throw new ApiError('请准备至少一个图位；Quick-A/B 需要对应参考。','rejected')
   await queue.current;if(!active.current)return;checkpoint({started:true});progress(current.current.taskId?'恢复已保存任务':'创建任务')
   let saved=current.current.taskId?await getTask(current.current.taskId):await createTaskDraft(current.current.title,current.current.operationId,current.current.overallRequirements,mode,current.current.listingText,current.current.supplementalText)
   if(saved.mode!==mode)throw new ApiError('已保存任务模式不一致，未继续上传或绑定。','rejected')
   checkpoint({taskId:saved.id});publish(saved);if(!active.current)return
   target='product';progress('保存商品图片')
   if(!current.current.productId){const uploaded=await uploadAsset(saved.id,'product_image',selected.current.product!,current.current.productOp);saved=uploaded
    if(!uploaded.uploaded_asset_id||!saved.assets.some(a=>a.id===uploaded.uploaded_asset_id&&a.role==='product_image'))throw new ApiError('服务器未明确返回本次商品素材身份。','unknown')
    checkpoint({productId:uploaded.uploaded_asset_id});publish(saved)}
   else if(!saved.assets.some(a=>a.id===current.current.productId&&a.role==='product_image'))throw new ApiError('已保存商品身份与服务器不一致。','unknown')
   if(!active.current)return
   for(const item of current.current.slots.filter(s=>s.active)){
    target=item.key;let slot=current.current.slots.find(s=>s.key===item.key)!;const label=slotTitle(slot.kind,slot.ordinal)
    progress(`${label}：创建图位`)
    if(!slot.slotId){const created=await createSlot(saved.id,slot.kind,slot.slotOp);saved=created
     if(!created.created_slot_id||!saved.slots.some(s=>s.id===created.created_slot_id&&s.kind===slot.kind))throw new ApiError('服务器未明确返回本次图位身份。','unknown')
     changeSlot(slot.key,{slotId:created.created_slot_id,configVersion:created.slots.find(s=>s.id===created.created_slot_id)?.version??0});publish(saved);slot=current.current.slots.find(s=>s.key===slot.key)!}
    if(!active.current)return
    const file=selected.current.references?.[slot.key]
    if(!slot.referenceId&&file){progress(`${label}：保存参考图片`);const uploaded=await uploadAsset(saved.id,'marketing_reference',file,slot.uploadOp);saved=uploaded
     if(!uploaded.uploaded_asset_id||!saved.assets.some(a=>a.id===uploaded.uploaded_asset_id&&a.role==='marketing_reference'))throw new ApiError('服务器未明确返回本次参考素材身份。','unknown')
     changeSlot(slot.key,{referenceId:uploaded.uploaded_asset_id});publish(saved);slot=current.current.slots.find(s=>s.key===slot.key)!}
    if(!active.current)return
    progress(`${label}：自动绑定与保存配置`);const persisted=saved.slots.find(s=>s.id===slot.slotId)
    if(!persisted||slot.referenceId&&!saved.assets.some(a=>a.id===slot.referenceId&&a.role==='marketing_reference'))throw new ApiError('已保存图位或参考身份不一致。','unknown')
    if(persisted.reference_asset_id!==slot.referenceId||persisted.output_size!==slot.size||persisted.requirements!==slot.requirements)
     saved=await configureSlot(saved.id,persisted,{reference_asset_id:slot.referenceId,output_size:slot.size,requirements:slot.requirements},slot.configVersion??0)
    const bound=saved.slots.find(s=>s.id===slot.slotId)
    if(!bound||bound.reference_asset_id!==slot.referenceId||bound.output_size!==slot.size||bound.requirements!==slot.requirements)throw new ApiError('服务器尚未确认本图位绑定及配置。','unknown')
    publish(saved);if(!active.current)return
   }
   if(mode==='listing'&&(saved.listing_materials?.listing_text!==current.current.listingText||saved.listing_materials?.supplemental_text!==current.current.supplementalText))throw new ApiError('商品资料与本次输入不一致，请核对。','unknown')
   if(saved.overall_requirement?.text!==current.current.overallRequirements)throw new ApiError('服务器整体要求与本次输入不一致，请核对。','unknown')
   remember(null);await newTaskFiles(current.current.operationId,null).catch(()=>undefined);if(active.current)navigate(`/workspace/${saved.id}`)
  }catch(cause){if(active.current)setError({stage:step,target,message:cause instanceof Error?cause.message:'保存失败',unknown:!(cause instanceof ApiError)||cause.outcome==='unknown'})
   if(step==='创建任务'&&cause instanceof ApiError&&cause.status===422)checkpoint({started:false})
  }finally{inFlight.current=false;if(active.current)setBusy(false)}
 }
 return {draft,files,task,busy,hydrating,stage,error,cacheWarning:warning,edit,editSlot,enableSlot,addSlot,removeSlot,choose,canChoose,clearReference,save}
}
