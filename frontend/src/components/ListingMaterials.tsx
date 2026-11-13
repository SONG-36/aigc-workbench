import {useEffect,useRef,useState} from 'react'
import {getTask,saveListingMaterials} from '../api/tasks'
import type {TaskDraft} from '../types'
import type {WriteAction} from '../hooks/useWriteAction'
import {operationId} from '../utils/operationId'

/** 原文输入，不要求 JSON；收起只改变文本框高度，不截断保存内容。 */
export function ListingMaterialsFields({listingText,supplementalText,disabled,onChange}:{
 listingText:string;supplementalText:string;disabled:boolean;onChange:(field:'listingText'|'supplementalText',value:string)=>void
}){
 const [listingExpanded,setListingExpanded]=useState(false),[supplementExpanded,setSupplementExpanded]=useState(false)
 return <div className="listing-text-fields">
  <label>Listing（必填）<textarea aria-label="Listing" required rows={listingExpanded?16:5} maxLength={50000}
   value={listingText} disabled={disabled} onChange={e=>onChange('listingText',e.target.value)}
   placeholder="直接粘贴商品标题、描述、五点卖点及参数，无需 JSON 或固定格式。"/></label>
  <button type="button" className="button-quiet" aria-expanded={listingExpanded} onClick={()=>setListingExpanded(v=>!v)}>{listingExpanded?'收起 Listing 文本框':'展开 Listing 文本框'}</button>
  <label>补充商品描述与卖点（可选）<textarea aria-label="补充商品描述与卖点" rows={supplementExpanded?16:5} maxLength={20000}
   value={supplementalText} disabled={disabled} onChange={e=>onChange('supplementalText',e.target.value)}
   placeholder="补充 Listing 未说明的商品信息和卖点。"/></label>
  <button type="button" className="button-quiet" aria-expanded={supplementExpanded} onClick={()=>setSupplementExpanded(v=>!v)}>{supplementExpanded?'收起补充资料文本框':'展开补充资料文本框'}</button>
 </div>
}

export function ListingMaterialsEditor({task,write,onSaved,onDirty}:{
 task:TaskDraft;write:WriteAction;onSaved:(task:TaskDraft)=>void;onDirty:(dirty:boolean)=>void
}){
 const m=task.listing_materials!
 const [draft,setDraft]=useState({listingText:m.listing_text,supplementalText:m.supplemental_text,version:m.version})
 const edited=useRef(false)
 const dirty=draft.listingText!==m.listing_text||draft.supplementalText!==m.supplemental_text
 useEffect(()=>{if(!edited.current)setDraft({listingText:m.listing_text,supplementalText:m.supplemental_text,version:m.version})},[m.version,m.listing_text,m.supplemental_text])
 useEffect(()=>{onDirty(dirty);return()=>onDirty(false)},[dirty,onDirty])
 useEffect(()=>{const guard=(e:BeforeUnloadEvent)=>{if(dirty){e.preventDefault();e.returnValue=''}}
  window.addEventListener('beforeunload',guard);return()=>window.removeEventListener('beforeunload',guard)},[dirty])
 function accept(next:TaskDraft){const saved=next.listing_materials!;edited.current=false
  setDraft({listingText:saved.listing_text,supplementalText:saved.supplemental_text,version:saved.version});onSaved(next)}
 return <section className="listing-materials" aria-label="Listing 商品资料设置">
  <h3>商品文字资料</h3>
  <ListingMaterialsFields {...draft} disabled={write.locked} onChange={(field,value)=>{edited.current=true;setDraft(d=>({...d,[field]:value}))}}/>
  <p className="muted">商品资料版本 V{m.version} · 本任务各图位共享；修改只影响后续执行，不改写历史 Context、图片或 Pass。</p>
  <div className="actions"><button type="button" disabled={write.locked||!dirty||!draft.listingText.trim()} onClick={()=>{
   const input={...draft},operation=operationId()
   void write.run(()=>saveListingMaterials(task.id,input.listingText,input.supplementalText,input.version,operation),accept)
  }}>保存商品资料</button>
  <button type="button" disabled={write.locked} onClick={()=>void write.run(()=>getTask(task.id),accept)}>重新读取商品资料（放弃本页未保存修改）</button></div>
  {dirty&&<p className="notice">商品资料尚未保存，生成与修改暂不可提交；旧页面不能覆盖新版本，请重新读取后核对。</p>}
 </section>
}
