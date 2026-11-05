import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/tasks'
import { generateImage, getSlotWorkspace, type SlotWorkspace } from '../api/generation'
import type { ImageSlot, TaskDraft } from '../types'

/** Reads may poll; a paid POST only occurs once inside the explicit submit action. */
export function useSlotGeneration(task: TaskDraft, slot: ImageSlot) {
  const [data, setData] = useState<SlotWorkspace | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [revision, setRevision] = useState(0)
  const pendingOperation = useRef<string | null>(null)
  const locked = useRef(false)
  const mounted = useRef(false)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    async function read() {
      try {
        const next = await getSlotWorkspace(task.id, slot.id, controller.signal)
        if (controller.signal.aborted) return
        setData(next)
        if (pendingOperation.current && next.executions.some(e => e.operation_id === pendingOperation.current)) {
          pendingOperation.current = null; setUncertain(false)
        }
        setError(null)
        if (pendingOperation.current || next.executions.some(e => e.state === 'RUNNING')) {
          timer = setTimeout(() => void read(), 1500)
        }
      } catch (cause) {
        if (controller.signal.aborted) return
        setError(cause instanceof Error ? cause.message : '候选读取失败。')
        timer = setTimeout(() => void read(), 5000) // Read-only recovery; never retries a paid operation.
      }
    }
    void read()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [task.id, slot.id, slot.reference_asset_id, slot.output_size, slot.requirements, task.assets.length, revision])

  async function submit() {
    if (locked.current || uncertain || !data?.readiness.can_generate || !data.readiness.quote_token) return
    locked.current = true
    const operation = crypto.randomUUID()
    pendingOperation.current = operation
    setSubmitting(true); setError(null)
    try {
      await generateImage(task.id, slot.id, operation, data.readiness.quote_token)
    } catch (cause) {
      if (!mounted.current) return
      const unknown = !(cause instanceof ApiError) || cause.outcome === 'unknown'
      if (!unknown) pendingOperation.current = null
      setUncertain(unknown)
      setError(cause instanceof Error ? cause.message : '提交结果未知，请查询服务器。')
    } finally {
      locked.current = false
      if (mounted.current) { setSubmitting(false); setRevision(value => value + 1) }
    }
  }
  return { data, error, submitting, uncertain, submit, refresh: () => setRevision(value => value + 1) }
}
