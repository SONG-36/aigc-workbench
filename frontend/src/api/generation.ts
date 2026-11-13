import { ApiError, request } from './tasks'

export type ExecutionState = 'NOT_STARTED' | 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'OUTCOME_UNKNOWN'
export interface Candidate {
  image_version_id: string; candidate_id: string; task_id: string; slot_id: string; run_id: string
  created_at: string; product_asset_id: string; reference_asset_id: string
  status: 'CANDIDATE_REVIEW_PENDING'; operator_approved: boolean; pass: boolean; url: string
  base_image_version_id?: string | null; revision_text?: string | null
  requested_output_size?: string; model_generation_size?: string; final_output_size?: string
  postprocess?: 'NONE' | 'LANCZOS_UPSCALE'
  execution_mode?: 'REAL' | 'TEST_ONLY'; not_for_real_delivery?: boolean
  review_status?: 'PENDING' | 'PASS' | 'REJECT'; is_current_delivery?: boolean; has_historical_pass?: boolean
  image: { width: number; height: number; size_bytes: number; sha256: string }
}
export interface ImageReview {
  id: string; operation_id: string; task_id: string; slot_id: string; image_version_id: string
  decision: 'PASS' | 'REJECT'; reviewer: string; reviewed_at: string; feedback: string
  source: 'TEST_REVIEW' | 'EXPLICIT_OPERATOR'
}
export interface RevisionQuote {
  snapshot_id: string; base_image_version_id: string; base_sha256: string; revision_text: string
  quote_token: string; model: string; estimate_usd: string; max_cost_usd: string | null
}
export interface Execution {
  operation_id: string; run_id: string | null; attempt_id: string | null; state: ExecutionState
  created_at: string; finished_at: string | null; model: string; execution_mode: 'REAL' | 'TEST_SUBSTITUTE'
  candidate_ids: string[]; http_status: number | null; duration_ms: number | null
  error: { code: string; message: string } | null
  usage?: { input_tokens?: number; output_tokens?: number; total_tokens?: number } | null
  usage_cost_estimate?: { usd: string; checked_on: string } | null
  progress?: { stage?: string; steps?: { capability: string; status: string; reused: boolean;
    run_id: string | null; attempt_id: string | null; snapshot_id: string; operation_id: string }[] } | null
}
export interface SlotWorkspace {
  execution_mode?: 'REAL' | 'TEST_SUBSTITUTE'
  task_id: string; slot_id: string; state: ExecutionState; candidates: Candidate[]; executions: Execution[]
  readiness: {
    can_generate: boolean; reason: string; quote_token: string | null; model: string
    max_cost_usd: string | null; estimate_usd: string | null; decision_snapshot_id: string | null
    product_asset_id: string | null; reference_asset_id: string | null
    product_selection?: string
  }
  can_request_revision?: boolean; reviews?: ImageReview[]
  delivery?: (ImageReview & { url: string }) | null
}
const states = ['NOT_STARTED', 'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'OUTCOME_UNKNOWN']

export async function getSlotWorkspace(taskId: string, slotId: string, signal?: AbortSignal): Promise<SlotWorkspace> {
  const value = await request(`/tasks/${taskId}/slots/${slotId}/workspace`, { signal }) as SlotWorkspace
  if (!value || value.task_id !== taskId || value.slot_id !== slotId || !states.includes(value.state)
    || !value.readiness || typeof value.readiness.can_generate !== 'boolean'
    || !Array.isArray(value.candidates) || !Array.isArray(value.executions)
    || !value.candidates.every(c => c.task_id === taskId && c.slot_id === slotId
      && c.status === 'CANDIDATE_REVIEW_PENDING' && c.pass === (c.review_status === 'PASS') && c.operator_approved === c.pass
      && c.url === `/api/tasks/${taskId}/slots/${slotId}/versions/${c.image_version_id}/content`)
    || !value.executions.every(e => states.includes(e.state) && ['REAL', 'TEST_SUBSTITUTE'].includes(e.execution_mode))
    || !(value.reviews || []).every(r => r.task_id === taskId && r.slot_id === slotId && ['PASS', 'REJECT'].includes(r.decision))
    || (value.delivery && (value.delivery.task_id !== taskId || value.delivery.slot_id !== slotId
      || value.delivery.decision !== 'PASS' || !value.candidates.some(c => c.image_version_id === value.delivery?.image_version_id && c.pass)
      || value.delivery.url !== `/api/tasks/${taskId}/slots/${slotId}/delivery?review_id=${value.delivery.id}`))) {
    throw new ApiError('服务器返回的候选或执行记录不符合当前图位，未应用。', 'unknown')
  }
  return value
}

export function quoteRevision(taskId: string, slotId: string, baseId: string, revisionText: string): Promise<RevisionQuote> {
  return request(`/tasks/${taskId}/slots/${slotId}/versions/${baseId}/revision-quote`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision_text: revisionText }),
  }) as Promise<RevisionQuote>
}

export function reviseImage(taskId: string, slotId: string, operationId: string, quote: RevisionQuote) {
  return request(`/tasks/${taskId}/slots/${slotId}/revisions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
      operation_id: operationId, snapshot_id: quote.snapshot_id, quote_token: quote.quote_token,
    }),
  })
}

export function reviewImage(taskId: string, slotId: string, versionId: string, operationId: string,
  decision: 'PASS' | 'REJECT', reviewer: string, feedback: string, currentReviewId: string | null) {
  return request(`/tasks/${taskId}/slots/${slotId}/versions/${versionId}/reviews`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
      operation_id: operationId, decision, reviewer, feedback, expected_current_review_id: currentReviewId,
    }),
  })
}

export async function generateImage(taskId: string, slotId: string, operationId: string, quoteToken: string) {
  return request(`/tasks/${taskId}/slots/${slotId}/generations`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operation_id: operationId, quote_token: quoteToken }),
  })
}
