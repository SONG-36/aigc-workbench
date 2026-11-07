import { Link, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { useEffect } from 'react'
import { TaskCenter } from './pages/TaskCenter'
import { Workspace } from './pages/Workspace'
import { Icon } from './components/Icon'

export function App() {
  const {pathname}=useLocation()
  const working = pathname.startsWith('/workspace/')
  useEffect(()=>{if(document.scrollingElement)document.scrollingElement.scrollTop=0},[pathname])
  return <>
    <header className="topbar"><Link className="brand" to="/"><span className="workspace-mark"><Icon name="layers" /></span><span>AIGC Workbench<small>商品视觉创作工作台</small></span></Link>
      <nav aria-label="主导航"><NavLink to="/" end>任务中心</NavLink><span className={working ? 'nav-current' : ''}>创作工作台</span>
        <button type="button" disabled title="开发者 Inspector 尚未实现">Inspector（未开放）</button></nav></header>
    <main><Routes><Route path="/" element={<TaskCenter />} /><Route path="/workspace/new" element={<Workspace />} />
      <Route path="/workspace/:taskId" element={<Workspace />} /><Route path="*" element={<p role="alert">页面不存在。<Link to="/">返回任务中心</Link></p>} /></Routes></main>
    <footer>AIGC Workbench · 真实素材驱动创作，人工审核后交付。</footer>
  </>
}
