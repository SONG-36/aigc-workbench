import {useState} from 'react'
import {Overlay} from './ImageOverlay'
export function ReferenceImage({url,label}: {url:string;label:string}) {
 const [open,setOpen]=useState(false)
 return <><button type="button" className="reference-button" aria-label={`放大${label}参考图`} onClick={()=>setOpen(true)}>
   <img className="reference-preview" src={url} alt={`${label}参考图`} /></button>
  {open&&<Overlay title={`${label}参考原图`} onClose={()=>setOpen(false)}><img className="full-image" src={url} alt={`${label}参考原图`} /></Overlay>}</>
}
