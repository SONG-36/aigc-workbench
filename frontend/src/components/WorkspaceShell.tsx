import { useState, type ReactNode } from 'react'
import type { TaskDraft } from '../types'

export function WorkspaceShell({ task, children, output }: { task: TaskDraft | null; children?: ReactNode; output?: ReactNode }) {
  const [inputOpen, setInputOpen] = useState(true)
  return <>
    <div className="heading">
      <div><p className="eyebrow">COMMERCE IMAGE WORKSPACE</p>
        <h1>{task?.title || '新建商品图任务'}</h1>
        <p className="muted">共享商品资料，在统一工作台制作与审核图片。</p>
      </div><span className="pill">Quick-A · 准备草稿</span>
    </div>
    <p className="notice" role="status">可查看历史候选、选择基础版本修改，并人工 Pass / Reject。生成和修改由手动点击触发，下载以本图位当前有效 Pass 为准。</p>
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
            <dt>基础素材</dt><dd>{task.materials_ready ? '已有图位基础素材齐备；生成条件以当前图位的服务器检查为准。'
              : '尚未提供商品图与营销参考，或图位绑定未齐备；不可生成。'}</dd></dl>
        </section>}
        {children}
        <label className="unavailable">整体运营要求（后续接通）<textarea disabled rows={3} /></label>
      </div>
    </details>
    <section id="output" aria-labelledby="output-title">
      <div className="section-head"><div><span className="eyebrow">OUTPUT</span><h2 id="output-title">逐图制作与审核</h2></div>
        <div className="actions"><button type="button" disabled>批量导出通过图片（未开放）</button>
          <button type="button" disabled>导出实验包（未开放）</button></div></div>
      {output || <><div className="output-controls"><button type="button" disabled>生成图片（未开放）</button>
        <span className="muted">请先保存任务、素材和图位，再检查本图的生成条件。</span></div>
      <div className="empty">尚无候选图片。当前仅完成真实素材与图位准备。</div>
      <p className="muted">保存任务及图位后，可在候选区查看版本、修改与人工审核；Inspector 尚未开放。</p></>}
    </section>
  </>
}
