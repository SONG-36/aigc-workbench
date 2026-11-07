import {useState} from 'react'
import {Overlay} from './ImageOverlay'
export function ReferenceImage({url,label,description=`${label}参考图`}: {url:string;label:string;description?:string}) {
 const [open,setOpen]=useState(false)
 return <><button type="button" className="reference-button" aria-label={`放大${description}`} onClick={()=>setOpen(true)}>
   <img className="reference-preview" src={url} alt={description} /></button>
  {open&&<Overlay title={`${description} · 原图`} onClose={()=>setOpen(false)}><img className="full-image" src={url} alt={`${description} · 原图`} /></Overlay>}</>
}
