import { useState, type ReactNode } from 'react'
import type { TaskDraft } from '../types'

const slotLabels = ['主图', '尺寸图', '同类商品对比图'] as const

export function WorkspaceShell({ task, children }: { task: TaskDraft | null; children?: ReactNode }) {
  const [inputOpen, setInputOpen] = useState(true)

  return <>
    <div className="heading">
      <div><p className="eyebrow">COMMERCE IMAGE WORKSPACE</p>
        <h1>{task?.title || '新建商品图任务'}</h1>
        <p className="muted">共享商品资料，在统一工作台制作与审核图片。</p>
      </div>
      <span className="pill">{task ? '草稿 · 尚不具备制作条件' : '新建 Quick-A 草稿'}</span>
    </div>
    <p className="notice" role="status">当前仅支持 Quick-A 草稿保存与恢复。尚无商品和营销参考素材，制作功能未开放。</p>
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
        <p className="muted">当前只能创建 Quick-A 草稿；Quick-B / Listing 尚未开放。</p>
        {children}
        {task && <section className="saved-task" aria-label="已保存草稿">
          <h3>已保存的任务草稿</h3>
          <dl><dt>Task ID</dt><dd data-testid="task-id">{task.id}</dd>
            <dt>创建时间</dt><dd>{new Date(task.created_at).toLocaleString()}</dd>
            <dt>制作条件</dt><dd>尚未提供商品图与营销参考，不可生成。</dd></dl>
        </section>}
        <section className="unavailable" aria-label="商品素材区域">
          <h3>同 SKU 商品素材 <span className="feature-label">上传未开放</span></h3>
          <div className="asset-empty">尚未上传商品图片</div>
          <button type="button" disabled>＋ 添加商品图片（未开放）</button>
        </section>
        <section className="unavailable" aria-label="营销参考和图位配置区域">
          <h3>按图位配置营销参考 <span className="feature-label">布局展示 · 未建立图位</span></h3>
          <p className="muted">参考绑定和制作要求尚未开放，以下不是已保存的 Slot。</p>
          <div className="slot-grid">
            {slotLabels.map(label => <fieldset key={label} disabled className="slot-shell">
              <legend>{label}</legend>
              <div className="reference-empty">主要营销参考区域</div>
              <button type="button">添加一张主要参考（未开放）</button>
              {label === '尺寸图' && <p className="muted">制作须提供运营确认、已标注真实尺寸的专用白底素材。</p>}
              <label>输出尺寸<select defaultValue="1024x1536"><option>1024x1536</option></select></label>
              <label>补充制作要求<textarea rows={2} /></label>
            </fieldset>)}
          </div>
          <div className="dynamic-slots"><button type="button" disabled>＋ 添加卖点图（未开放）</button>
            <button type="button" disabled>＋ 添加使用场景图（未开放）</button></div>
        </section>
        <label className="unavailable">整体运营要求（未开放）<textarea disabled rows={3} /></label>
      </div>
    </details>
    <section id="output" aria-labelledby="output-title">
      <div className="section-head"><div><span className="eyebrow">OUTPUT</span><h2 id="output-title">逐图制作与审核</h2></div>
        <div className="actions"><button type="button" disabled>导出通过的图片（未开放）</button>
          <button type="button" disabled>导出实验包（未开放）</button></div></div>
      <div className="output-controls"><button type="button" disabled>生成图片（未开放）</button>
        <span className="muted">没有真实图位或候选结果，不会执行 AI。</span></div>
      <div className="empty">尚无候选图片。上传、参考绑定与制作将在后续阶段接通。</div>
      <p className="muted">历史版本、修改、Pass / Reject 与 Inspector 尚未接通，不提供模拟操作。</p>
    </section>
  </>
}
