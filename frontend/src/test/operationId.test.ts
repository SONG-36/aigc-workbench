import { describe, expect, it, vi } from 'vitest'
import { operationId } from '../utils/operationId'

describe('LAN HTTP operation identity', () => {
  it('creates a unique UUID v4 without requiring secure-context randomUUID', () => {
    const getRandomValues = crypto.getRandomValues.bind(crypto)
    vi.stubGlobal('crypto', { getRandomValues })
    const first = operationId(), second = operationId()
    expect(first).toMatch(/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/)
    expect(first).not.toBe(second)
  })
})
