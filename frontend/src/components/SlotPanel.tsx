import { operationId as newOperationId } from '../utils/operationId'
import { useEffect, useState } from 'react'
import { configureSlot, createSlot } from '../api/tasks'
import type { WriteAction } from '../hooks/useWriteAction'
import { OUTPUT_SIZES, type ImageSlot, type OutputSize, type SlotKind, type TaskDraft } from '../types'

export const SLOT_KINDS: [SlotKind, string][] = [
  ['main', '主图'], ['comparison', '同类商品对比图'], ['selling_point', '卖点图'], ['scene', '使用场景图'],
]

function AddSlot({ task, kind, label, write, onSaved }: {
  task: TaskDraft; kind: SlotKind; label: string; write: WriteAction; onSaved: (task: TaskDraft) => void
}) {
  const [operationId, setOperationId] = useState(() => newOperationId())
  return <button type="button" className="add-slot" disabled={write.locked} onClick={() => {
    void write.run(() => createSlot(task.id, kind, operationId), updated => {
      onSaved(updated); setOperationId(newOperationId())
    })
  }}>＋ 添加{label}</button>
}

function SlotEditor({ task, slot, label, write, onSaved, onDirty }: {
  task: TaskDraft; slot: ImageSlot; label: string; write: WriteAction; onSaved: (task: TaskDraft) => void
  onDirty?: (slotId: string, dirty: boolean) => void
}) {
  const [reference, setReference] = useState(slot.reference_asset_id || '')
  const [size, setSize] = useState(slot.output_size)
  const [requirements, setRequirements] = useState(slot.requirements)
  useEffect(() => {
    setReference(slot.reference_asset_id || '')
    setSize(slot.output_size); setRequirements(slot.requirements)
  }, [slot.reference_asset_id, slot.output_size, slot.requirements])
  const references = task.assets.filter(a => a.role === 'marketing_reference')
  const selected = references.find(a => a.id === reference)
  const dirty = reference !== (slot.reference_asset_id || '') || size !== slot.output_size
    || requirements !== slot.requirements
  useEffect(() => { onDirty?.(slot.id, dirty) }, [slot.id, dirty, onDirty])
  useEffect(() => () => onDirty?.(slot.id, false), [slot.id, onDirty])
  return <form className="slot-shell" data-slot-id={slot.id} onSubmit={event => {
    event.preventDefault()
    void write.run(() => configureSlot(task.id, slot, {
      reference_asset_id: reference || null, output_size: size, requirements,
    }), onSaved)
  }}>
    <h4>{label}</h4>
    <p className="muted">{dirty ? '有未保存配置' : '图位配置已保存'} · {slot.inputs_ready ? '本图基础素材齐备' : '本图基础素材未齐备'}</p>
    {selected ? <img className="reference-preview" src={selected.url}
      alt={`${label}参考${dirty ? '待保存预览' : '已保存预览'}：${selected.filename}`} />
      : <div className="reference-empty">尚未绑定主要营销参考</div>}
    <fieldset disabled={write.locked}>
      <label>主要营销参考<select aria-label={`${label}主要营销参考`} value={reference}
        onChange={event => setReference(event.target.value)}>
        <option value="">尚未绑定</option>
        {references.map(a => <option key={a.id} value={a.id}>{a.filename}</option>)}
      </select></label>
      <label>输出尺寸<select value={size} onChange={event => setSize(event.target.value as OutputSize)}>
        {OUTPUT_SIZES.map(value => <option key={value} value={value}>
          {value === '1536x1536' ? '1536 × 1536（正方形）' : value}</option>)}
      </select></label>
      {size === '1536x1536' && <p className="muted">以 1024×1024 原生方图生成，再用 LANCZOS 等比例放大至 1536×1536；不裁切。</p>}
      <label>补充制作要求<textarea maxLength={2000} rows={2} value={requirements}
        onChange={event => setRequirements(event.target.value)} /></label>
      <button type="submit">保存{label}配置</button>
    </fieldset>
  </form>
}

export function SlotPanel({ task, write, onSaved, onDirty }: {
  task: TaskDraft; write: WriteAction; onSaved: (task: TaskDraft) => void
  onDirty?: (slotId: string, dirty: boolean) => void
}) {
  function group(kind: SlotKind, label: string) {
    const slots = task.slots.filter(s => s.kind === kind)
    const fixed = kind === 'main' || kind === 'comparison'
    return <div className="slot-group" key={kind}><h4>{label} · {slots.length} 个</h4>
      <div className={fixed ? 'fixed-slot' : 'slot-grid'}>{slots.map((s, index) =>
        <SlotEditor key={s.id} task={task} slot={s} label={fixed ? label : `${label} ${index + 1}`}
          write={write} onSaved={onSaved} onDirty={onDirty} />)}
        {(!fixed || slots.length === 0) && <AddSlot task={task} kind={kind} label={label}
          write={write} onSaved={onSaved} />}</div>
    </div>
  }
  return <section aria-label="营销参考和图位配置区域">
    <h3>按图位配置营销参考</h3>
    <p className="muted">每个图位独立绑定当前任务的营销参考。替换绑定保留原素材；保存不会制作图片。</p>
    <div className="fixed-slots">
      {group('main', '主图')}
      <div className="slot-group"><h4>尺寸图</h4><div className="slot-shell unavailable">
        <p className="muted">专用尺寸素材及运营确认流程尚未接通；本轮不创建尺寸图，不影响其他图位保存。</p>
      </div></div>
      {group('comparison', '同类商品对比图')}
    </div>
    {SLOT_KINDS.filter(([kind]) => kind === 'selling_point' || kind === 'scene')
      .map(([kind, label]) => group(kind, label))}
  </section>
}
