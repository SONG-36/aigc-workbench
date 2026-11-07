import manifest from '../../showcase/cases.json'
import {parseManifest,resolveCases} from './manifest'
import assets from 'virtual:showcase-assets'

// The build injects only exact manifest entries from the ignored publishing directory.
export const publicCases = resolveCases(parseManifest(manifest),assets)

export const workflow = [
  {title:'准备真实素材',text:'提供实售商品图片，在每个图位直接上传同款营销参考，设置输出尺寸与整体、逐图要求。'},
  {title:'理解与制作决定',text:'分别理解商品与参考，结合两张原图和有效要求，形成单图营销内容与视觉制作决定。'},
  {title:'生成独立候选',text:'按图位生成候选图片，保留来源与版本。生成候选不等于已通过人工审核。'},
  {title:'按需修改与审核',text:'明确选择基础版本，再提出本轮修改意见；新版本独立保存，历史版本继续保留。'},
  {title:'Pass 后交付',text:'人工对具体版本执行 Pass 或 Reject；当前有效交付指向已通过版本，再下载对应 PNG。'},
]
