import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useNewQuickATask } from '../hooks/useNewQuickATask'
import { OUTPUT_SIZES, type Asset, type OutputSize, type SlotKind } from '../types'
import { SLOT_KINDS } from './SlotPanel'

function DraftImage({ label, file, saved, expectedName, disabled, onChange }: {
  label: string; file: File | null; saved?: Asset; expectedName?: string; disabled: boolean
  onChange: (file: File | null) => void
}) {
  const [preview, setPreview] = useState<string | null>(null)
  useEffect(() => {
    if (!file || typeof URL.createObjectURL !== 'function') { setPreview(null); return }
    const url = URL.createObjectURL(file); setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])
  return <section className="asset-section" aria-label={`${label}新建资料`}>
    <h3>{label}</h3>
    {saved ? <figure><img className="reference-preview" src={saved.url} alt={saved.filename} />
      <figcaption>{saved.filename} · 已保存到服务器</figcaption></figure> : <>
      <label className="upload-label">选择{label}<input type="file" aria-label={`选择${label}`}
        accept="image/png,image/jpeg,image/webp" disabled={disabled} required={!file}
        onChange={e => { if (e.target.files?.[0]) onChange(e.target.files[0]); e.target.value = '' }} /></label>
      {file && <div className="pending-asset"><p>{file.name} · 待保存，尚未上传服务器</p>
        {preview && <img src={preview} alt={`待上传预览：${file.name}`} />}</div>}
      {!file && expectedName && <p className="notice">待上传文件 {expectedName} 尚未恢复，请重新选择；已保存资料不会重复上传。</p>}
    </>}
    <p className="muted">JPEG / PNG / WebP，单张最多 10 MiB。实售商品图决定外观，营销参考按 Quick-A 同款同配置前提使用。</p>
  </section>
}

export function TaskForm() {
  const form = useNewQuickATask()
  const { draft, files, task, busy, hydrating, error } = form
  const saved = (id: string | null) => task?.assets.find(a => a.id === id)
  return <form onSubmit={event => { event.preventDefault(); void form.save() }} className="draft-form">
    {draft.started && <p className="notice">正在恢复同一次保存。成功保存的资料会跳过，未完成阶段保留原操作身份；不会自动提交。</p>}
    <label htmlFor="task-title">任务名称</label>
    <input id="task-title" value={draft.title} onChange={e => form.edit({ title: e.target.value })}
      maxLength={120} required disabled={busy || draft.started} placeholder="为当前目标商品填写一个任务名称" />
    <div className="asset-columns">
      <DraftImage label="商品图片" file={files.product} saved={saved(draft.productId)} expectedName={draft.fileInfo.product?.name}
        disabled={!form.canChoose('product')} onChange={file => form.choose('product', file)} />
      <DraftImage label="营销参考" file={files.reference} saved={saved(draft.referenceId)} expectedName={draft.fileInfo.reference?.name}
        disabled={!form.canChoose('reference')} onChange={file => form.choose('reference', file)} />
    </div>
    <fieldset disabled={busy || hydrating || draft.started} className="slot-shell">
      <legend>首张图位配置</legend>
      <label>图位类型<select aria-label="新建图位类型" value={draft.kind} onChange={e => form.edit({ kind: e.target.value as SlotKind })}>
        {SLOT_KINDS.map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}
      </select></label>
      <label>输出尺寸<select aria-label="新建输出尺寸" value={draft.size} onChange={e => form.edit({ size: e.target.value as OutputSize })}>
        {OUTPUT_SIZES.map(size => <option key={size} value={size}>{size === '1536x1536' ? '1536 × 1536（正方形）' : size}</option>)}
      </select></label>
      {draft.size === '1536x1536' && <p className="muted">以 1024×1024 原生方图生成，再用 LANCZOS 等比例放大至 1536×1536；不裁切。</p>}
      <label>本图补充制作要求<textarea aria-label="新建图位要求" rows={3} maxLength={2000} value={draft.requirements}
        onChange={e => form.edit({ requirements: e.target.value })} /></label>
    </fieldset>
    <p className="muted">一次保存任务、两份原始素材及图位配置，随后进入工作台；保存不会调用模型。</p>
    {form.cacheWarning && <p className="notice">{form.cacheWarning}</p>}
    {busy && <p role="status">{form.stage}…</p>}
    {error && <p role="alert" className="error">{error.stage}失败：{error.message}
      {error.unknown ? ' 结果尚未确认；继续保存会复用原操作身份，不重复创建。' : ' 文件仍保留在本页。'}</p>}
    {task && error && <p><Link to={`/workspace/${task.id}`}>打开已保存部分，核对尚未完成的资料</Link></p>}
    <div className="actions"><button type="submit" className="primary" disabled={busy || hydrating}>
      {busy ? '正在保存任务和资料…' : error ? '继续保存任务和资料' : '保存任务和资料，进入工作台'}
    </button></div>
  </form>
}
