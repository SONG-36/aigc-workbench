import {useState} from 'react'
import {Icon} from '../components/Icon'
import {Overlay} from '../components/ImageOverlay'
import {CaseGallery} from './CaseGallery'
import {StudioOverview} from './StudioOverview'
import {workflow} from './content'
import type {PublicCase} from './manifest'

const capabilities = [
  {icon:'image' as const,title:'以实售商品为外观依据',text:'从实际商品图片出发。同款营销参考提供营销内容与设计参考，商品主体与附件仍以实售商品为准。'},
  {icon:'grid' as const,title:'每个图位，独立制作',text:'主图、卖点图和场景图各自配置参考、输出尺寸与要求，在同一工作台查看对应成果。'},
  {icon:'layers' as const,title:'修改有基础，审核有版本',text:'选定基础版本提出修改意见，新候选独立保存。人工审核后，下载当前有效 Pass 图片。'},
]
export function Showcase({cases}:{cases:PublicCase[]}) {
  const [layoutOpen,setLayoutOpen] = useState(false)
  const [step,setStep] = useState(0)
  const [caseIndex,setCaseIndex] = useState(0)
  const entry = cases[caseIndex]||null
  return <div className="public-showcase" id="top">
    <a className="showcase-skip" href="#showcase-main">跳到主要内容</a>
    <header className="showcase-header"><a className="showcase-brand" href="#top"><span><Icon name="layers"/></span><span>AIGC Workbench<small>商品视觉创作工作台</small></span></a>
      <nav aria-label="产品展示导航"><a href="#capabilities">产品能力</a><a href="#cases">案例</a><a href="#process">制作流程</a><span>只读展示</span></nav></header>
    <main id="showcase-main" className="showcase-main" tabIndex={-1}>
      <section className="showcase-hero" aria-labelledby="showcase-title"><div><p className="eyebrow">PREMIUM LIGHT STUDIO</p><h1 id="showcase-title">从真实商品出发，<br/>制作下一版营销图。</h1>
        <p className="showcase-lead">把商品素材、同款营销参考与运营要求，汇集到逐图创作的工作台。让生成、版本修改与人工审核衔接起来。</p>
        <div className="showcase-hero-actions"><a className="button-link primary" href="#capabilities">了解 Quick-A<Icon name="arrow"/></a><a className="button-link" href="#cases">查看案例状态</a></div>
        <p className="showcase-hero-note">独立只读产品展示 · 此页面不提供上传或 AI 执行</p>
      </div><div className="showcase-hero-preview"><StudioOverview/><div className="showcase-diagram-caption"><span>工作台布局示意 · 无真实业务数据</span><button type="button" onClick={()=>setLayoutOpen(true)}><Icon name="expand"/>放大布局</button></div></div></section>
      <section id="capabilities" className="showcase-section" aria-labelledby="capabilities-title"><div className="showcase-section-heading"><div><p className="eyebrow">QUICK-A</p><h2 id="capabilities-title">同款参考，新的营销表达。</h2></div><p>面向图片驱动的商品营销图制作，无需先准备完整 Listing。</p></div>
        <div className="showcase-capabilities">{capabilities.map(c=><article key={c.title}><span className="showcase-feature-icon"><Icon name={c.icon}/></span><h3>{c.title}</h3><p>{c.text}</p></article>)}</div>
      </section>
      <section id="cases" className="showcase-section" aria-labelledby="cases-title"><div className="showcase-section-heading"><div><p className="eyebrow">SOURCE TO RESULT</p><h2 id="cases-title">商品、参考与成果，对照查看。</h2></div><p>只展示获准公开的素材。人工 Pass 与对外公开授权分别核查。</p></div>
        {cases.length>1&&<div className="showcase-case-selector" aria-label="选择公开案例">{cases.map((c,index)=><button key={c.slug} aria-pressed={caseIndex===index} onClick={()=>setCaseIndex(index)}>{c.title}</button>)}</div>}
        <CaseGallery key={entry?.slug||'pending'} entry={entry}/>
        {!cases.length&&<p className="showcase-clearance-note"><Icon name="layers"/>案例内容待公开授权，当前不发布商业商品图、参考图或历史生成图片。上方仅保留对照浏览位置。</p>}
      </section>
      <section id="process" className="showcase-section" aria-labelledby="process-title"><div className="showcase-section-heading"><div><p className="eyebrow">CREATION, WITH CONTEXT</p><h2 id="process-title">从素材到交付，每一步有依据。</h2></div><p>点击步骤了解已实现的 Quick-A 操作路径。</p></div>
        <div className="showcase-process"><ol>{workflow.map((s,index)=><li key={s.title}><button type="button" aria-pressed={step===index} aria-controls="showcase-step-detail" onClick={()=>setStep(index)}><span>0{index+1}</span>{s.title}<Icon name="chevron"/></button></li>)}</ol>
          <div id="showcase-step-detail" className="showcase-step-detail" aria-live="polite"><span className="eyebrow">步骤 0{step+1}</span><h3>{workflow[step].title}</h3><p>{workflow[step].text}</p><div className="showcase-step-footnote"><Icon name="check"/>流程说明 · 此处不执行任务</div></div></div>
      </section>
      <section className="showcase-boundary" aria-labelledby="boundary-title"><div><p className="eyebrow">BUILT FOR REVIEW</p><h2 id="boundary-title">创作交给工具，交付由人确认。</h2><p>候选不会自动成为有效交付。修改保留基础版本，新的候选通过人工审核后，才更新当前有效图片。</p></div>
        <div><h3>关于这份展示</h3><p>本页介绍已实现的 Quick-A 工作方式，不提供在线生成、上传、审核或内部记录查询。真实 AI 效果仍需结合具体素材逐图评价。</p><a href="#top">返回顶部<Icon name="arrow"/></a></div></section>
    </main>
    <footer className="showcase-footer"><span>AIGC Workbench</span><p>真实素材驱动创作，人工审核后交付。</p><span>只读产品展示</span></footer>
    {layoutOpen&&<Overlay title="Quick-A 工作台结构示意" onClose={()=>setLayoutOpen(false)}><StudioOverview/><p className="showcase-caption">这是界面布局说明，不是真实任务、商品图片或 AI 生成成果。</p></Overlay>}
  </div>
}
