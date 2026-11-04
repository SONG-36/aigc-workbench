import type { WriteAction } from '../hooks/useWriteAction'

export function WriteFeedback({ write }: { write: WriteAction }) {
  return <>
    {write.busy && <p role="status">正在保存，请稍候…</p>}
    {write.error && <p role="alert" className="error">{write.error}</p>}
    {write.unknown && <div className="notice">
      <p>保存结果未知，不代表服务器已撤销。可查询任务，或使用同一操作身份安全重试。</p>
      <button type="button" onClick={write.retry} disabled={write.busy}>安全重试本次保存</button>
    </div>}
  </>
}
