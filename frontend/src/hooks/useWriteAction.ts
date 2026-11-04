import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/tasks'
import type { TaskDraft } from '../types'

type Pending = { action: () => Promise<TaskDraft>; success: (task: TaskDraft) => void }

/** One explicit write at a time; leaving a page never rolls back the server. */
export function useWriteAction() {
  const active = useRef(false)
  const generation = useRef(0)
  const inFlight = useRef(false)
  const pending = useRef<Pending | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [unknown, setUnknown] = useState(false)

  useEffect(() => {
    active.current = true
    return () => { active.current = false; generation.current += 1 }
  }, [])

  async function run(action: Pending['action'], success: Pending['success']) {
    if (!active.current || inFlight.current) return
    inFlight.current = true
    const token = ++generation.current
    pending.current = { action, success }
    setBusy(true); setError(null); setUnknown(false)
    try {
      const task = await action()
      if (active.current && token === generation.current) {
        pending.current = null
        success(task)
      }
    } catch (cause) {
      if (active.current && token === generation.current) {
        const uncertain = !(cause instanceof ApiError) || cause.outcome === 'unknown'
        setUnknown(uncertain)
        if (!uncertain) pending.current = null
        setError(cause instanceof Error ? cause.message : '保存结果未知，请核对后处理。')
      }
    } finally {
      if (active.current && token === generation.current) {
        inFlight.current = false
        setBusy(false)
      }
    }
  }

  function retry() {
    if (pending.current) void run(pending.current.action, pending.current.success)
  }

  return { busy, error, unknown, locked: busy || unknown, run, retry }
}

export type WriteAction = ReturnType<typeof useWriteAction>
