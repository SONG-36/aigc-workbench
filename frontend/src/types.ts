export type AssetRole = 'product_image' | 'marketing_reference'
export type SlotKind = 'main' | 'comparison' | 'selling_point' | 'scene'

export interface Asset {
  id: string
  task_id: string
  role: AssetRole
  filename: string
  sha256: string
  size_bytes: number
  media_type: string
  width: number
  height: number
  created_at: string
  url: string
}

export interface ImageSlot {
  id: string
  task_id: string
  kind: SlotKind
  position: number
  reference_asset_id: string | null
  output_size: string
  requirements: string
  inputs_ready: boolean
}

export interface TaskDraft {
  id: string
  title: string
  mode: 'quick_a'
  status: 'draft'
  can_generate: false
  materials_ready: boolean
  readiness_reason: string
  created_at: string
  assets: Asset[]
  slots: ImageSlot[]
}
