import { useEffect, useRef, useState } from 'react'
import { getTask, saveOverallRequirement } from '../api/tasks'
import type { TaskDraft } from '../types'
import type { WriteAction } from '../hooks/useWriteAction'
import { operationId } from '../utils/operationId'

export function OverallRequirements({task, write, onSaved, onDirty}: {
  task: TaskDraft; write: WriteAction; onSaved: (task: TaskDraft) => void; onDirty: (dirty: boolean) => void
}) {
  const serverText = task.overall_requirement?.text || '', version = task.overall_requirement?.version || 0
  const [text, setText] = useState(serverText), [saved, setSaved] = useState(false)
  const edited = useRef(false)
  const dirty = text !== serverText
  useEffect(() => {if (!edited.current) setText(serverText)}, [serverText, version])
  useEffect(() => {onDirty(dirty); return () => onDirty(false)}, [dirty, onDirty])
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {if (dirty) {event.preventDefault();event.returnValue = ''}}
    window.addEventListener('beforeunload', guard); return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])
  return <section className="overall-requirements" aria-label="整体运营要求设置">
    <label>整体运营要求<textarea aria-label="整体运营要求" rows={3} maxLength={2000} value={text}
      disabled={write.locked} onChange={e => {edited.current = true;setText(e.target.value);setSaved(false)}} /></label>
    <p className="muted">服务器版本 V{version} · 本任务全部图位。仅约束后续生成和显式修改，与逐图要求共同生效；不自动重做图片或改变已有 Pass。</p>
    <div className="actions"><button type="button" disabled={write.locked || !dirty}
      onClick={() => {const input = text, expected = version, operation = operationId();void write.run(
        () => saveOverallRequirement(task.id, input, expected, operation),
        next => {edited.current = false;setText(next.overall_requirement?.text || '');onSaved(next);setSaved(true)})}}>保存整体运营要求</button>
      <button type="button" disabled={write.locked} onClick={() => void write.run(() => getTask(task.id), onSaved)}>读取服务器要求版本</button>
    </div>
    {dirty && <p className="notice">整体要求尚未保存，生成与修改暂不可提交；输入不会被服务器刷新静默覆盖。</p>}
    {saved && <p role="status">整体运营要求已保存到服务器。</p>}
  </section>
}
