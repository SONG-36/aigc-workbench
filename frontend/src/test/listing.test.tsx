import {act,fireEvent,render,screen,waitFor} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {MemoryRouter} from 'react-router-dom'
import {expect,it,vi} from 'vitest'
import {App} from '../App'
import {chooseImages,json,taskHttp} from './newTaskFixture'
import type {TaskDraft} from '../types'

const cache=vi.hoisted(()=>new Map())
vi.mock('../utils/newTaskFiles',()=>({newTaskFiles:async(id:string,files?:unknown)=>{
 if(files===undefined)return cache.get(id)
 if(files===null)cache.delete(id);else cache.set(id,files)
}}))
const base:TaskDraft={id:'d1dc6568-ee0c-43d1-865c-77287e68cbde',title:'Listing test',mode:'listing',status:'draft',
 can_generate:false,materials_ready:false,readiness_reason:'missing_product_assets',created_at:'2026-11-13T00:00:00Z',assets:[],slots:[],
 listing_materials:{id:'materials-v1',task_id:'d1dc6568-ee0c-43d1-865c-77287e68cbde',version:1,listing_text:'ORIGINAL_PRODUCT_FACTS',
  supplemental_text:'ORIGINAL_SUPPLEMENT',saved_at:'2026-11-13T00:00:00Z',scope:'TASK_ALL_SLOTS',source:'TEST_ONLY'}}
const mount=(path='/workspace/new?mode=listing')=>render(<MemoryRouter initialEntries={[path]}><App/></MemoryRouter>)

it.each([false,true])('creates Listing with plain text, optional design reference=%s, and no generation',async hasReference=>{
 const http=taskHttp(base),fetchMock=vi.fn(http.handle);vi.stubGlobal('fetch',fetchMock)
 mount();const user=userEvent.setup()
 expect(screen.getByLabelText('工作模式')).toHaveValue('listing')
 expect(screen.getByLabelText('Listing')).toBeRequired();expect(screen.getByLabelText('Listing')).toHaveAttribute('rows','5')
 await user.type(screen.getByLabelText('任务名称'),base.title)
 const longText='商品标题\n五点卖点和参数\n'+('30 cm; original product information\n'.repeat(160))
 fireEvent.change(screen.getByLabelText('Listing'),{target:{value:longText}})
 await user.type(screen.getByLabelText('补充商品描述与卖点'),'SUPPLEMENT_FROM_FORM')
 await user.click(screen.getByRole('button',{name:'展开 Listing 文本框'}));expect(screen.getByLabelText('Listing')).toHaveAttribute('rows','16')
 expect(screen.getByLabelText('Listing')).toHaveValue(longText)
 if(hasReference)await chooseImages(user)
 else{
  const input=screen.getByLabelText('选择商品图片');await waitFor(()=>expect(input).not.toBeDisabled())
  await user.upload(input,new File(['product'],'product.png',{type:'image/png'}))
 }
 await user.click(screen.getByRole('button',{name:'保存任务和资料，进入工作台'}))
 await screen.findByTestId('task-id')
 const command=fetchMock.mock.calls.find(([p,o])=>p==='/api/tasks'&&o?.method==='POST')!
 expect(JSON.parse(String(command[1]?.body))).toMatchObject({mode:'listing',listing_text:longText,supplemental_text:'SUPPLEMENT_FROM_FORM'})
 expect(http.saved().slots).toHaveLength(1)
 expect(http.saved().slots[0].reference_asset_id===null).toBe(!hasReference)
 expect(http.saved().assets).toHaveLength(hasReference?2:1)
 expect(http.saved().listing_materials?.listing_text).toBe(longText)
 expect(screen.getByText('Listing · 商品资料制图')).toBeInTheDocument()
 expect(fetchMock.mock.calls.some(([p])=>/generations|revisions/.test(p))).toBe(false)
})

it('blocks empty Listing and keeps Quick-A, Quick-B and Listing drafts isolated',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(json([])));mount('/workspace/new');const user=userEvent.setup()
 await user.type(screen.getByLabelText('任务名称'),'A_DRAFT')
 await user.selectOptions(screen.getByLabelText('工作模式'),'listing')
 await waitFor(()=>expect(screen.getByLabelText('任务名称')).toHaveValue(''))
 await user.type(screen.getByLabelText('任务名称'),'LISTING_DRAFT')
 expect(screen.getByLabelText('Listing')).toHaveValue('')
 const input=screen.getByLabelText('选择商品图片');await waitFor(()=>expect(input).not.toBeDisabled())
 await user.upload(input,new File(['p'],'product.png',{type:'image/png'}))
 await user.click(screen.getByRole('button',{name:'保存任务和资料，进入工作台'}))
 expect(vi.mocked(fetch).mock.calls.some(([,o])=>o?.method==='POST')).toBe(false)
 await user.type(screen.getByLabelText('Listing'),'LISTING_PRIVATE_FACTS')
 const listing=JSON.parse(sessionStorage.getItem('aigc.pending-listing-task-creation')!)
 await user.selectOptions(screen.getByLabelText('工作模式'),'quick_b')
 await waitFor(()=>expect(screen.getByLabelText('任务名称')).toHaveValue(''))
 expect(screen.queryByLabelText('Listing')).not.toBeInTheDocument()
 await user.type(screen.getByLabelText('任务名称'),'B_DRAFT')
 const b=JSON.parse(sessionStorage.getItem('aigc.pending-quick-b-task-creation')!)
 expect(b.operationId).not.toBe(listing.operationId)
 await user.selectOptions(screen.getByLabelText('工作模式'),'listing')
 await waitFor(()=>expect(screen.getByLabelText('Listing')).toHaveValue('LISTING_PRIVATE_FACTS'))
 expect(screen.getByLabelText('任务名称')).toHaveValue('LISTING_DRAFT')
 expect(JSON.parse(sessionStorage.getItem('aigc.pending-task-creation')!).title).toBe('A_DRAFT')
})

function readyTask(count=1):TaskDraft{
 const task=structuredClone(base);task.materials_ready=true
 task.assets=[{id:'product',task_id:task.id,role:'product_image',filename:'product.png',sha256:'a'.repeat(64),size_bytes:10,
  width:10,height:10,media_type:'image/png',created_at:task.created_at,url:`/api/tasks/${task.id}/assets/product/content`}]
 task.slots=Array.from({length:count},(_,i)=>({id:`slot-${i}`,task_id:task.id,kind:i===0?'main':'scene',position:i,version:1,
  reference_asset_id:null,output_size:'1024x1024',requirements:`SLOT_${i}`,inputs_ready:true}))
 return task
}
function workspace(task:TaskDraft,id:string,state='NOT_STARTED'){
 return {task_id:task.id,slot_id:id,state,candidates:[],executions:[],readiness:{can_generate:state==='NOT_STARTED',quote_token:'a'.repeat(64),
  reason:'TEST_ONLY ready',model:'test',max_cost_usd:null,estimate_usd:null,decision_snapshot_id:null,product_asset_id:'product',reference_asset_id:null}}
}

it('saves new material version, locks generation while dirty, and restores long text after reload',async()=>{
 const task=readyTask(),http=taskHttp(task)
 const fetchMock=vi.fn(async(path:string,options?:RequestInit)=>path.endsWith('/workspace')?json(workspace(task,'slot-0')):http.handle(path,options))
 vi.stubGlobal('fetch',fetchMock);const page=mount('/workspace/'+task.id);const user=userEvent.setup()
 await screen.findByTestId('task-id');await waitFor(()=>expect(screen.getByRole('button',{name:'生成一张候选'})).not.toBeDisabled())
 const updated='UPDATED_PRODUCT_FACTS\n'+'LONG_PARAMETER_TEXT\n'.repeat(300)
 fireEvent.change(screen.getByLabelText('Listing'),{target:{value:updated}})
 expect(screen.getByRole('button',{name:'生成一张候选'})).toBeDisabled()
 expect(screen.getByRole('button',{name:'生成全部有效图位（1）'})).toBeDisabled()
 await user.click(screen.getByRole('button',{name:'保存商品资料'}))
 await waitFor(()=>expect(http.saved().listing_materials?.version).toBe(2))
 await waitFor(()=>expect(screen.getByRole('button',{name:'生成一张候选'})).not.toBeDisabled())
 const put=fetchMock.mock.calls.find(([p,o])=>p.endsWith('/listing-materials')&&o?.method==='PUT')!
 expect(JSON.parse(String(put[1]?.body))).toMatchObject({listing_text:updated,supplemental_text:'ORIGINAL_SUPPLEMENT',expected_version:1,operation_id:expect.any(String)})
 page.unmount();mount('/workspace/'+task.id);await screen.findByTestId('task-id')
 expect(screen.getByLabelText('Listing')).toHaveValue(updated)
 expect(fetchMock.mock.calls.some(([p])=>p.endsWith('/generations')||p.endsWith('/revisions'))).toBe(false)
})

it('keeps a stale-page draft after 409 and requires an explicit reload before replacing it',async()=>{
 const task=readyTask(),http=taskHttp(task);let current=structuredClone(task)
 const fetchMock=vi.fn(async(path:string,options?:RequestInit)=>{
  if(path.endsWith('/workspace'))return json(workspace(task,'slot-0'))
  if(path.endsWith('/listing-materials')&&options?.method==='PUT')return json({error:{message:'商品资料版本已变化，请重新读取'}},409)
  if(path===`/api/tasks/${task.id}`&&!options?.method)return json(current)
  return http.handle(path,options)
 });vi.stubGlobal('fetch',fetchMock);mount('/workspace/'+task.id);const user=userEvent.setup();await screen.findByTestId('task-id')
 fireEvent.change(screen.getByLabelText('Listing'),{target:{value:'UNSAVED_OLD_PAGE'}})
 current={...current,listing_materials:{...current.listing_materials!,version:2,listing_text:'NEW_SERVER_FACTS'}}
 await user.click(screen.getByRole('button',{name:'保存商品资料'}))
 expect(await screen.findByRole('alert')).toHaveTextContent('商品资料版本已变化')
 expect(screen.getByLabelText('Listing')).toHaveValue('UNSAVED_OLD_PAGE')
 expect(screen.getByRole('button',{name:'生成一张候选'})).toBeDisabled()
 await user.click(screen.getByRole('button',{name:'重新读取商品资料（放弃本页未保存修改）'}))
 await waitFor(()=>expect(screen.getByLabelText('Listing')).toHaveValue('NEW_SERVER_FACTS'))
 expect(fetchMock.mock.calls.filter(([p,o])=>p.endsWith('/listing-materials')&&o?.method==='PUT')).toHaveLength(1)
})

it('replays a lost material-save response with the same Operation and one version increment',async()=>{
 const task=readyTask(),http=taskHttp(task);let lose=true
 const fetchMock=vi.fn(async(path:string,options?:RequestInit)=>{
  if(path.endsWith('/workspace'))return json(workspace(task,'slot-0'))
  const response=await http.handle(path,options)
  if(path.endsWith('/listing-materials')&&options?.method==='PUT'&&lose){lose=false;throw Error('TEST_ONLY lost save response')}
  return response
 });vi.stubGlobal('fetch',fetchMock);mount('/workspace/'+task.id);const user=userEvent.setup();await screen.findByTestId('task-id')
 fireEvent.change(screen.getByLabelText('Listing'),{target:{value:'IDEMPOTENT_MATERIALS'}})
 await user.click(screen.getByRole('button',{name:'保存商品资料'}));await screen.findByRole('alert')
 expect(screen.getByLabelText('Listing')).toBeDisabled()
 await user.click(screen.getByRole('button',{name:'安全重试本次保存'}))
 await waitFor(()=>expect(screen.queryByRole('alert')).not.toBeInTheDocument())
 const puts=fetchMock.mock.calls.filter(([p,o])=>p.endsWith('/listing-materials')&&o?.method==='PUT')
 expect(puts).toHaveLength(2);expect(puts[0][1]?.body).toBe(puts[1][1]?.body)
 expect(http.saved().listing_materials?.version).toBe(2)
})

it.each([1,4,13])('submits dynamic N=%i Listing slots without requiring any design reference',async count=>{
 const task=readyTask(count),posts:{id:string;operation:string}[]=[],states:Record<string,string>={}
 vi.stubGlobal('fetch',vi.fn(async(path:string,options?:RequestInit)=>{
  if(path===`/api/tasks/${task.id}`)return json(task)
  const id=path.split('/')[5]
  if(options?.method==='POST'){
   const body=JSON.parse(String(options.body));posts.push({id,operation:body.operation_id});states[id]='QUEUED'
   return json({operation_id:body.operation_id,state:'QUEUED'},202)
  }
  return json(workspace(task,id,states[id]))
 }))
 mount('/workspace/'+task.id);const all=await screen.findByRole('button',{name:`生成全部有效图位（${count}）`})
 expect(screen.queryByText(/暂不可生成：.*参考/)).not.toBeInTheDocument()
 act(()=>{fireEvent.click(all);fireEvent.click(all)})
 await waitFor(()=>expect(posts).toHaveLength(count))
 expect(new Set(posts.map(p=>p.operation)).size).toBe(count)
 expect(posts.map(p=>p.id).sort()).toEqual(task.slots.map(s=>s.id).sort())
 expect(all).toBeDisabled()
})

it('enables multiple fixed Listing slots without uploading references or entering per-slot requirements',async()=>{
 const http=taskHttp(base);vi.stubGlobal('fetch',vi.fn(http.handle));mount();const user=userEvent.setup()
 await user.type(screen.getByLabelText('任务名称'),base.title);await user.type(screen.getByLabelText('Listing'),'RAW_PRODUCT_FACTS')
 const input=screen.getByLabelText('选择商品图片');await waitFor(()=>expect(input).not.toBeDisabled())
 await user.upload(input,new File(['product'],'product.png',{type:'image/png'}))
 await user.click(screen.getByRole('checkbox',{name:'制作同类商品对比图'}))
 await user.click(screen.getByRole('button',{name:'保存任务和资料，进入工作台'}));await screen.findByTestId('task-id')
 expect(http.saved().slots.map(s=>s.kind)).toEqual(['main','comparison'])
 expect(http.saved().slots.every(s=>s.reference_asset_id===null&&s.requirements==='')).toBe(true)
 expect(http.saved().assets).toHaveLength(1)
})
