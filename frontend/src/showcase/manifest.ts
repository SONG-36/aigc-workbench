export const imageRoles = ['product', 'reference', 'result'] as const
export type ImageRole = typeof imageRoles[number]
export const imageLabels: Record<ImageRole, string> = {
  product: '商品原图', reference: '营销参考', result: '生成成果',
}
export interface PublicImage { src: string; alt: string; caption: string }
export type CaseImages<T> = { product: T; reference?: T; result: T }
interface CaseDetails {
  slug: string; title: string; productCategory: string; description: string
  versionLabel?: string; humanReviewLabel?: string; displayOrder: number
  approvalDate: string; rightsReview: 'NEEDS_RIGHTS_REVIEW'; rightsNotice: string
}
export interface PublicCase extends CaseDetails {
  images: CaseImages<PublicImage>
}
export interface ApprovedImageSpec {
  file: string; sha256: string; alt: string; caption: string
  authorizationStatus: 'OPERATOR_APPROVED_FOR_PUBLIC_SHOWCASE'; approvalRecord: string
}
export interface ApprovedCaseSpec extends CaseDetails {
  approvalRecord: string
  clearance: { confidentiality: true; publication: true }
  resultReview: 'HUMAN_PASSED'
  images: CaseImages<ApprovedImageSpec>
}

export function availableImageRoles(images: CaseImages<unknown>): ImageRole[] {
  return imageRoles.filter(role => images[role] !== undefined)
}

/** Publication clearance is separate from an internal image Pass. Fail closed. */
export function parseManifest(value: unknown): ApprovedCaseSpec[] {
  const record = (object: unknown): object is Record<string, unknown> => !!object && typeof object === 'object' && !Array.isArray(object)
  if (!record(value) || !Array.isArray(value.cases))
    throw new Error('公开案例清单格式无效。')
  const keys = (object: object, allowed: string[]) => Object.keys(object).every(k=>allowed.includes(k))
  if (!keys(value,['cases'])) throw new Error('公开案例清单包含非公开字段。')
  const slugs = new Set<string>()
  const files = new Map<string, string>()
  for (const item of value.cases) {
    if (!record(item)) throw new Error('公开案例记录无效。')
    const text = (s: unknown): s is string => typeof s === 'string' && !!s.trim() && s.length <= 500
    if (!keys(item,['slug','title','productCategory','description','versionLabel','humanReviewLabel','displayOrder','approvalDate','rightsReview','rightsNotice','approvalRecord','clearance','resultReview','images'])
      || !text(item.slug) || !/^[a-z][a-z0-9-]{0,70}$/.test(item.slug) || slugs.has(item.slug)
      || !text(item.title) || !text(item.productCategory) || !text(item.description) || !text(item.approvalRecord)
      || !Number.isSafeInteger(item.displayOrder) || (item.displayOrder as number) < 0
      || ['versionLabel','humanReviewLabel'].some(k => k in item && !text(item[k]))
      || typeof item.approvalDate !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(item.approvalDate)
      || item.rightsReview !== 'NEEDS_RIGHTS_REVIEW' || !text(item.rightsNotice)
      || item.resultReview !== 'HUMAN_PASSED'
      || !record(item.clearance) || item.clearance.confidentiality !== true || item.clearance.publication !== true)
      throw new Error('公开案例缺少完整素材许可、保密核查、发布授权或人工审核。')
    if (!keys(item.clearance,['confidentiality','publication']) || !record(item.images) || !keys(item.images,[...imageRoles]))
      throw new Error('公开案例包含非公开字段。')
    if (!item.images.product || !item.images.result) throw new Error('公开案例必须包含商品与成果。')
    for (const role of imageRoles) {
      if (role === 'reference' && !('reference' in item.images)) continue
      const image = item.images[role]
      if (!record(image) || !keys(image,['file','sha256','alt','caption','authorizationStatus','approvalRecord'])
        || !text(image.file) || !/^[a-z][a-z0-9-]*\.(png|jpe?g|webp)$/.test(image.file)
        || typeof image.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(image.sha256)
        || image.authorizationStatus !== 'OPERATOR_APPROVED_FOR_PUBLIC_SHOWCASE' || !text(image.approvalRecord)
        || !text(image.alt) || !text(image.caption)) throw new Error('案例每张图片必须具有明确公开授权、文件哈希和图片说明。')
      if (files.has(image.file) && files.get(image.file) !== image.sha256) throw new Error('同名公开素材的哈希不一致。')
      files.set(image.file,image.sha256)
    }
    slugs.add(item.slug)
  }
  return value.cases as ApprovedCaseSpec[]
}

export function resolveCases(specs: ApprovedCaseSpec[], assets: Record<string,string>): PublicCase[] {
  return [...parseManifest({cases:specs})].sort((a,b)=>a.displayOrder-b.displayOrder).map(({slug,title,productCategory,description,versionLabel,humanReviewLabel,displayOrder,approvalDate,rightsReview,rightsNotice,images}) => ({slug,title,productCategory,description,versionLabel,humanReviewLabel,displayOrder,approvalDate,rightsReview,rightsNotice,
    images: Object.fromEntries(availableImageRoles(images).map(role => {
      const image = images[role]!, src = assets[image.file]
      if (!src) throw new Error('已授权案例缺少对应的本地图片文件。')
      return [role,{src,alt:image.alt,caption:image.caption}]
    })) as CaseImages<PublicImage>,
  }))
}
