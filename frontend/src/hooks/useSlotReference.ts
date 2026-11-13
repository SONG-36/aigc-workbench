import {useEffect,useRef,useState} from 'react'
import {ApiError,configureSlot,uploadAsset} from '../api/tasks'
import type {ImageSlot,TaskDraft} from '../types'
import type {WriteAction} from './useWriteAction'
import {operationId} from '../utils/operationId'
import {newTaskFiles} from '../utils/newTaskFiles'
type Pending={operation:string;assetId:string|null;size:ImageSlot['output_size'];requirements:string;expectedVersion?:number;fileInfo?:{name:string;size:number;lastModified:number}}
export function useSlotReference(task:TaskDraft,slot:ImageSlot,write:WriteAction,onSaved:(t:TaskDraft)=>void){
 const cacheKey=`aigc.slot-reference/${task.id}/${slot.id}`
 const current=useRef<Pending|null>(null),fileRef=useRef<File|null>(null),active=useRef(true)
 const fileWrites=useRef(Promise.resolve())
 const [rejected,setRejected]=useState(false)
 const [conflict,setConflict]=useState(false)
 const [pending,setPending]=useState<Pending|null>(null),[file,setFile]=useState<File|null>(null),[message,setMessage]=useState('')
 function cacheFile(file:File|null){
  // Serialize cache writes so completion cannot race an older file save after cleanup.
  fileWrites.current=fileWrites.current.then(async()=>{await newTaskFiles(cacheKey,file?{product:null,reference:file}:null)}).catch(()=>undefined)
 }
 function remember(value:Pending|null){current.current=value;try{value?sessionStorage.setItem(cacheKey,JSON.stringify(value)):sessionStorage.removeItem(cacheKey)}catch{/* Page state remains. */};if(active.current)setPending(value)}
 useEffect(()=>{active.current=true;try{const saved=JSON.parse(sessionStorage.getItem(cacheKey)||'null');if(saved?.operation){current.current=saved;setPending(saved)}}catch{/* No automatic writes. */}
  void newTaskFiles(cacheKey).then(cached=>{if(active.current&&cached?.reference){fileRef.current=cached.reference;setFile(cached.reference)}}).catch(()=>undefined)
  return()=>{active.current=false}
 },[cacheKey])
 function choose(file:File,size:ImageSlot['output_size'],requirements:string){
  if(write.locked||current.current)return
  fileRef.current=file;setFile(file);setMessage('正在上传并自动绑定…')
  setRejected(false);const intent={operation:operationId(),assetId:null,size,requirements,expectedVersion:slot.version??0,fileInfo:{name:file.name,size:file.size,lastModified:file.lastModified}};remember(intent)
  cacheFile(file)
  execute()
 }
 function execute(){void write.run(async()=>{
  const intent=current.current;if(!intent)throw new ApiError('本图位没有待恢复上传。','rejected')
  let state=task
  try {
  if(!intent.assetId){if(!fileRef.current)throw new ApiError('未恢复原始文件，请重新打开已保存资料核对；不会新建另一上传。','rejected')
   state=await uploadAsset(task.id,'marketing_reference',fileRef.current,intent.operation)
   const id=(state as TaskDraft&{uploaded_asset_id?:string}).uploaded_asset_id
   if(!id||!state.assets.some(a=>a.id===id&&a.role==='marketing_reference'))throw new ApiError('上传结果缺少明确素材身份，不能报告绑定成功。','unknown')
   remember({...intent,assetId:id})
  }
  if(active.current)setMessage('图片已上传，正在保存当前图位绑定…')
  const bound=await configureSlot(task.id,slot,{reference_asset_id:current.current!.assetId,output_size:intent.size,requirements:intent.requirements},intent.expectedVersion??0)
  if(!bound.slots.some(s=>s.id===slot.id&&s.reference_asset_id===current.current!.assetId))throw new ApiError('上传完成，但服务器尚未确认本图位绑定。','unknown')
  return bound
  } catch(cause) {
   if(cause instanceof ApiError&&cause.status===409&&active.current){setConflict(true);setMessage('图位数据已变化，请重新读取后核对；不会自动覆盖。')}
   else if(active.current)setMessage(current.current?.assetId?'图片已上传，但绑定未完成；重试只恢复绑定。':'上传未完成，原文件与操作身份保留。')
   if(cause instanceof ApiError&&cause.outcome==='rejected'&&cause.status!==409&&!current.current?.assetId&&active.current)setRejected(true)
   throw cause
  }
 },next=>{onSaved(next);remember(null);fileRef.current=null;setFile(null);setMessage('已上传并绑定到当前图位');cacheFile(null)})}
 function reset(){if(!write.locked&&rejected){remember(null);fileRef.current=null;setFile(null);setRejected(false);setMessage('请选择有效图片重新上传。');cacheFile(null)}}
 function resume(file:File){const intent=current.current;if(!intent||write.locked)return
  if(intent.fileInfo&&(file.name!==intent.fileInfo.name||file.size!==intent.fileInfo.size||file.lastModified!==intent.fileInfo.lastModified)){setMessage('请重新选择同一原始待上传文件；不能替换结果未确认的上传。');return}
  fileRef.current=file;setFile(file);cacheFile(file);execute()
 }
 function abandon(){remember(null);fileRef.current=null;setFile(null);setConflict(false);setMessage('已重新读取图位，未自动绑定待上传素材。');cacheFile(null)}
 return {pending,file,message,choose,rejected,reset,resume,retry:execute,conflict,abandon}
}
