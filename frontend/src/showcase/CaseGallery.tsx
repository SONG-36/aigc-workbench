import {useId,useState,type KeyboardEvent} from 'react'
import {Icon} from '../components/Icon'
import {Overlay} from '../components/ImageOverlay'
import {imageRoles,imageLabels,type ImageRole,type PublicCase} from './manifest'

export function CaseGallery({entry}:{entry:PublicCase|null}) {
  const [role,setRole] = useState<ImageRole>('product')
  const [open,setOpen] = useState(false)
  const [failed,setFailed] = useState<string[]>([])
  const id = useId(), image = entry?.images[role]
  const broken = !!image && failed.includes(image.src)
  const fail = () => { if(image) setFailed(previous => previous.includes(image.src)?previous:[...previous,image.src]); setOpen(false) }
  function select(next:ImageRole) { setRole(next);setOpen(false) }
  function key(event:KeyboardEvent<HTMLButtonElement>,index:number) {
    const target = event.key==='ArrowRight'?(index+1)%3:event.key==='ArrowLeft'?(index+2)%3:event.key==='Home'?0:event.key==='End'?2:null
    if(target===null)return
    event.preventDefault();select(imageRoles[target]);document.getElementById(`${id}-${imageRoles[target]}`)?.focus()
  }
  return <div className="showcase-gallery">
    <div className="showcase-gallery-header"><div><h3>{entry?.title||'真实 Quick-A 案例'}</h3><p>{entry?.description||'完成素材公开授权后，在这里查看商品、参考与成果。'}</p></div>
      <span className="pill pill--neutral">{entry?'经授权的真实案例':'公开授权待确认'}</span></div>
    <div role="tablist" aria-label="案例图片对照" className="showcase-tabs">{imageRoles.map((r,index)=><button key={r} type="button" role="tab" id={`${id}-${r}`} aria-selected={role===r} aria-controls={`${id}-panel`} tabIndex={role===r?0:-1} onKeyDown={e=>key(e,index)} onClick={()=>select(r)}>{imageLabels[r]}</button>)}</div>
    <div id={`${id}-panel`} role="tabpanel" aria-labelledby={`${id}-${role}`} className="showcase-case-panel">
      {image&&!broken?<><button type="button" className="showcase-case-image" aria-label={`放大${imageLabels[role]}`} onClick={()=>setOpen(true)}><img src={image.src} alt={image.alt} onError={fail}/><span><Icon name="expand"/>放大查看</span></button><p className="showcase-caption">{image.caption}</p></>
        :<div className="showcase-case-empty" role={broken?'alert':undefined}><span className="showcase-empty-icon"><Icon name="image"/></span>
          <h4>{broken?'图片暂时无法显示':`${imageLabels[role]} · 待公开授权`}</h4>
          <p>{broken?'请查看其他已授权图片，或稍后重新打开本页。':'这里保留真实案例的位置，不使用未经许可的商品或参考图片。'}</p>
          {!entry&&<small>尚未发布案例图片 · 非生成结果</small>}</div>}
    </div>
    {open&&image&&!broken&&<Overlay title={`${entry?.title} · ${imageLabels[role]}`} onClose={()=>setOpen(false)}><img className="full-image" src={image.src} alt={image.alt} onError={fail}/><p className="showcase-caption">{image.caption}</p><a className="button-link" href={image.src} target="_blank" rel="noreferrer"><Icon name="expand"/>查看已授权原图</a></Overlay>}
  </div>
}
