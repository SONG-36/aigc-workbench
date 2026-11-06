export const OUTPUT_SIZES = ['1024x1536', '1024x1024', '1536x1024', '1536x1536'] as const
export type OutputSize = typeof OUTPUT_SIZES[number]

export type AssetRole = 'product_image' | 'marketing_reference'
export type SlotKind = 'main' | 'dimensions' | 'comparison' | 'selling_point' | 'scene'

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
  output_size: OutputSize
  requirements: string
  inputs_ready: boolean
  archived?: boolean
}

export interface OverallRequirement {
  id: string | null; task_id: string; version: number; text: string
  scope: 'TASK_ALL_SLOTS'; saved_at: string | null; source: string
}

export interface TaskDraft {
  id: string
  title: string
  overall_requirement?: OverallRequirement
  mode: 'quick_a'
  status: 'draft'
  can_generate: false
  materials_ready: boolean
  readiness_reason: string
  created_at: string
  assets: Asset[]
  slots: ImageSlot[]
}
