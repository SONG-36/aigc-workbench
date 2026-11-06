import type {SlotKind} from '../types'
export const SLOT_KINDS: [SlotKind,string][]=[['main','主图'],['dimensions','尺寸图'],['comparison','同类商品对比图'],['selling_point','卖点图'],['scene','使用场景图']]
export const fixedSlot=(kind:SlotKind)=>['main','dimensions','comparison'].includes(kind)
export const slotTitle=(kind:SlotKind,ordinal=1)=>`${SLOT_KINDS.find(([k])=>k===kind)![1]}${fixedSlot(kind)?'':` ${String(ordinal).padStart(2,'0')}`}`
