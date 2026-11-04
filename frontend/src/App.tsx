import { Link, NavLink, Route, Routes } from 'react-router-dom'
import { TaskCenter } from './pages/TaskCenter'
import { Workspace } from './pages/Workspace'

export function App() {
  return <>
    <header className="topbar"><Link className="brand" to="/"><span className="workspace-mark" aria-hidden="true">图</span>商品图工作台</Link>
      <nav><NavLink to="/" end>任务中心</NavLink><span>统一工作台</span>
        <button type="button" disabled title="开发者 Inspector 尚未实现">Inspector（未开放）</button></nav></header>
    <main><Routes>
      <Route path="/" element={<TaskCenter />} />
      <Route path="/workspace/new" element={<Workspace />} />
      <Route path="/workspace/:taskId" element={<Workspace />} />
      <Route path="*" element={<p role="alert">页面不存在。<Link to="/">返回任务中心</Link></p>} />
    </Routes><footer>当前版本仅支持 Quick-A 草稿创建与恢复 · 尚不具备图片制作条件</footer></main>
  </>
}
