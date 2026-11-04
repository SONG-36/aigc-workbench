export interface TaskDraft {
  id: string
  title: string
  mode: 'quick_a'
  status: 'draft'
  can_generate: false
  readiness_reason: 'missing_product_and_reference_assets'
  created_at: string
}
