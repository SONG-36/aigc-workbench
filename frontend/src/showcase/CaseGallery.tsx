import {useId,useState,type KeyboardEvent} from 'react'
import {Icon} from '../components/Icon'
import {Overlay} from '../components/ImageOverlay'
import {availableImageRoles,imageRoles,imageLabels,type ImageRole,type PublicCase} from './manifest'

export function CaseGallery({entry}:{entry:PublicCase|null}) {
  const [role,setRole] = useState<ImageRole>(entry?'result':'product')
  const [open,setOpen] = useState(false)
  const [failed,setFailed] = useState<string[]>([])
  const roles = entry ? availableImageRoles(entry.images) : [...imageRoles]
  const selectedRole = roles.includes(role) ? role : 'product'
  const id = useId(), image = entry?.images[selectedRole]
  const broken = !!image && failed.includes(image.src)
  const fail = (src:string) => { setFailed(previous => previous.includes(src)?previous:[...previous,src]); setOpen(false) }
  function select(next:ImageRole) { setRole(next);setOpen(false) }
  function key(event:KeyboardEvent<HTMLButtonElement>,index:number) {
    const target = event.key==='ArrowRight'?(index+1)%roles.length:event.key==='ArrowLeft'?(index+roles.length-1)%roles.length:event.key==='Home'?0:event.key==='End'?roles.length-1:null
    if(target===null)return
    event.preventDefault();select(roles[target]);document.getElementById(`${id}-${roles[target]}`)?.focus()
  }
  return <div className="showcase-gallery">
    <div className="showcase-gallery-header"><div><h3>{entry?.title||'真实 Quick-A 案例'}</h3><p>{entry?.description||'完成素材公开授权后，在这里查看商品、参考与成果。'}</p></div>
      <span className="pill pill--neutral">{entry?'运营批准展示':'公开授权待确认'}</span></div>
    {entry&&<p className="showcase-case-meta">{[entry.productCategory,entry.versionLabel,entry.humanReviewLabel].filter(Boolean).join(' · ')}{!entry.images.reference&&<span>本案例未公开营销参考</span>}</p>}
    {entry&&<>
      <p className="showcase-rights-note">运营批准日期：{entry.approvalDate} · 权利待复核<br/>{entry.rightsNotice}</p>
      <div className="showcase-comparison" aria-label={entry.images.reference?'真实案例三图对照':'真实案例两图对照'}>{roles.map((r,index)=>{
        const source=entry.images[r]!,error=failed.includes(source.src)
        return <figure key={r} className={r==='result'?'showcase-comparison-result':undefined}>
          <figcaption><span>0{index+1} · {r==='result'?'人工审核后的候选':'制作输入'}</span><strong>{imageLabels[r]}</strong></figcaption>
          {error?<p role="alert">图片暂时无法显示</p>:<button type="button" aria-label={`放大对照中的${imageLabels[r]}`} onClick={()=>{setRole(r);setOpen(true)}}><img src={source.src} alt={`${imageLabels[r]}缩略预览：${source.alt}`} onError={()=>fail(source.src)}/><span><Icon name="expand"/>查看原图</span></button>}
          <p>{source.caption}</p>
        </figure>
      })}</div>
    </>}
    <div role="tablist" aria-label="案例图片对照" className="showcase-tabs">{roles.map((r,index)=><button key={r} type="button" role="tab" id={`${id}-${r}`} aria-selected={selectedRole===r} aria-controls={`${id}-panel`} tabIndex={selectedRole===r?0:-1} onKeyDown={e=>key(e,index)} onClick={()=>select(r)}>{imageLabels[r]}</button>)}</div>
    <div id={`${id}-panel`} role="tabpanel" aria-labelledby={`${id}-${selectedRole}`} className="showcase-case-panel">
      {image&&!broken?<><button type="button" className="showcase-case-image" aria-label={`放大${imageLabels[selectedRole]}`} onClick={()=>setOpen(true)}><img src={image.src} alt={image.alt} onError={()=>fail(image.src)}/><span><Icon name="expand"/>放大查看</span></button><p className="showcase-caption">{image.caption}</p></>
        :<div className="showcase-case-empty" role={broken?'alert':undefined}><span className="showcase-empty-icon"><Icon name="image"/></span>
          <h4>{broken?'图片暂时无法显示':`${imageLabels[selectedRole]} · 待公开授权`}</h4>
          <p>{broken?'请查看其他已授权图片，或稍后重新打开本页。':'这里保留真实案例的位置，不使用未经许可的商品或参考图片。'}</p>
          {!entry&&<small>尚未发布案例图片 · 非生成结果</small>}</div>}
    </div>
    {open&&image&&!broken&&<Overlay title={`${entry?.title} · ${imageLabels[selectedRole]}`} onClose={()=>setOpen(false)}><img className="full-image" src={image.src} alt={image.alt} onError={()=>fail(image.src)}/><p className="showcase-caption">{image.caption}</p><a className="button-link" href={image.src} target="_blank" rel="noreferrer"><Icon name="expand"/>查看已授权原图</a></Overlay>}
  </div>
}
