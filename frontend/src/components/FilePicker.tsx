import { Icon } from './Icon'
import { useState } from 'react'

/** A styled label keeps the native file input keyboard- and screen-reader accessible. */
export function FilePicker({ label, action, disabled, compact = false, onSelect }: {
  label: string; action: string; disabled: boolean; compact?: boolean; onSelect: (file: File) => void
}) {
  const [error,setError]=useState('')
  return <><label className={`file-picker ${compact ? 'file-picker--compact' : ''} ${disabled ? 'is-disabled' : ''}`}>
    <span className="upload-symbol"><Icon name="upload" /></span>
    <span><strong>{action}</strong>{!compact && <span className="file-picker-hint">JPEG、PNG 或 WebP · 单张不超过 10 MiB</span>}</span>
    <input className="file-picker-input" type="file" aria-label={label} accept="image/png,image/jpeg,image/webp" disabled={disabled}
      onChange={event => {
        const file=event.target.files?.[0]
        if(file){if(['image/png','image/jpeg','image/webp'].includes(file.type)){setError('');onSelect(file)}else setError('请选择 JPEG、PNG 或 WebP 图片；当前素材未被替换。')}
        event.target.value=''
      }} />
  </label>{error&&<p role="alert" className="error">{error}</p>}</>
}
