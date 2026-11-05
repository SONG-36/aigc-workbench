import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/tasks'
import { quoteRevision, reviewImage, type Candidate, type RevisionQuote, type SlotWorkspace } from '../api/generation'

export interface RevisionDrafts { baseId: string; texts: Record<string, string> }

/** Drafts belong to an explicit base; selecting another preview never retargets an edit. */
export function RevisionPanel({ taskId, slotId, candidate, data, draft, setDraft, select, disabled, submit }: {
  taskId: string; slotId: string; candidate: Candidate; data: SlotWorkspace; draft: RevisionDrafts
  setDraft: (draft: RevisionDrafts) => void; select: (id: string) => void; disabled: boolean
  submit: (quote: RevisionQuote) => Promise<void>
}) {
  const [quote, setQuote] = useState<RevisionQuote | null>(null)
  const [quoting, setQuoting] = useState(false)
  const [error, setError] = useState('')
  const [ack, setAck] = useState(false)
  const quoteLock = useRef(false)
  const text = draft.texts[draft.baseId] || ''
  const matches = draft.baseId === candidate.image_version_id
  useEffect(() => { setQuote(null); setAck(false) }, [draft.baseId, text])
  async function prepare() {
    if (quoteLock.current || disabled || !matches || !text.trim()) return
    quoteLock.current = true; setQuoting(true); setError('')
    try { setQuote(await quoteRevision(taskId, slotId, draft.baseId, text)); setAck(false) }
    catch (cause) { setError(cause instanceof Error ? cause.message : '修改预检失败，未付费。') }
    finally { setQuoting(false); quoteLock.current = false }
  }
  return <section className="revision-panel" aria-label="单图修改">
    <h3>基于指定版本修改</h3>
    <button disabled={disabled || quoting} onClick={() => {
      setDraft({ ...draft, baseId: candidate.image_version_id }); setQuote(null); setError('')
    }}>以当前查看版本为修改基础</button>
    {draft.baseId && <>
      <p className="muted">修改基础：{draft.baseId}</p>
      {!matches && <p className="notice">正在查看其他版本，原修改草稿仍绑定原基础。
        <button onClick={() => select(draft.baseId)}>查看基础版本并继续</button></p>}
      <label>本轮修改意见<textarea aria-label="本轮修改意见" maxLength={2000} value={text}
        disabled={disabled || !matches || quoting} onChange={e => setDraft({ ...draft,
          texts: { ...draft.texts, [draft.baseId]: e.target.value } })} /></label>
      <p className="muted">保留基础图已实现的有效设计；只发送当前有效约束和本轮意见。不会自动重放历史修改。</p>
      <button disabled={disabled || !matches || !text.trim() || quoting || !data.can_request_revision} onClick={() => void prepare()}>
        {quoting ? '正在核对修改输入…' : '检查修改与费用'}</button>
    </>}
    {error && <p role="alert" className="error">{error}</p>}
    {quote && matches && quote.revision_text === text.trim() && <fieldset className="paid-confirm">
      <legend>确认一次付费修改</legend>
      <p>基础：{quote.base_image_version_id}</p><p>本轮意见：{quote.revision_text}</p>
      <p>{quote.model} · 本地估算 USD {quote.estimate_usd} · 估算门槛 USD {quote.max_cost_usd}</p>
      <p className="muted">门槛不是供应商账单硬上限；只执行一次，不自动重试，新候选不替换原 Pass。</p>
      <label className="paid-ack"><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />
        {data.execution_mode === 'TEST_SUBSTITUTE' ? '测试替代工具，不调用真实 API' : '我确认执行这一次付费修改'}</label>
      <button className="primary" disabled={!ack || disabled || !data.can_request_revision} onClick={() => {
        const frozen = quote; setQuote(null); setAck(false); void submit(frozen)
      }}>确认付费，修改 1 张</button>
    </fieldset>}
  </section>
}

/** Local pilot identity is operator-entered, not an authenticated account. AI execution never creates these review records. */
export function ReviewPanel({ taskId, slotId, candidate, data, disabled, refresh }: {
  taskId: string; slotId: string; candidate: Candidate; data: SlotWorkspace; disabled: boolean; refresh: () => void
}) {
  const [reviewer, setReviewer] = useState('')
  const [feedback, setFeedback] = useState('')
  const [decision, setDecision] = useState<'PASS' | 'REJECT' | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState('')
  const lock = useRef(false)
  const reviews = (data.reviews || []).filter(r => r.image_version_id === candidate.image_version_id)
  useEffect(() => {
    if (pending && data.reviews?.some(r => r.operation_id === pending)) {
      setPending(null); setError(''); setFeedback(''); refresh()
    }
  }, [data.reviews, pending, refresh])
  useEffect(() => {
    if (!pending) return
    const timer = setTimeout(refresh, 1500) // GET recovery only; never resend an uncertain review.
    return () => clearTimeout(timer)
  }, [pending, refresh])
  async function submit() {
    if (!decision || lock.current || pending || !reviewer.trim()) return
    lock.current = true
    const operation = crypto.randomUUID(); setPending(operation); setError('')
    try {
      await reviewImage(taskId, slotId, candidate.image_version_id, operation, decision, reviewer, feedback, data.delivery?.id || null)
      setPending(null); setFeedback('')
    } catch (cause) {
      if (cause instanceof ApiError && cause.outcome !== 'unknown') setPending(null)
      setError(cause instanceof Error ? cause.message : '审核保存结果未知，请查询服务器。')
    } finally { lock.current = false; setDecision(null); refresh() }
  }
  return <section className="review-panel" aria-label="人工版本审核">
    <h3>人工审核当前查看版本</h3>
    <p>版本：{candidate.image_version_id} · {candidate.review_status || 'PENDING'}
      {candidate.is_current_delivery ? ' · 当前有效交付' : ''}</p>
    <label>审核人<input aria-label="审核人" value={reviewer} maxLength={80} disabled={disabled || !!pending}
      onChange={e => setReviewer(e.target.value)} /></label>
    <p className="muted">本地 Pilot 使用人工填写姓名，尚无账户身份认证。</p>
    <label>审核反馈<textarea aria-label="审核反馈" maxLength={2000} value={feedback} disabled={disabled || !!pending}
      onChange={e => setFeedback(e.target.value)} /></label>
    <div className="review-actions">
      <button disabled={disabled || !!pending || !reviewer.trim()} onClick={() => setDecision('PASS')}>Pass 此版本</button>
      <button disabled={disabled || !!pending || !reviewer.trim() || candidate.has_historical_pass}
        onClick={() => setDecision('REJECT')}>Reject 此版本</button>
    </div>
    {decision && <div className="notice">
      <p>确认 {decision} 版本 {candidate.image_version_id}？{decision === 'PASS'
        ? '仅此图位的当前有效交付会更新为该版本，历史审核继续保留。' : '保留图片和历史，当前有效交付保持不变。'}</p>
      <button disabled={disabled || !!pending} onClick={() => void submit()}>确认 {decision}</button>
      <button onClick={() => setDecision(null)}>取消审核</button>
    </div>}
    {pending && <p role="status">正在查询审核保存结果，不会自动重发。</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {!!reviews.length && <details><summary>此版本审核历史 · {reviews.length}</summary>
      {reviews.map(r => <p key={r.id}>{r.decision} · {r.reviewer} · {new Date(r.reviewed_at).toLocaleString()}
        {r.source === 'TEST_REVIEW' ? ' · 测试审核' : ''}<br />{r.feedback || '无反馈'}</p>)}
    </details>}
  </section>
}
