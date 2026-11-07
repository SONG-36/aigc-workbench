export const imageRoles = ['product', 'reference', 'result'] as const
export type ImageRole = typeof imageRoles[number]
export const imageLabels: Record<ImageRole, string> = {
  product: '商品原图', reference: '营销参考', result: '生成成果',
}
export interface PublicImage { src: string; alt: string; caption: string }
export interface PublicCase {
  slug: string; title: string; description: string
  images: Record<ImageRole, PublicImage>
}
export interface ApprovedCaseSpec {
  slug: string; title: string; description: string
  approvalRecord: string
  clearance: { product: true; reference: true; result: true; confidentiality: true; publication: true }
  resultReview: 'HUMAN_PASSED'
  images: Record<ImageRole, { file: string; alt: string; caption: string }>
}

/** Publication clearance is separate from an internal image Pass. Fail closed. */
export function parseManifest(value: unknown): ApprovedCaseSpec[] {
  if (!value || typeof value !== 'object' || !('cases' in value) || !Array.isArray(value.cases))
    throw new Error('公开案例清单格式无效。')
  const keys = (object: object, allowed: string[]) => Object.keys(object).every(k=>allowed.includes(k))
  if (!keys(value,['cases'])) throw new Error('公开案例清单包含非公开字段。')
  const slugs = new Set<string>()
  for (const item of value.cases) {
    if (!item || typeof item !== 'object') throw new Error('公开案例记录无效。')
    const text = (s: unknown): s is string => typeof s === 'string' && !!s.trim() && s.length <= 500
    if (!keys(item,['slug','title','description','approvalRecord','clearance','resultReview','images'])
      || !text(item.slug) || !/^[a-z][a-z0-9-]{0,70}$/.test(item.slug) || slugs.has(item.slug)
      || !text(item.title) || !text(item.description) || !text(item.approvalRecord)
      || item.resultReview !== 'HUMAN_PASSED'
      || !['product','reference','result','confidentiality','publication'].every(k => item.clearance?.[k] === true))
      throw new Error('公开案例缺少完整素材许可、保密核查、发布授权或人工审核。')
    if (!keys(item.clearance,['product','reference','result','confidentiality','publication']) || !keys(item.images||{},[...imageRoles]))
      throw new Error('公开案例包含非公开字段。')
    for (const role of imageRoles) {
      const image = item.images?.[role]
      if (!image || !keys(image,['file','alt','caption']) || !text(image.file) || !/^[a-z][a-z0-9-]*\.(png|jpe?g|webp)$/.test(image.file)
        || !text(image.alt) || !text(image.caption)) throw new Error('案例图片必须是描述清楚的本地已授权素材。')
    }
    slugs.add(item.slug)
  }
  return value.cases as ApprovedCaseSpec[]
}

export function resolveCases(specs: ApprovedCaseSpec[], assets: Record<string,string>): PublicCase[] {
  return specs.map(({slug,title,description,images}) => ({slug,title,description,
    images: Object.fromEntries(imageRoles.map(role => {
      const image = images[role], src = assets[`./assets/approved/${image.file}`]
      if (!src) throw new Error('已授权案例缺少对应的本地图片文件。')
      return [role,{src,alt:image.alt,caption:image.caption}]
    })) as Record<ImageRole,PublicImage>,
  }))
}
