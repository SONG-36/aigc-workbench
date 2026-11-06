/** HTTP-only fake persistence for deterministic form tests; never an AI result. */
import { screen, waitFor } from '@testing-library/react'
import { expect } from 'vitest'
import userEvent from '@testing-library/user-event'
import type { TaskDraft } from '../types'

export const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
export async function chooseImages(user = userEvent.setup()) {
  const product = new File(['product'], 'product.png', { type: 'image/png', lastModified: 1 })
  const reference = new File(['reference'], 'reference.png', { type: 'image/png', lastModified: 2 })
  const input = await screen.findByLabelText('选择商品图片')
  await waitFor(() => expect(input).not.toBeDisabled())
  await user.upload(input, product)
  await user.upload(screen.getByLabelText('选择营销参考'), reference)
  return { product, reference }
}
export function taskHttp(initial: TaskDraft) {
  let saved = structuredClone(initial)
  const assetOps = new Map<string, string>()
  const handle = async (path: string, options?: RequestInit): Promise<Response> => {
    if (path.endsWith('/workspace')) return json({task_id: saved.id, slot_id: saved.slots[0].id,
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
    } else if (options?.method === 'POST' && path.endsWith('/slots')) {
      const body = JSON.parse(options.body as string)
      saved.slots = [{id: crypto.randomUUID(), task_id: saved.id, kind: body.kind, position: 0,
        reference_asset_id: null, output_size: '1024x1536', requirements: '', inputs_ready: false}]
    } else if (options?.method === 'PUT') {
      saved.slots[0] = {...saved.slots[0], ...JSON.parse(options.body as string), inputs_ready: true}
      saved.materials_ready = true
    }
    return json(saved, options?.method === 'POST' ? 201 : 200)
  }
  return {handle, saved: () => saved}
}
