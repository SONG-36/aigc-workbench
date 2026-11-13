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
const blank:TaskDraft={id:'6e67c094-2eaf-4890-93cb-e9fe4c34b043',title:'Quick-B test',mode:'quick_b',
 status:'draft',can_generate:false,materials_ready:false,readiness_reason:'missing_product_and_reference_assets',
 assets:[],slots:[],created_at:'2026-11-13T00:00:00Z'}
function mount(path:string){return render(<MemoryRouter initialEntries={[path]}><App/></MemoryRouter>)}

it('offers Quick-B from the task centre and saves its mode, files, references and requirements without generation',async()=>{
 const http=taskHttp(blank),fetchMock=vi.fn(async(path:string,options?:RequestInit)=>{
  if(path==='/api/tasks'&&!options?.method)return json([])
  return http.handle(path,options)
 });vi.stubGlobal('fetch',fetchMock)
 mount('/');const user=userEvent.setup()
 const entry=screen.getByRole('link',{name:/新建 Quick-B/});expect(entry).toHaveAttribute('href','/workspace/new?mode=quick_b')
 await user.click(entry);expect(screen.getByLabelText('工作模式')).toHaveValue('quick_b')
 await user.type(screen.getByLabelText('任务名称'),blank.title);await chooseImages(user)
 await user.type(screen.getByLabelText('主图新建图位要求'),'B_SLOT_REQ')
 await user.type(screen.getByLabelText('整体运营要求'),'B_GLOBAL_REQ')
 await user.click(screen.getByRole('button',{name:'保存任务和资料，进入工作台'}))
 await screen.findByRole('heading',{name:blank.title})
 await waitFor(()=>expect(screen.queryByText('新建 Quick-B')).not.toBeInTheDocument())
 expect(screen.getByText('Quick-B · 换主体快速改版')).toBeInTheDocument()
 const posts=fetchMock.mock.calls.filter(([,o])=>o?.method==='POST')
 const create=posts.find(([p])=>p==='/api/tasks')!
 expect(JSON.parse(String(create[1]?.body))).toEqual({title:blank.title,operation_id:expect.any(String),mode:'quick_b',overall_requirements:'B_GLOBAL_REQ'})
 expect(http.saved().slots[0].requirements).toBe('B_SLOT_REQ')
 expect(http.saved().assets).toHaveLength(2)
 expect(fetchMock.mock.calls.filter(([p])=>p.includes('/generations')||p.includes('/revisions'))).toHaveLength(0)
})

it('keeps Quick-A and Quick-B draft requirements, identities and pending files separate when switching mode',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(json([])))
 mount('/workspace/new');const user=userEvent.setup()
 await user.type(screen.getByLabelText('任务名称'),'A_ONLY_DRAFT');await chooseImages(user)
 const a=JSON.parse(sessionStorage.getItem('aigc.pending-task-creation')!)
 await user.selectOptions(screen.getByLabelText('工作模式'),'quick_b')
 await waitFor(()=>expect(screen.getByLabelText('任务名称')).toHaveValue(''))
 expect(screen.queryByText(/product\.png.*待保存/)).not.toBeInTheDocument()
 await user.type(screen.getByLabelText('任务名称'),'B_ONLY_DRAFT')
 const b=JSON.parse(sessionStorage.getItem('aigc.pending-quick-b-task-creation')!)
 expect(b.mode).toBe('quick_b');expect(a.operationId).not.toBe(b.operationId)
 expect(b.productInfo).toBeNull()
 await user.selectOptions(screen.getByLabelText('工作模式'),'quick_a')
 await waitFor(()=>expect(screen.getByLabelText('任务名称')).toHaveValue('A_ONLY_DRAFT'))
 expect(await screen.findByText(/product\.png.*待保存/)).toBeInTheDocument()
 expect(JSON.parse(sessionStorage.getItem('aigc.pending-task-creation')!).operationId).toBe(a.operationId)
 expect(JSON.parse(sessionStorage.getItem('aigc.pending-quick-b-task-creation')!).title).toBe('B_ONLY_DRAFT')
})

it('resumes a lost Quick-B creation response with the same mode and Operation',async()=>{
 const http=taskHttp(blank);let lose=true
 const fetchMock=vi.fn(async(path:string,options?:RequestInit)=>{
  const response=await http.handle(path,options)
  if(path==='/api/tasks'&&options?.method==='POST'&&lose){lose=false;throw Error('TEST_ONLY lost create response')}
  return response
 });vi.stubGlobal('fetch',fetchMock)
 mount('/workspace/new?mode=quick_b');const user=userEvent.setup()
 await user.type(screen.getByLabelText('任务名称'),blank.title);await chooseImages(user)
 await user.click(screen.getByRole('button',{name:'保存任务和资料，进入工作台'}))
 expect(await screen.findByRole('alert')).toHaveTextContent('结果尚未确认')
 await user.click(screen.getByRole('button',{name:'继续保存任务和资料'}))
 await screen.findByRole('heading',{name:blank.title})
 await waitFor(()=>expect(screen.queryByText('新建 Quick-B')).not.toBeInTheDocument())
 const creates=fetchMock.mock.calls.filter(([p,o])=>p==='/api/tasks'&&o?.method==='POST')
 expect(creates).toHaveLength(2);expect(creates[0][1]?.body).toBe(creates[1][1]?.body)
 expect(JSON.parse(String(creates[0][1]?.body)).mode).toBe('quick_b')
 expect(http.saved().assets).toHaveLength(2)
})

it.each([1,4,13])('submits every valid Quick-B slot dynamically for N=%i, with independent Operations',async count=>{
 const task=structuredClone(blank);task.materials_ready=true
 task.assets=['product_image',...Array(count).fill('marketing_reference')].map((role,i)=>({id:`asset-${i}`,task_id:task.id,
  role,filename:`test-${i}.png`,sha256:'a'.repeat(64),size_bytes:10,width:10,height:10,media_type:'image/png',created_at:task.created_at,
  url:`/api/tasks/${task.id}/assets/asset-${i}/content`}))
 task.slots=Array.from({length:count},(_,i)=>({id:`slot-${i}`,task_id:task.id,kind:i===0?'dimensions':'scene',position:i,version:1,
  reference_asset_id:`asset-${i+1}`,output_size:'1024x1024',requirements:`B_SLOT_${i}`,inputs_ready:true}))
 const posts:{id:string;operation:string}[]=[],states:Record<string,string>={}
 vi.stubGlobal('fetch',vi.fn(async(path:string,options?:RequestInit)=>{
  if(path===`/api/tasks/${task.id}`)return json(task)
  const id=path.split('/')[5]
  if(options?.method==='POST'){
   const body=JSON.parse(String(options.body));posts.push({id,operation:body.operation_id});states[id]='QUEUED'
   return json({operation_id:body.operation_id,state:'QUEUED'},202)
  }
  return json({task_id:task.id,slot_id:id,state:states[id]||'NOT_STARTED',candidates:[],executions:[],readiness:{
   can_generate:!states[id],reason:'TEST_ONLY ready',quote_token:'a'.repeat(64),model:'test',max_cost_usd:null,
   estimate_usd:null,decision_snapshot_id:null,product_asset_id:'asset-0',reference_asset_id:`asset-${Number(id?.split('-')[1])+1}`}})
 }))
 mount('/workspace/'+task.id)
 const all=await screen.findByRole('button',{name:`生成全部有效图位（${count}）`})
 expect(screen.getByText('Quick-B · 换主体快速改版')).toBeInTheDocument()
 expect(screen.getByRole('checkbox',{name:'选择尺寸图'})).not.toBeDisabled()
 act(()=>{fireEvent.click(all);fireEvent.click(all)})
 await waitFor(()=>expect(posts).toHaveLength(count))
 expect(new Set(posts.map(p=>p.operation)).size).toBe(count)
 expect(posts.map(p=>p.id).sort()).toEqual(task.slots.map(s=>s.id).sort())
 expect(all).toBeDisabled()
})
