import {useEffect,useState} from 'react'
import type {ImageSlot,TaskDraft} from '../types'
import {useSlotGeneration} from '../hooks/useSlotGeneration'
import {RevisionPanel,ReviewPanel,type RevisionDrafts} from './RevisionReviewPanel'
import {Overlay} from './ImageOverlay'
import {ExecutionDetails} from './ExecutionDetails'
import {Icon} from './Icon'
import {executionPresentation,reviewName,versionName,type SlotPresentation} from '../utils/presentation'

function SlotOutput({task,slot,locked,draft,setDraft,onStatus}:{task:TaskDraft;slot:ImageSlot;locked:boolean;draft:RevisionDrafts;setDraft:(d:RevisionDrafts)=>void;onStatus?:(s:SlotPresentation)=>void}) {
 const generation=useSlotGeneration(task,slot)
 const [selected,setSelected]=useState(''),[viewer,setViewer]=useState<{url:string;title:string;versionId?:string}|null>(null)
 const {data,error,submitting,uncertain}=generation
 const candidate=data?.candidates.find(c=>c.image_version_id===selected)||data?.candidates[0]
 const previewCandidate=data?.candidates.find(c=>c.image_version_id===viewer?.versionId)
 const ready=data?.readiness,latest=data?.executions[0],status=executionPresentation(data,candidate)
 useEffect(()=>{onStatus?.({...status,slotId:slot.id})},[onStatus,slot.id,status.label,status.tone])
 const sourceIds=candidate?[candidate.product_asset_id,candidate.reference_asset_id]:[ready?.product_asset_id,ready?.reference_asset_id||slot.reference_asset_id]
 const sources=sourceIds.map(id=>task.assets.find(a=>a.id===id))
 if(!sources[0])sources[0]=task.assets.find(a=>a.role==='product_image')
 const canGenerate=!!ready?.can_generate&&!locked&&!submitting&&!uncertain&&!error
 return <div className="slot-output">
  <div className="section-head output-heading"><h2>逐图制作与审核</h2><span className="muted">人工审核后交付</span></div>
  <div className="output-controls"><div><span role="status" aria-live="polite" className={`pill pill--${status.tone}`}>
   {data?.state==='RUNNING'&&<Icon name="refresh" className="is-spinning"/>}{status.label}</span>
   <p className="muted">{locked?'有待保存或待核对的输入，请先处理。':ready?.reason}</p></div>
   <button className="primary" disabled={!canGenerate} onClick={event=>{if(event.detail<2)void generation.submit()}}><Icon name="plus"/>{submitting?'正在提交…':'生成一张候选'}</button>
   <button className="refresh-button button-quiet" aria-label="刷新执行状态" title="刷新执行状态" onClick={generation.refresh}><Icon name="refresh"/></button>
  </div>
  {error&&<p role="alert" className="error">{error}</p>}
  {uncertain&&<p className="notice">提交结果尚未确认，正在查询服务器；不会自动重发付费请求。</p>}
  {(data?.execution_mode==='TEST_SUBSTITUTE'||latest?.execution_mode==='TEST_SUBSTITUTE')&&<p className="notice">TEST_ONLY · 测试替代工具 · 非真实模型生成，不能作为真实运营交付</p>}
  {latest?.error&&<p role="alert" className="error">{latest.error.message}（{latest.error.code}{latest.http_status?` / HTTP ${latest.http_status}`:''}）</p>}
  {data&&<>
   {candidate?<div className="candidate-panel">
    <div className="section-head candidate-heading"><div className="candidate-title"><h3>{versionName(data.candidates,candidate.image_version_id)}</h3>
     <span className={`pill pill--${candidate.review_status==='PASS'?'success':candidate.review_status==='REJECT'?'danger':'neutral'}`}>{reviewName(candidate.review_status)}</span>
     {candidate.is_current_delivery&&<span className="muted">当前有效交付</span>}</div>
     <label>历史候选版本<select aria-label="历史候选版本" value={candidate.image_version_id} onChange={e=>setSelected(e.target.value)}>
      {data.candidates.map(c=><option key={c.image_version_id} value={c.image_version_id}>{versionName(data.candidates,c.image_version_id)} · {reviewName(c.review_status)} · {new Date(c.created_at).toLocaleString()}</option>)}
     </select></label>
    </div>
    <button className="candidate-image" aria-label="放大查看候选原图" onClick={()=>setViewer({url:candidate.url,title:`生成候选 · ${versionName(data.candidates,candidate.image_version_id)} · 原始 PNG`,versionId:candidate.image_version_id})}><img src={candidate.url} alt="当前图位的已保存生成候选"/></button>
    <div className="canvas-caption"><span>{candidate.image.width} × {candidate.image.height} · {new Date(candidate.created_at).toLocaleString()}</span><a href={candidate.url} target="_blank" rel="noreferrer"><Icon name="expand"/>打开原始 PNG</a></div>
    {candidate.postprocess==='LANCZOS_UPSCALE'&&<p className="notice">分辨率放大：模型原生 {candidate.model_generation_size} → 最终 {candidate.final_output_size}（LANCZOS 等比例，无裁切）。</p>}
    {candidate.execution_mode==='TEST_ONLY'&&<p className="notice">TEST_ONLY 候选 · 不可用于真实运营交付</p>}
    {data.candidates.length>1&&<div className="version-strip" aria-label="历史版本缩略图">{data.candidates.map(c=><button className="version-thumbnail" key={c.image_version_id} aria-label={`查看版本 ${versionName(data.candidates,c.image_version_id)}`} aria-pressed={candidate.image_version_id===c.image_version_id} onClick={()=>setSelected(c.image_version_id)}>
     <img src={c.url} alt=""/><span>{versionName(data.candidates,c.image_version_id)}</span></button>)}</div>}
    <div className="image-actions-grid"><RevisionPanel taskId={task.id} slotId={slot.id} candidate={candidate} data={data} draft={draft} setDraft={setDraft} select={setSelected}
     disabled={locked||submitting||uncertain||!!error} submit={generation.submitRevision}/>
     <ReviewPanel key={candidate.image_version_id} taskId={task.id} slotId={slot.id} candidate={candidate} data={data} disabled={locked} refresh={generation.refresh}/></div>
    <section className="delivery-panel" aria-label="当前有效交付"><div className="section-head"><h3>当前有效交付</h3>{data.delivery&&<span className="pill pill--success"><Icon name="check"/>Pass</span>}</div>
     {data.delivery?<><p>{versionName(data.candidates,data.delivery.image_version_id)} · {data.delivery.reviewer}</p>
      {data.delivery.source==='TEST_REVIEW'&&<p className="notice">TEST_ONLY 测试审核，不构成真实运营交付。</p>}
      <p className="muted">{new Date(data.delivery.reviewed_at).toLocaleString()}</p><a className="button-link" href={data.delivery.url}><Icon name="download"/>{data.delivery.source==='TEST_REVIEW'?'下载 TEST_ONLY 测试 PNG':'下载当前有效交付 PNG'}</a></>
      :<p className="muted">尚无人工 Pass；候选不能作为有效交付下载。</p>}
    </section>
    <details className="metadata-details"><summary>版本身份与修改来源</summary><p>版本 {candidate.image_version_id}</p>{candidate.base_image_version_id&&<p>修改基础 {candidate.base_image_version_id}<br/>本轮：{candidate.revision_text}</p>}</details>
   </div>:<div className="empty output-empty"><Icon name="image"/><h3>你的下一版图片，从这里开始。</h3><p>本图位尚无已保存的候选图片。</p><p className="muted">保存商品与本图位参考后，检查生成条件。结果会保留为独立版本，等待人工审核。</p></div>}
   <details className="candidate-sources" aria-label="商品与参考来源"><summary>{candidate?'本候选来源对照':'当前商品与参考'}</summary>
    {ready?.product_selection&&<p className="muted">本次生成使用最新上传的商品图片：{task.assets.find(a=>a.id===ready.product_asset_id)?.filename}。历史候选的来源单独保留。</p>}
    <div className="source-grid">{sources.map((asset,index)=><figure key={index}><figcaption>{index===0?'实际商品原图':'绑定的营销参考'}</figcaption>
     {asset?<><button aria-label={`放大${index===0?'商品原图':'营销参考'}`} onClick={()=>setViewer({url:asset.url,title:index===0?'实际商品原图':'营销参考原图'})}><img src={asset.url} alt={index===0?'实售商品来源':'营销参考来源'}/></button><p className="muted">{asset.filename}</p></>:<p className="muted">来源尚未具备</p>}</figure>)}</div>
   </details>
   <ExecutionDetails executions={data.executions}/>
  </>}
  {viewer&&<Overlay title={viewer.title} onClose={()=>setViewer(null)}>
   {previewCandidate&&<div className="preview-info"><span className={`pill pill--${previewCandidate.review_status==='PASS'?'success':previewCandidate.review_status==='REJECT'?'danger':'neutral'}`}>{reviewName(previewCandidate.review_status)}</span>
    <span>{previewCandidate.image.width} × {previewCandidate.image.height}</span><time dateTime={previewCandidate.created_at}>{new Date(previewCandidate.created_at).toLocaleString()}</time>
    {previewCandidate.is_current_delivery&&<span>当前有效交付</span>}
    {previewCandidate.execution_mode==='TEST_ONLY'&&<span>TEST_ONLY · 不可用于真实运营交付</span>}
   </div>}
   <img className="full-image" src={viewer.url} alt={viewer.title}/>
   <div className="preview-actions"><a className="button-link" href={viewer.url} target="_blank" rel="noreferrer"><Icon name="expand"/>{previewCandidate?'查看生成的原始 PNG':'新窗口查看原始文件'}</a>
    {previewCandidate?.is_current_delivery&&data?.delivery?.image_version_id===previewCandidate.image_version_id&&<a className="button-link" href={data.delivery.url}><Icon name="download"/>{data.delivery.source==='TEST_REVIEW'?'下载 TEST_ONLY 测试 PNG':'下载当前有效 Pass 图片'}</a>}
   </div>
   {previewCandidate&&!previewCandidate.is_current_delivery&&<p className="muted">此版本不是当前有效交付；原始 PNG 供查看，不代表已通过审核。</p>}
  </Overlay>}
 </div>
}

export function GenerationWorkspace({task,slot,locked,unsavedSlots,onStatus}:{task:TaskDraft;slot?:ImageSlot;locked:boolean;unsavedSlots:Set<string>;onStatus?:(s:SlotPresentation)=>void}) {
 const [drafts,setDrafts]=useState<Record<string,RevisionDrafts>>({})
 if(!slot)return <><button disabled>生成一张候选</button><div className="empty output-empty"><Icon name="image"/><h3>还没有制作图位</h3><p>请先添加图位并保存参考。</p></div></>
 return <SlotOutput key={slot.id} task={task} slot={slot} onStatus={onStatus} locked={locked||unsavedSlots.has(slot.id)} draft={drafts[`${task.id}/${slot.id}`]||{baseId:'',texts:{}}}
  setDraft={value=>setDrafts(previous=>({...previous,[`${task.id}/${slot.id}`]:value}))}/>
}
