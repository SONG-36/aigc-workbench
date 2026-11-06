import {useEffect,useState} from 'react'
import {Link} from 'react-router-dom'
import {useNewQuickATask} from '../hooks/useNewQuickATask'
import {OUTPUT_SIZES,type Asset,type OutputSize} from '../types'
import {fixedSlot,slotTitle} from '../utils/slotTypes'
import {ReferenceImage} from './ReferenceImage'
function DraftImage({label,file,saved,expectedName,disabled,onChange}:{label:string;file:File|null;saved?:Asset;expectedName?:string;disabled:boolean;onChange:(file:File)=>void}) {
 const [preview,setPreview]=useState<string|null>(null)
 useEffect(()=>{if(!file||typeof URL.createObjectURL!=='function'){setPreview(null);return};const url=URL.createObjectURL(file);setPreview(url);return()=>URL.revokeObjectURL(url)},[file])
 return <section className="asset-section"><label className="upload-label">{saved?'原始文件已保存':`选择${label}`}
  <input type="file" aria-label={label==='商品图片'?'选择商品图片':`${label}图片`} accept="image/png,image/jpeg,image/webp" disabled={disabled}
   onChange={e=>{if(e.target.files?.[0])onChange(e.target.files[0]);e.target.value=''}} /></label>
  {saved?<><ReferenceImage url={saved.url} label={label}/><p>{saved.filename} · 原始文件已上传，绑定状态以保存结果为准</p></>
   :file?<div className="pending-asset"><p>{file.name} · 待保存，尚未上传服务器</p>{preview&&<ReferenceImage url={preview} label={label}/>}</div>
   :<div className="reference-empty">{expectedName?`${expectedName} 尚未恢复，请重新选择同一原始文件`:'请直接在本图位选择一张图片'}</div>}
 </section>
}
export function TaskForm(){
 const form=useNewQuickATask(),{draft,files,task,busy,hydrating,error}=form
 const saved=(id:string|null)=>task?.assets.find(a=>a.id===id)
 return <form onSubmit={e=>{e.preventDefault();void form.save()}} className="draft-form">
  {draft.started&&<p className="notice">恢复同一次保存；已保存身份与文件会跳过，未完成输入仍保留，不自动提交。</p>}
  <label htmlFor="task-title">任务名称</label><input id="task-title" value={draft.title} required maxLength={120} disabled={busy||draft.started}
   onChange={e=>form.edit({title:e.target.value})} placeholder="为当前目标商品填写一个任务名称"/>
  <h3>商品图片</h3><DraftImage label="商品图片" file={files.product} saved={saved(draft.productId)} expectedName={draft.productInfo?.name}
   disabled={!form.canChoose('product')} onChange={file=>form.choose('product',file)}/>
  <h3>按图位直接上传参考</h3><p className="muted">每张参考直接保存并绑定到所在图位，无需先上传到共享参考库。</p>
  <div className="slot-grid">{draft.slots.map(slot=>{const label=slotTitle(slot.kind,slot.ordinal),file=files.references?.[slot.key]||null
   return <fieldset className="slot-shell" key={slot.key} data-draft-slot-id={slot.key} data-slot-kind={slot.kind}>
    <legend>{label}</legend>
    {!fixedSlot(slot.kind)&&<button type="button" disabled={busy||draft.started} onClick={()=>form.removeSlot(slot.key)}>删除{label}</button>}
    {slot.kind==='dimensions'&&<p className="notice">仅上传已标注真实物理尺寸的专用白底素材；当前仅保存，专项确认与制作尚未开放，不允许 AI 推断尺寸。</p>}
    <DraftImage label={`${label}参考`} file={file} saved={saved(slot.referenceId)} expectedName={slot.fileInfo?.name}
     disabled={!form.canChoose(slot.key)} onChange={file=>form.choose(slot.key,file)}/>
    {(file||slot.referenceId)&&<button type="button" disabled={busy||draft.started} onClick={()=>form.clearReference(slot.key)}>移除{label}参考</button>}
    <label>输出尺寸<select aria-label={`${label}新建输出尺寸`} value={slot.size} disabled={busy||hydrating||draft.started}
     onChange={e=>form.editSlot(slot.key,{size:e.target.value as OutputSize})}>
     {OUTPUT_SIZES.map(size=><option key={size} value={size}>{size==='1536x1536'?'1536 × 1536（正方形）':size}</option>)}
    </select></label>
    {slot.size==='1536x1536'&&<p className="muted">1024×1024 原生方图经 LANCZOS 放大至 1536×1536，不裁切；这是像素尺寸。</p>}
    <label>本图补充要求<textarea aria-label={`${label}新建图位要求`} value={slot.requirements} maxLength={2000} rows={2} disabled={busy||draft.started}
     onChange={e=>form.editSlot(slot.key,{requirements:e.target.value})}/></label>
   </fieldset>})}</div>
  <div className="actions"><button type="button" disabled={busy||draft.started} onClick={()=>form.addSlot('selling_point')}>＋ 添加卖点图</button>
   <button type="button" disabled={busy||draft.started} onClick={()=>form.addSlot('scene')}>＋ 添加使用场景图</button></div>
  <label>整体运营要求（可选）<textarea aria-label="整体运营要求" rows={3} maxLength={2000} value={draft.overallRequirements} disabled={busy||draft.started}
   onChange={e=>form.edit({overallRequirements:e.target.value})}/></label>
  <p className="muted">作用于本任务全部图位的后续生成和显式修改；不自动重做或使已有 Pass 失效。空整体要求合法。</p>
  <p className="muted">一次保存 Task、商品素材、各图位参考与配置。JPEG / PNG / WebP，单张最多 10 MiB；保存不会调用模型。</p>
  {form.cacheWarning&&<p className="notice">{form.cacheWarning}</p>}{busy&&<p role="status">{form.stage}…</p>}
  {error&&<p role="alert" className="error">{error.stage}失败：{error.message}{error.unknown?' 结果尚未确认；继续保存复用原操作身份。':' 未完成文件与输入仍保留。'}</p>}
  {task&&error&&<p><Link to={`/workspace/${task.id}`}>打开已保存部分，核对尚未完成的资料</Link></p>}
  <button type="submit" className="primary" disabled={busy||hydrating}>{busy?'正在保存任务和资料…':error?'继续保存任务和资料':'保存任务和资料，进入工作台'}</button>
 </form>
}
