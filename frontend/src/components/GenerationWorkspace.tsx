import {Overlay} from './ImageOverlay'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { ImageSlot, TaskDraft } from '../types'
import { useSlotGeneration } from '../hooks/useSlotGeneration'
import type { ExecutionState } from '../api/generation'
import { RevisionPanel, ReviewPanel, type RevisionDrafts } from './RevisionReviewPanel'

const kinds = { main: '主图', dimensions: '尺寸图', comparison: '同类商品对比图', selling_point: '卖点图', scene: '场景图' }
const labels: Record<ExecutionState, string> = {
  NOT_STARTED: '尚未开始', RUNNING: '执行中', SUCCEEDED: '成功并产生候选', FAILED: '执行失败', OUTCOME_UNKNOWN: '结果未知',
}
const phases: Record<string, string> = { PREPARING: '准备中', C01: '商品理解中', C02: '参考理解中',
  C03_C04_C05: '制作决定中', C06: '图片生成中', C07: '图片修改中', REVIEW_PENDING: '候选待审核' }


function SlotOutput({ task, slot, locked, draft, setDraft }: {
  task: TaskDraft; slot: ImageSlot; locked: boolean; draft: RevisionDrafts; setDraft: (draft: RevisionDrafts) => void
}) {
  const generation = useSlotGeneration(task, slot)
  const [selected, setSelected] = useState('')
  const [viewer, setViewer] = useState<{ url: string; title: string } | null>(null)
  const { data, error, submitting, uncertain } = generation
  const candidate = data?.candidates.find(c => c.image_version_id === selected) || data?.candidates[0]
  const ready = data?.readiness
  const sourceIds = candidate ? [candidate.product_asset_id, candidate.reference_asset_id]
    : [ready?.product_asset_id, ready?.reference_asset_id || slot.reference_asset_id]
  const sources = sourceIds.map(id => task.assets.find(a => a.id === id))
  if (!sources[0]) sources[0] = task.assets.find(a => a.role === 'product_image')
  const canGenerate = !!ready?.can_generate && !locked && !submitting && !uncertain && !error
  const latest = data?.executions[0]
  const progressLabel = data?.state === 'RUNNING' && latest?.progress?.stage ? phases[latest.progress.stage]
    : data?.state === 'SUCCEEDED' && latest?.progress?.stage === 'REVIEW_PENDING' ? phases.REVIEW_PENDING : null

  return <div className="slot-output">
    <div className="output-controls">
      <button className="primary" disabled={!canGenerate} onClick={event => { if (event.detail < 2) void generation.submit() }}>
        {submitting ? '正在提交…' : '生成一张候选'}</button>
      <div><span role="status" className="pill">{progressLabel || (data ? labels[data.state] : '读取服务器状态…')}</span>
        <p className="muted">{locked ? '有待保存或待核对的输入，请先处理。' : ready?.reason}</p></div>
      <button onClick={generation.refresh}>刷新执行状态</button>
    </div>
    {error && <p role="alert" className="error">{error}</p>}
    {uncertain && <p className="notice">提交结果尚未确认，正在查询服务器；不会自动重发付费请求。</p>}
    {(data?.execution_mode === 'TEST_SUBSTITUTE' || latest?.execution_mode === 'TEST_SUBSTITUTE')
      && <p className="notice">TEST_ONLY · 测试替代工具 · 非真实模型生成，不能作为真实运营交付</p>}
    {ready?.product_selection && <p className="muted">本次生成使用最新上传的商品图片：
      {task.assets.find(a => a.id === ready.product_asset_id)?.filename}。历史候选的来源单独保留。</p>}
    {latest?.error && <p role="alert" className="error">{latest.error.message}（{latest.error.code}
      {latest.http_status ? ` / HTTP ${latest.http_status}` : ''}）</p>}
    {data && <div className="candidate-workspace">
      <div className="candidate-panel">
        {candidate ? <>
          <label>历史候选版本<select aria-label="历史候选版本" value={candidate.image_version_id}
            onChange={event => setSelected(event.target.value)}>
            {data.candidates.map((c, index) => <option key={c.image_version_id} value={c.image_version_id}>
              候选 {data.candidates.length - index} · {c.review_status || 'PENDING'} · {new Date(c.created_at).toLocaleString()}</option>)}
          </select></label>
          <button className="candidate-image" aria-label="放大查看候选原图"
            onClick={() => setViewer({ url: candidate.url, title: '生成候选 · 原始 PNG' })}>
            <img src={candidate.url} alt="当前图位的已保存生成候选" /></button>
          <p className="muted">{candidate.review_status || 'PENDING'}{candidate.is_current_delivery ? ' · 当前有效交付' : ''} · {candidate.image.width}×{candidate.image.height} ·
            {new Date(candidate.created_at).toLocaleString()}</p>
          {candidate.postprocess === 'LANCZOS_UPSCALE' && <p className="notice">
            分辨率放大：模型原生 {candidate.model_generation_size} → 最终 {candidate.final_output_size}（LANCZOS 等比例，无裁切）。</p>}
          <p className="muted">版本 {candidate.image_version_id}</p>
          {candidate.execution_mode === 'TEST_ONLY' && <p className="notice">TEST_ONLY 候选 · 不可用于真实运营交付</p>}
          {candidate.base_image_version_id && <p className="muted">修改基础 {candidate.base_image_version_id}<br />本轮：{candidate.revision_text}</p>}
          <a href={candidate.url} target="_blank" rel="noreferrer">打开原始 PNG</a>
          <RevisionPanel taskId={task.id} slotId={slot.id} candidate={candidate} data={data} draft={draft} setDraft={setDraft}
            select={setSelected} disabled={locked || submitting || uncertain || !!error} submit={generation.submitRevision} />
          <ReviewPanel key={candidate.image_version_id} taskId={task.id} slotId={slot.id} candidate={candidate} data={data}
            disabled={locked} refresh={generation.refresh} />
        </> : <div className="empty">本图位尚无已保存的候选图片。</div>}
        <section className="delivery-panel" aria-label="当前有效交付"><h3>当前有效交付</h3>
          {data.delivery ? <><p>Pass 版本 {data.delivery.image_version_id}</p>
            {data.delivery.source === 'TEST_REVIEW' && <p className="notice">TEST_ONLY 测试审核，不构成真实运营交付。</p>}
            <p className="muted">{data.delivery.reviewer} · {new Date(data.delivery.reviewed_at).toLocaleString()}</p>
            <a href={data.delivery.url}>{data.delivery.source === 'TEST_REVIEW' ? '下载 TEST_ONLY 测试 PNG' : '下载当前有效交付 PNG'}</a></>
            : <p>尚无人工 Pass；候选不能作为有效交付下载。</p>}
        </section>
      </div>
      <aside className="candidate-sources" aria-label="商品与参考来源">
        <h3>{candidate ? '本候选来源对照' : '当前商品与参考'}</h3>
        {sources.map((asset, index) => <figure key={index}>
          <figcaption>{index === 0 ? '实际商品原图' : '绑定的营销参考'}</figcaption>
          {asset ? <><button aria-label={`放大${index === 0 ? '商品原图' : '营销参考'}`} onClick={() =>
            setViewer({ url: asset.url, title: index === 0 ? '实际商品原图' : '营销参考原图' })}>
            <img src={asset.url} alt={index === 0 ? '实售商品来源' : '营销参考来源'} /></button>
            <p className="muted">{asset.filename}</p></> : <p className="muted">来源尚未具备</p>}
        </figure>)}
      </aside>
    </div>}
    {!!data?.executions.length && <details className="execution-history"><summary>执行记录 · {data.executions.length}</summary>
      {data.executions.map(e => <div key={e.operation_id} className="execution-row">
        <p>{labels[e.state]} · {new Date(e.created_at).toLocaleString()}
          {e.execution_mode === 'TEST_SUBSTITUTE' ? ' · 测试替代工具' : ''}</p>
        <p className="muted">{e.model} · Run {e.run_id || '尚未进入模型执行'}
          {e.duration_ms !== null ? ` · ${(e.duration_ms / 1000).toFixed(2)} 秒` : ''}</p>
        {e.usage && <p className="muted">实际 Usage：输入 {e.usage.input_tokens ?? '未知'}，输出 {e.usage.output_tokens ?? '未知'} tokens。</p>}
        {e.usage_cost_estimate && <p className="muted">用量费用估算 USD {e.usage_cost_estimate.usd}（费率日期 {e.usage_cost_estimate.checked_on}，非最终账单）。</p>}
        {e.error && <p className="error">{e.error.message}（{e.error.code}）</p>}
        {e.progress?.steps?.map(s => <p className="muted" key={s.capability}>
          {s.capability} · {s.status}{s.reused ? ' · 复用有效结果' : ''} · Run {s.run_id || '准备中'}</p>)}
      </div>)}
    </details>}
    {viewer && <Overlay title={viewer.title} onClose={() => setViewer(null)}>
      <img className="full-image" src={viewer.url} alt={viewer.title} />
      <a href={viewer.url} target="_blank" rel="noreferrer">新窗口查看原始文件</a>
    </Overlay>}

  </div>
}

export function GenerationWorkspace({ task, locked, unsavedSlots }: {
  task: TaskDraft; locked: boolean; unsavedSlots: Set<string>
}) {
  const [search, setSearch] = useSearchParams()
  const [drafts, setDrafts] = useState<Record<string, RevisionDrafts>>({})
  const slots = task.slots.filter(s=>!s.archived)
  const selected = slots.find(s => s.id === search.get('slot')) || slots[0]
  if (!selected) return <><button disabled>生成一张候选</button>
    <div className="empty">请先保存一个图位；尚未开始生成。</div></>
  return <div className="output-layout"><nav className="output-slots" aria-label="选择制作图位">
    {slots.map(s => <button key={s.id} aria-pressed={s.id === selected.id} onClick={() => {
      setSearch(previous => { const next = new URLSearchParams(previous); next.set('slot', s.id); return next }, { replace: true })
    }}>{kinds[s.kind]}{['selling_point', 'scene'].includes(s.kind)
      ? ` ${task.slots.filter(item => item.kind === s.kind).findIndex(item => item.id === s.id) + 1}` : ''}</button>)}
  </nav><SlotOutput key={selected.id} task={task} slot={selected} locked={locked || unsavedSlots.has(selected.id)}
    draft={drafts[`${task.id}/${selected.id}`] || { baseId: '', texts: {} }}
    setDraft={value => setDrafts(previous => ({ ...previous, [`${task.id}/${selected.id}`]: value }))} /></div>
}
