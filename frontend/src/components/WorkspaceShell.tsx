import { useState, type ReactNode } from 'react'
import type { TaskDraft } from '../types'

export function WorkspaceShell({ task, children }: { task: TaskDraft | null; children?: ReactNode }) {
  const [inputOpen, setInputOpen] = useState(true)
  return <>
    <div className="heading">
      <div><p className="eyebrow">COMMERCE IMAGE WORKSPACE</p>
        <h1>{task?.title || '新建商品图任务'}</h1>
        <p className="muted">共享商品资料，在统一工作台制作与审核图片。</p>
      </div><span className="pill">Quick-A · 准备草稿</span>
    </div>
    <p className="notice" role="status">当前可保存实际素材与图位配置。图片理解、生成、修改与审核均未开放。</p>
    <details id="input-panel" open={inputOpen} onToggle={event => setInputOpen(event.currentTarget.open)}>
      <summary><span className="eyebrow">INPUT</span><strong>{task?.title || '共享资料与商品理解'}</strong>
        <span className="muted">按需展开</span></summary>
      <div className="input-content">
        <h2>同 SKU 商品素材与图位参考</h2>
        <label htmlFor="task-mode">工作模式</label>
        <select id="task-mode" value="quick_a" disabled>
          <option value="quick_a">Quick-A｜同款快速改版</option>
          <option value="quick_b" disabled>Quick-B｜换主体快速改版（未开放）</option>
          <option value="listing" disabled>Listing｜商品资料制图（未开放）</option>
        </select>
        <p className="muted">Quick-A 使用实际商品图和同款同配置营销参考，无需填写 Listing。</p>
        {task && <section className="saved-task" aria-label="已保存草稿">
          <h3>已保存的任务</h3>
          <dl><dt>Task ID</dt><dd data-testid="task-id">{task.id}</dd>
            <dt>创建时间</dt><dd>{new Date(task.created_at).toLocaleString()}</dd>
            <dt>基础素材</dt><dd>{task.materials_ready ? '已有图位基础素材齐备；图片生成仍未开放。'
              : '尚未提供商品图与营销参考，或图位绑定未齐备；不可生成。'}</dd></dl>
        </section>}
        {children}
        <label className="unavailable">整体运营要求（后续接通）<textarea disabled rows={3} /></label>
      </div>
    </details>
    <section id="output" aria-labelledby="output-title">
      <div className="section-head"><div><span className="eyebrow">OUTPUT</span><h2 id="output-title">逐图制作与审核</h2></div>
        <div className="actions"><button type="button" disabled>导出通过的图片（未开放）</button>
          <button type="button" disabled>导出实验包（未开放）</button></div></div>
      <div className="output-controls"><button type="button" disabled>生成图片（未开放）</button>
        <span className="muted">素材保存与 AI 执行分开；本版本不会执行 AI。</span></div>
      <div className="empty">尚无候选图片。当前仅完成真实素材与图位准备。</div>
      <p className="muted">历史版本、修改、Pass / Reject 与 Inspector 尚未接通，不提供模拟操作。</p>
    </section>
  </>
}
