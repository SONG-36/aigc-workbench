import {fireEvent,render,screen,within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {expect,it,vi} from 'vitest'
import {Showcase} from '../showcase/Showcase'
import {CaseGallery} from '../showcase/CaseGallery'
import {publicCases} from '../showcase/content'
import {parseManifest,resolveCases,type PublicCase} from '../showcase/manifest'

const fixture:PublicCase={slug:'test-only',title:'TEST_ONLY · gallery fixture',productCategory:'TEST_ONLY 图形',displayOrder:1,versionLabel:'TEST_ONLY V2',humanReviewLabel:'TEST_ONLY 审核标签',description:'组件验收图形，不是真实商品案例。',
  images:{product:{src:'./test-product.png',alt:'TEST_ONLY 商品图形',caption:'TEST_ONLY product'},
    reference:{src:'./test-reference.png',alt:'TEST_ONLY 参考图形',caption:'TEST_ONLY reference'},
    result:{src:'./test-result.png',alt:'TEST_ONLY 成果图形',caption:'TEST_ONLY result'}}}
const spec=()=>({slug:'test-only',title:'TEST_ONLY',productCategory:'TEST_ONLY',displayOrder:1,description:'TEST_ONLY',approvalRecord:'TEST_ONLY_UNIT_FIXTURE',
  clearance:{confidentiality:true,publication:true},resultReview:'HUMAN_PASSED',
  images:Object.fromEntries(['product','reference','result'].map(role=>[role,{file:`test-${role}.png`,sha256:'a'.repeat(64),authorizationStatus:'APPROVED_FOR_PUBLIC_SHOWCASE',approvalRecord:'TEST_ONLY_UNIT_FIXTURE',alt:`TEST_ONLY ${role}`,caption:'TEST_ONLY'}]))})

it('runs the public page without fetch, uploads, business commands or internal links; shows honest pending cases',async()=>{
  const fetch=vi.fn(()=>{throw Error('Public page must not call an API')});vi.stubGlobal('fetch',fetch)
  expect(publicCases).toEqual([])
  render(<Showcase cases={publicCases}/>);const user=userEvent.setup()
  expect(screen.getByRole('heading',{level:1})).toHaveTextContent('从真实商品出发')
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  expect(document.querySelector('input[type=file],form')).toBeNull()
  for(const link of screen.getAllByRole('link'))expect(link.getAttribute('href')).toMatch(/^#/)
  await user.click(screen.getByRole('tab',{name:'营销参考'}))
  expect(screen.getByRole('tabpanel')).toHaveTextContent('营销参考 · 待公开授权')
  await user.keyboard('{ArrowRight}')
  expect(screen.getByRole('tab',{name:'生成成果'})).toHaveFocus()
  expect(screen.getByRole('tabpanel')).toHaveTextContent('生成成果 · 待公开授权')
  await user.click(screen.getByRole('button',{name:/Pass 后交付/}))
  expect(document.getElementById('showcase-step-detail')).toHaveTextContent('当前有效交付指向已通过版本')
  const enlarge=screen.getByRole('button',{name:'放大布局'});await user.click(enlarge)
  expect(screen.getByRole('dialog')).toHaveTextContent('不是真实任务、商品图片或 AI 生成成果')
  await user.keyboard('{Escape}');expect(enlarge).toHaveFocus();expect(fetch).not.toHaveBeenCalled()
})

it('switches all local cleared images, supports keyboard selection, zoom and focus return without network commands',async()=>{
  const fetch=vi.fn();vi.stubGlobal('fetch',fetch);render(<CaseGallery entry={fixture}/>);const user=userEvent.setup()
  expect(screen.getByAltText('TEST_ONLY 商品图形')).toHaveAttribute('src','./test-product.png')
  await user.click(screen.getByRole('tab',{name:'营销参考'}));await user.keyboard('{End}')
  expect(screen.getByAltText('TEST_ONLY 成果图形')).toHaveAttribute('src','./test-result.png')
  const enlarge=screen.getByRole('button',{name:'放大生成成果'});await user.click(enlarge)
  const dialog=screen.getByRole('dialog');expect(within(dialog).getByRole('link',{name:'查看已授权原图'})).toHaveAttribute('href','./test-result.png')
  await user.click(within(dialog).getByRole('img'));await user.keyboard('{Escape}')
  expect(enlarge).toHaveFocus();expect(fetch).not.toHaveBeenCalled()
})

it('shows an honest image error without retrying or substituting a different result',async()=>{
  render(<CaseGallery entry={fixture}/>);const user=userEvent.setup()
  fireEvent.error(screen.getByAltText('TEST_ONLY 商品图形'))
  expect(screen.getByRole('alert')).toHaveTextContent('图片暂时无法显示')
  expect(screen.queryByRole('button',{name:'放大商品原图'})).not.toBeInTheDocument()
  await user.click(screen.getByRole('tab',{name:'营销参考'}));expect(screen.getByAltText('TEST_ONLY 参考图形')).toBeInTheDocument()
  await user.click(screen.getByRole('tab',{name:'商品原图'}));expect(screen.getByRole('alert')).toBeInTheDocument()
})

it('supports two images without exposing a reference tab and wraps keyboard focus over the actual roles',async()=>{
  const {reference:_,...images}=fixture.images
  render(<CaseGallery entry={{...fixture,images}}/>);const user=userEvent.setup()
  expect(screen.getAllByRole('tab')).toHaveLength(2)
  expect(screen.queryByRole('tab',{name:'营销参考'})).not.toBeInTheDocument()
  expect(screen.getByText('本案例未公开营销参考')).toBeInTheDocument()
  expect(screen.getByText(/TEST_ONLY V2/)).toHaveTextContent('TEST_ONLY 审核标签')
  await user.click(screen.getByRole('tab',{name:'商品原图'}));await user.keyboard('{ArrowLeft}')
  expect(screen.getByRole('tab',{name:'生成成果'})).toHaveFocus()
  await user.keyboard('{ArrowRight}');expect(screen.getByRole('tab',{name:'商品原图'})).toHaveFocus()
  await user.keyboard('{End}');await user.click(screen.getByRole('button',{name:'放大生成成果'}))
  expect(within(screen.getByRole('dialog')).getByRole('img')).toHaveAttribute('src','./test-result.png')
  await user.keyboard('{Escape}');expect(screen.getByRole('button',{name:'放大生成成果'})).toHaveFocus()
})

it('falls back to product when switching from a reference selection to a case without one',async()=>{
  const {rerender}=render(<CaseGallery entry={fixture}/>);const user=userEvent.setup()
  await user.click(screen.getByRole('tab',{name:'营销参考'}))
  const {reference:_,...images}=fixture.images
  rerender(<CaseGallery entry={{...fixture,slug:'two-images',images}}/>)
  expect(screen.getByRole('tab',{name:'商品原图'})).toHaveAttribute('aria-selected','true')
  expect(screen.getByAltText('TEST_ONLY 商品图形')).toBeInTheDocument()
})

it.each(['confidentiality','publication'])('rejects publication when %s clearance is missing',key=>{
  const item=spec();Object.assign(item.clearance,{[key]:false});expect(()=>parseManifest({cases:[item]})).toThrow()
})

it.each(['product','reference','result'])('rejects pending or prohibited %s even if the result has Pass',role=>{
  for(const authorizationStatus of ['PENDING_OPERATOR_APPROVAL','NOT_ALLOWED',undefined]) {
    const item=spec();Object.assign(item.images[role],{authorizationStatus})
    expect(()=>parseManifest({cases:[item]})).toThrow('每张图片必须具有明确公开授权')
  }
  for(const change of [{approvalRecord:''},{sha256:'invalid'},{task_id:'private'}]) {
    const item=spec();Object.assign(item.images[role],change);expect(()=>parseManifest({cases:[item]})).toThrow()
  }
})

it('allows an omitted reference only; resolves exact local assets in display order without publishing approval metadata',()=>{
  const two=spec();delete two.images.reference
  const three={...spec(),slug:'three-images',displayOrder:0}
  const assets=Object.fromEntries(['product','reference','result'].map(role=>[`./assets/approved/test-${role}.png`,`./${role}.png`]))
  const cases=resolveCases(parseManifest({cases:[two,three]}),assets)
  expect(cases.map(c=>c.slug)).toEqual(['three-images','test-only'])
  expect(cases[1].images.reference).toBeUndefined();expect(cases[1].images.result.src).toBe('./result.png')
  expect(cases[1]).not.toHaveProperty('approvalRecord')
  for(const role of ['product','result']) {
    const item=spec();delete item.images[role];expect(()=>parseManifest({cases:[item]})).toThrow()
  }
  const item=spec();Object.assign(item.images,{reference:null});expect(()=>parseManifest({cases:[item]})).toThrow()
})

it('rejects inconsistent filename hashes and duplicate cases or invalid public metadata',()=>{
  const first=spec(),second={...spec(),slug:'second'};second.images.product.sha256='b'.repeat(64)
  expect(()=>parseManifest({cases:[first,second]})).toThrow('同名公开素材的哈希不一致')
  expect(()=>parseManifest({cases:[first,first]})).toThrow()
  for(const change of [{displayOrder:-1},{displayOrder:1.2},{productCategory:''},{versionLabel:''}])
    expect(()=>parseManifest({cases:[{...spec(),...change}]})).toThrow()
})
it('does not equate internal Pass with public permission or accept private fields and remote paths',()=>{
  const item=spec();expect(parseManifest({cases:[]})).toEqual([])
  expect(()=>parseManifest({cases:[{...item,resultReview:'PENDING'}]})).toThrow()
  expect(()=>parseManifest({cases:[{...item,approvalRecord:''}]})).toThrow()
  expect(()=>parseManifest({cases:[{...item,task_id:'internal-id'}]})).toThrow()
  for(const file of ['https://internal/image.png','../../private.png','private.svg']) {
    const changed=structuredClone(item);changed.images.product.file=file
    expect(()=>parseManifest({cases:[changed]})).toThrow()
  }
  expect(()=>resolveCases(parseManifest({cases:[item]}),{})).toThrow('缺少对应的本地图片')
})
