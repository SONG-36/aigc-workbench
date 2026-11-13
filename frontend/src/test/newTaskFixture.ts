/** HTTP-only fake persistence for deterministic form tests; never an AI result. */
import { screen, waitFor } from '@testing-library/react'
import { expect } from 'vitest'
import userEvent from '@testing-library/user-event'
import type { TaskDraft, ImageSlot } from '../types'

export const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
export async function chooseImages(user = userEvent.setup()) {
  const product = new File(['product'], 'product.png', { type: 'image/png', lastModified: 1 })
  const reference = new File(['reference'], 'reference.png', { type: 'image/png', lastModified: 2 })
  const input = await screen.findByLabelText('选择商品图片')
  await waitFor(() => expect(input).not.toBeDisabled())
  await user.upload(input, product)
  await user.upload(screen.getByLabelText('主图参考图片'), reference)
  return { product, reference }
}
export function taskHttp(initial: TaskDraft) {
  let saved = structuredClone(initial)
  let uploaded: string | undefined, created: string | undefined
  const slotOps = new Map<string,string>()
  const assetOps = new Map<string, string>()
  const requirementOps = new Set<string>()
  const materialOps=new Set<string>()
  const handle = async (path: string, options?: RequestInit): Promise<Response> => {
    uploaded = undefined; created = undefined
    if (path === '/api/tasks' && options?.method === 'POST') {
      const body = JSON.parse(options.body as string)
      if(body.mode==='listing')saved.listing_materials={id:crypto.randomUUID(),task_id:saved.id,version:1,
        listing_text:body.listing_text,supplemental_text:body.supplemental_text||'',saved_at:saved.created_at,scope:'TASK_ALL_SLOTS',source:'TEST_ONLY_OPERATOR_MATERIALS'}
      if (body.overall_requirements) saved.overall_requirement = {id: crypto.randomUUID(), task_id: saved.id, version: 1,
        text: body.overall_requirements, scope: 'TASK_ALL_SLOTS', saved_at: saved.created_at, source: 'EXPLICIT_OPERATOR_REQUIREMENT'}
    }
    if (path.endsWith('/workspace')) return json({task_id: saved.id, slot_id: path.split('/').at(-2),
      state: 'NOT_STARTED', candidates: [], executions: [], readiness: {can_generate: false, reason: 'TEST_ONLY'}})
    if (path.includes('/assets?')) {
      const url = new URL(path, 'http://localhost'), role = url.searchParams.get('role')!
      const op = url.searchParams.get('operation_id')!
      if (!assetOps.has(op)) {
        const id = crypto.randomUUID(); assetOps.set(op, id)
        saved.assets.push({id, task_id: saved.id, role: role as 'product_image' | 'marketing_reference',
          filename: url.searchParams.get('filename')!, sha256: 'a'.repeat(64), size_bytes: 10,
          width: 10, height: 10, media_type: 'image/png', created_at: saved.created_at,
          url: `/api/tasks/${saved.id}/assets/${id}/content`})
      }
      uploaded = assetOps.get(op)
    } else if (options?.method === 'POST' && path.endsWith('/slots')) {
      const body = JSON.parse(options.body as string)
      if (!slotOps.has(body.operation_id)) {
        const slot:ImageSlot = {id: crypto.randomUUID(), task_id: saved.id, kind: body.kind, position: saved.slots.length,
          reference_asset_id: null, output_size: '1024x1536', requirements: '', inputs_ready: saved.mode==='listing'}
        saved.slots.push(slot);slotOps.set(body.operation_id,slot.id)
      }
      created=slotOps.get(body.operation_id)
    } else if (options?.method === 'POST' && path.endsWith('/remove')) {
      const id=path.split('/').at(-2);saved.slots=saved.slots.map(s=>s.id===id?{...s,archived:true,inputs_ready:false}:s)
    } else if (options?.method === 'PUT' && path.endsWith('/listing-materials')) {
      const body=JSON.parse(options.body as string)
      if(!materialOps.has(body.operation_id)){
        if(body.expected_version!==(saved.listing_materials?.version||0))return json({error:{message:'商品资料版本已变化，请重新读取'}},409)
        materialOps.add(body.operation_id)
        saved.listing_materials={id:crypto.randomUUID(),task_id:saved.id,version:body.expected_version+1,
          listing_text:body.listing_text,supplemental_text:body.supplemental_text,saved_at:saved.created_at,scope:'TASK_ALL_SLOTS',source:'TEST_ONLY_OPERATOR_MATERIALS'}
      }
    } else if (options?.method === 'PUT' && path.endsWith('/overall-requirement')) {
      const body = JSON.parse(options.body as string)
      if (!requirementOps.has(body.operation_id)) {
        if (body.expected_version !== (saved.overall_requirement?.version || 0)) return json({error:{message:'要求版本已变化'}},409)
        requirementOps.add(body.operation_id)
        saved.overall_requirement = {id:crypto.randomUUID(),task_id:saved.id,text:body.text,scope:'TASK_ALL_SLOTS',
          version:body.expected_version+1,saved_at:saved.created_at,source:'EXPLICIT_OPERATOR_REQUIREMENT'}
      }
    } else if (options?.method === 'PUT') {
      const id=path.split('/').at(-1),body=JSON.parse(options.body as string)
      saved.slots=saved.slots.map(s=>s.id===id?{...s,...body,inputs_ready:saved.mode==='listing'||!!body.reference_asset_id}:s)
      saved.materials_ready = saved.slots.some(s=>!s.archived&&s.inputs_ready)
    }
    return json({...saved,...(uploaded?{uploaded_asset_id:uploaded}:{}),...(created?{created_slot_id:created}:{})}, options?.method === 'POST' ? 201 : 200)
  }
  return {handle, saved: () => saved}
}
