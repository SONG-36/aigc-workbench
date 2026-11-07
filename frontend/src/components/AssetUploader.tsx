import { operationId as newOperationId } from '../utils/operationId'
import { useEffect, useState } from 'react'
import { uploadAsset } from '../api/tasks'
import type { WriteAction } from '../hooks/useWriteAction'
import type { AssetRole, TaskDraft } from '../types'
import { FilePicker } from './FilePicker'

export function AssetUploader({ task, role, write, onSaved }: {
  task: TaskDraft; role: AssetRole; write: WriteAction; onSaved: (task: TaskDraft) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [operationId, setOperationId] = useState(() => newOperationId())
  const label = role === 'product_image' ? '商品图片' : '营销参考'
  const saved = task.assets.filter(asset => asset.role === role)

  useEffect(() => {
    if (!file || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
      setPreview(null); return
    }
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  return <section aria-label={`${label}素材区域`} className="asset-section">
    <h3>{label} <span className="feature-label">已保存 {saved.length} 张</span></h3>
    <div className="asset-grid">{saved.map(asset => <figure key={asset.id} data-asset-id={asset.id}>
      <a href={asset.url} target="_blank" rel="noreferrer"><img src={asset.url} alt={asset.filename} /></a>
      <figcaption>{asset.filename}<br /><span className="muted">服务端已保存 · {asset.width}×{asset.height}</span></figcaption>
    </figure>)}</div>
    <FilePicker label={`选择${label}`} action={`添加${label}`} compact={saved.length > 0} disabled={write.locked}
      onSelect={file=>{setFile(file);setOperationId(newOperationId())}} />
    <p className="muted">JPEG / PNG / WebP，单张最多 10 MiB、2500 万像素；可逐张添加多张素材。原文件不覆盖。</p>
    {file && <div className="pending-asset">
      <p>{file.name} · {write.busy ? '有保存操作进行中；此处尚未确认保存' : '待上传文件，本地预览不是已保存素材'}</p>
      {preview && <img src={preview} alt={`待上传预览：${file.name}`} />}
      <button type="button" className="primary" disabled={write.locked} onClick={() => {
        void write.run(() => uploadAsset(task.id, role, file, operationId), updated => {
          onSaved(updated); setFile(null)
        })
      }}>上传并保存{label}</button>
    </div>}
  </section>
}
