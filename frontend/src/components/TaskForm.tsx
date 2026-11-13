import {useEffect,useState} from 'react'
import {Link} from 'react-router-dom'
import {useNewQuickATask} from '../hooks/useNewQuickATask'
import {OUTPUT_SIZES,type Asset,type OutputSize,type TaskDraft} from '../types'
import {fixedSlot,slotTitle} from '../utils/slotTypes'
import {ReferenceImage} from './ReferenceImage'
import {FilePicker} from './FilePicker'
import {Icon} from './Icon'
import {ListingMaterialsFields} from './ListingMaterials'

function DraftImage({label,file,saved,expectedName,disabled,onChange}:{label:string;file:File|null;saved?:Asset;expectedName?:string;disabled:boolean;onChange:(file:File)=>void}) {
 const [preview,setPreview]=useState<string|null>(null)
 useEffect(()=>{if(!file||typeof URL.createObjectURL!=='function'){setPreview(null);return};const url=URL.createObjectURL(file);setPreview(url);return()=>URL.revokeObjectURL(url)},[file])
 const present=!!saved||!!file
 const description=label==='商品图片'?'实际商品原图':`${label}图片`
 return <section className="asset-section">
  {saved?<><ReferenceImage url={saved.url} label={label} description={description}/><p className="draft-asset-note">{saved.filename} · 原始文件已上传，绑定状态以保存结果为准</p></>
   :file?<div className="pending-asset">{preview&&<ReferenceImage url={preview} label={label} description={description}/>}<p>{file.name} · 待保存，尚未上传服务器</p></div>:null}
  <FilePicker label={label==='商品图片'?'选择商品图片':`${label}图片`} action={present?`替换${label}`:`选择${label}`} compact={present} disabled={disabled} onSelect={onChange}/>
  {!present&&expectedName&&<p className="notice">{expectedName} 尚未恢复，请重新选择同一原始文件</p>}
 </section>
}
export function TaskForm({mode='quick_a'}:{mode?:TaskDraft['mode']}){
 const form=useNewQuickATask(mode),{draft,files,task,busy,hydrating,error}=form
 const saved=(id:string|null)=>task?.assets.find(a=>a.id===id)
 return <form onSubmit={e=>{e.preventDefault();void form.save()}} className="draft-form">
  <nav className="form-jump-links" aria-label="新建任务页内导航"><a href="#task-materials">商品素材</a><a href="#slot-references">图位参考</a><a href="#overall-requirements">整体要求</a><a href="#save-task">前往保存<Icon name="arrow"/></a></nav>
  {draft.started&&<p className="notice">恢复同一次保存；已保存身份与文件会跳过，未完成输入仍保留，不自动提交。</p>}
  <section id="task-materials" className="form-section"><div className="section-head"><div className="section-title"><span className="section-number">01</span><h3>任务与商品素材</h3></div><span className="muted">真实商品是外观依据</span></div>
   <div className="task-material-layout">
   <div className="task-name-field"><label htmlFor="task-title">任务名称</label><input id="task-title" value={draft.title} required maxLength={120} disabled={busy||draft.started}
    onChange={e=>form.edit({title:e.target.value})} placeholder="例如：汽车电路检测仪 · 营销主图"/></div>
   <div className="product-draft"><label>实际商品图片</label><DraftImage label="商品图片" file={files.product} saved={saved(draft.productId)} expectedName={draft.productInfo?.name}
    disabled={!form.canChoose('product')} onChange={file=>form.choose('product',file)}/></div>
   </div>
  </section>
  {mode==='listing'&&<section className="form-section" aria-label="Listing 商品资料输入"><h3>商品文字资料</h3>
   <ListingMaterialsFields listingText={draft.listingText} supplementalText={draft.supplementalText} disabled={busy||draft.started}
    onChange={(field,value)=>form.edit({[field]:value})}/><p className="muted">商品资料提供营销内容；下方设计参考可选，只提供版式和风格。</p></section>}
  <section id="slot-references" className="form-section"><div className="section-head"><div className="section-title"><span className="section-number">02</span><h3>图位与营销参考</h3></div></div>
   <p className="muted">{mode==='listing'?'设计参考可选；无需参考也可直接制作。默认主图已启用，可按需增加卖点或场景图。':'在对应图位上传参考，保存时自动绑定。'}</p>
   <div className="slot-grid">{draft.slots.map(slot=>{const label=slotTitle(slot.kind,slot.ordinal),file=files.references?.[slot.key]||null
    return <fieldset className="slot-shell" key={slot.key} data-draft-slot-id={slot.key} data-slot-kind={slot.kind}>
     <legend>{label}</legend>
     {mode==='listing'&&fixedSlot(slot.kind)&&<label className="batch-slot-choice"><input type="checkbox" aria-label={`制作${label}`} checked={slot.active}
      disabled={busy||draft.started} onChange={event=>form.enableSlot(slot.key,event.target.checked)}/>制作{label}（无需设计参考）</label>}
     {!fixedSlot(slot.kind)&&<button className="button-quiet button-danger" type="button" disabled={busy||draft.started} onClick={()=>form.removeSlot(slot.key)}>删除{label}</button>}
     {slot.kind==='dimensions'&&<p className="notice">{mode==='listing'?'尺寸图需运营确认、已标注真实尺寸的专用白底商品素材；不继承设计参考的尺寸，也不能由像素推断。':'尺寸图使用商品图和本图位营销参考直接生成；保留参考已有的尺寸数值、单位、文字和标注关系，不编造新尺寸。'}</p>}
     <DraftImage label={`${label}参考`} file={file} saved={saved(slot.referenceId)} expectedName={slot.fileInfo?.name}
      disabled={!form.canChoose(slot.key)} onChange={file=>form.choose(slot.key,file)}/>
     {(file||slot.referenceId)&&<button className="button-quiet" type="button" disabled={busy||draft.started} onClick={()=>form.clearReference(slot.key)}>移除{label}参考</button>}
     <label>输出尺寸<select aria-label={`${label}新建输出尺寸`} value={slot.size} disabled={busy||hydrating||draft.started}
      onChange={e=>form.editSlot(slot.key,{size:e.target.value as OutputSize})}>
      {OUTPUT_SIZES.map(size=><option key={size} value={size}>{size==='1536x1536'?'1536 × 1536（正方形）':size}</option>)}
     </select></label>
     {slot.size==='1536x1536'&&<p className="muted">1024×1024 原生方图经 LANCZOS 放大至 1536×1536，不裁切；这是像素尺寸。</p>}
     <label>本图补充要求<textarea aria-label={`${label}新建图位要求`} value={slot.requirements} maxLength={2000} rows={2} disabled={busy||draft.started}
      onChange={e=>form.editSlot(slot.key,{requirements:e.target.value})} placeholder="选填，仅用于这个图位"/></label>
    </fieldset>})}</div>
   <div className="actions"><button type="button" disabled={busy||draft.started} onClick={()=>form.addSlot('selling_point')}>＋ 添加卖点图</button>
    <button type="button" disabled={busy||draft.started} onClick={()=>form.addSlot('scene')}>＋ 添加使用场景图</button></div>
  </section>
  <section id="overall-requirements" className="form-section"><div className="section-title"><span className="section-number">03</span><h3>整体运营要求</h3><span className="muted">可选</span></div>
   <label>作用于整个任务<textarea aria-label="整体运营要求" rows={3} maxLength={2000} value={draft.overallRequirements} disabled={busy||draft.started}
    onChange={e=>form.edit({overallRequirements:e.target.value})} placeholder="例如：所有图片采用简洁蓝白设计，保持商品外观与附件完整。"/></label>
   <p className="muted">作用于本任务全部图位的后续生成和显式修改；不自动重做或使已有 Pass 失效。空整体要求合法。</p>
  </section>
  {form.cacheWarning&&<p className="notice">{form.cacheWarning}</p>}{busy&&<p role="status" aria-live="polite" className="loading-line"><Icon name="refresh" className="is-spinning"/>{form.stage}…</p>}
  {error&&<p role="alert" className="error">{error.stage}失败：{error.message}{error.unknown?' 结果尚未确认；继续保存复用原操作身份。':' 未完成文件与输入仍保留。'}</p>}
  {task&&error&&<p><Link to={`/workspace/${task.id}`}>打开已保存部分，核对尚未完成的资料</Link></p>}
  <div className="form-submit-bar"><p>保存任务、原始素材与图位配置。此操作不会调用模型。</p>
   <button id="save-task" type="submit" className="primary" disabled={busy||hydrating}>{busy?'正在保存任务和资料…':error?'继续保存任务和资料':'保存任务和资料，进入工作台'}<Icon name="arrow"/></button>
  </div>
 </form>
}
