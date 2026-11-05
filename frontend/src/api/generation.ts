import { ApiError, request } from './tasks'

export type ExecutionState = 'NOT_STARTED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'OUTCOME_UNKNOWN'
export interface Candidate {
  image_version_id: string; candidate_id: string; task_id: string; slot_id: string; run_id: string
  created_at: string; product_asset_id: string; reference_asset_id: string
  status: 'CANDIDATE_REVIEW_PENDING'; operator_approved: false; pass: false; url: string
  image: { width: number; height: number; size_bytes: number; sha256: string }
}
export interface Execution {
  operation_id: string; run_id: string | null; attempt_id: string | null; state: ExecutionState
  created_at: string; finished_at: string | null; model: string; execution_mode: 'REAL' | 'TEST_SUBSTITUTE'
  candidate_ids: string[]; http_status: number | null; duration_ms: number | null
  error: { code: string; message: string } | null
}
export interface SlotWorkspace {
  execution_mode?: 'REAL' | 'TEST_SUBSTITUTE'
  task_id: string; slot_id: string; state: ExecutionState; candidates: Candidate[]; executions: Execution[]
  readiness: {
    can_generate: boolean; reason: string; quote_token: string | null; model: string
    max_cost_usd: string; estimate_usd: string | null; decision_snapshot_id: string | null
    product_asset_id: string | null; reference_asset_id: string | null
  }
}
const states = ['NOT_STARTED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'OUTCOME_UNKNOWN']

export async function getSlotWorkspace(taskId: string, slotId: string, signal?: AbortSignal): Promise<SlotWorkspace> {
  const value = await request(`/tasks/${taskId}/slots/${slotId}/workspace`, { signal }) as SlotWorkspace
  if (!value || value.task_id !== taskId || value.slot_id !== slotId || !states.includes(value.state)
    || !value.readiness || typeof value.readiness.can_generate !== 'boolean'
    || !Array.isArray(value.candidates) || !Array.isArray(value.executions)
    || !value.candidates.every(c => c.task_id === taskId && c.slot_id === slotId
      && c.status === 'CANDIDATE_REVIEW_PENDING' && c.pass === false && c.operator_approved === false
      && c.url === `/api/tasks/${taskId}/slots/${slotId}/versions/${c.image_version_id}/content`)
    || !value.executions.every(e => states.includes(e.state) && ['REAL', 'TEST_SUBSTITUTE'].includes(e.execution_mode))) {
    throw new ApiError('服务器返回的候选或执行记录不符合当前图位，未应用。', 'unknown')
  }
  return value
}

export async function generateImage(taskId: string, slotId: string, operationId: string, quoteToken: string) {
  return request(`/tasks/${taskId}/slots/${slotId}/generations`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operation_id: operationId, quote_token: quoteToken, confirm_paid: true, max_cost_usd: '1.00' }),
  })
}
