import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { RevisionPanel, ReviewPanel, type RevisionDrafts } from '../components/RevisionReviewPanel'
import { getSlotWorkspace, type Candidate, type SlotWorkspace, type RevisionQuote } from '../api/generation'

const base: Candidate = {
  image_version_id: 'v1', candidate_id: 'c1', task_id: 't', slot_id: 's', run_id: 'r1',
  created_at: '2026-11-05T00:00:00Z', product_asset_id: 'p', reference_asset_id: 'ref',
  status: 'CANDIDATE_REVIEW_PENDING', operator_approved: false, pass: false, review_status: 'PENDING',
  image: { width: 1024, height: 1536, size_bytes: 100, sha256: 'a'.repeat(64) },
  url: '/api/tasks/t/slots/s/versions/v1/content',
}
const data: SlotWorkspace = { task_id: 't', slot_id: 's', state: 'SUCCEEDED', candidates: [base], executions: [],
  can_request_revision: true, reviews: [], delivery: null,
  readiness: { can_generate: true, reason: 'ready', quote_token: 'x', model: 'test', max_cost_usd: '1.00',
    estimate_usd: '0.9', decision_snapshot_id: 'd', product_asset_id: 'p', reference_asset_id: 'ref' } }
const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200 })

function DraftHarness({ submit }: { submit: (quote: RevisionQuote) => Promise<void> }) {
  const [selected, setSelected] = useState('v1')
  const [draft, setDraft] = useState<RevisionDrafts>({ baseId: '', texts: {} })
  return <><button onClick={() => setSelected(selected === 'v1' ? 'v2' : 'v1')}>切换预览</button>
    <RevisionPanel taskId="t" slotId="s" candidate={{ ...base, image_version_id: selected }} data={data}
      draft={draft} setDraft={setDraft} select={setSelected} disabled={false} submit={submit} /></>
}

describe('C07 explicit base, paid action and manual review boundaries', () => {
  it('keeps separate drafts per explicitly selected base and never retargets a draft on preview', async () => {
    const submit = vi.fn(); render(<DraftHarness submit={submit} />)
    const user = userEvent.setup()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    await user.click(screen.getByText('以当前查看版本为修改基础'))
    await user.type(screen.getByLabelText('本轮修改意见'), 'Only headline V1')
    await user.click(screen.getByText('切换预览'))
    expect(screen.getByLabelText('本轮修改意见')).toHaveValue('Only headline V1')
    expect(screen.getByLabelText('本轮修改意见')).toBeDisabled()
    await user.click(screen.getByText('以当前查看版本为修改基础'))
    expect(screen.getByLabelText('本轮修改意见')).toHaveValue('')
    await user.type(screen.getByLabelText('本轮修改意见'), 'Only background V2')
    await user.click(screen.getByText('切换预览'))
    await user.click(screen.getByText('以当前查看版本为修改基础'))
    expect(screen.getByLabelText('本轮修改意见')).toHaveValue('Only headline V1')
    expect(submit).not.toHaveBeenCalled()
  })

  it('one manual edit click prepares inputs and submits without fee approval', async () => {
    const fetch = vi.fn().mockImplementation((_p, options) => {
      const text = JSON.parse(options.body).revision_text
      return Promise.resolve(response({ snapshot_id: 'frozen', base_image_version_id: 'v1', revision_text: text,
        base_sha256: 'a'.repeat(64), quote_token: 'quote', model: 'fixed', estimate_usd: '0.91', max_cost_usd: '1.00' }))
    }); vi.stubGlobal('fetch', fetch)
    const submit = vi.fn().mockResolvedValue(undefined); render(<DraftHarness submit={submit} />)
    const user = userEvent.setup()
    await user.click(screen.getByText('以当前查看版本为修改基础'))
    await user.type(screen.getByLabelText('本轮修改意见'), 'Color only')
    await user.dblClick(screen.getByText('修改这张图片'))
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByText('确认一次付费修改')).not.toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][0]).toContain('/versions/v1/revision-quote')
  })

  it('does not dispatch a late preflight after the operator leaves the component', async () => {
    let resolve!: (value: Response) => void
    const pending = new Promise<Response>(yes => { resolve = yes })
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(pending))
    const submit = vi.fn().mockResolvedValue(undefined)
    const component = render(<DraftHarness submit={submit} />); const user = userEvent.setup()
    await user.click(screen.getByText('以当前查看版本为修改基础'))
    await user.type(screen.getByLabelText('本轮修改意见'), 'A')
    await user.click(screen.getByText('修改这张图片'))
    component.unmount()
    resolve(response({ snapshot_id: 'frozen', base_image_version_id: 'v1', revision_text: 'A', quote_token: 'q' }))
    await pending
    expect(submit).not.toHaveBeenCalled()
  })

  it('saves one explicit review of the exact version with expected delivery identity', async () => {
    const fetch = vi.fn().mockResolvedValue(response({})); vi.stubGlobal('fetch', fetch)
    const refresh = vi.fn()
    render(<ReviewPanel taskId="t" slotId="s" candidate={base} data={data} disabled={false} refresh={refresh} />)
    const user = userEvent.setup()
    expect(screen.getByText('Pass 此版本')).toBeDisabled()
    await user.type(screen.getByLabelText('操作者标签'), 'Tester')
    await user.type(screen.getByLabelText('审核反馈'), 'Visually checked synthetic image')
    await user.click(screen.getByText('Pass 此版本'))
    expect(fetch).not.toHaveBeenCalled()
    await user.dblClick(screen.getByText('确认 PASS'))
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
    expect(fetch.mock.calls[0][0]).toContain('/versions/v1/reviews')
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toMatchObject({ decision: 'PASS', reviewer: 'Tester', expected_current_review_id: null })
  })

  it('rejects a delivery URL which is not the server-scoped current Pass', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ ...data, delivery: { id: 'bad', task_id: 't',
      slot_id: 's', decision: 'PASS', image_version_id: 'v1', url: '/arbitrary/download' } })))
    await expect(getSlotWorkspace('t', 's')).rejects.toThrow('不符合当前图位')
  })
})
