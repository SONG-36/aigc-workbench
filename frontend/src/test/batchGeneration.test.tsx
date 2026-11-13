import {act,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import {it,expect,vi} from 'vitest'
import {BatchGeneration} from '../components/BatchGeneration'
import {App} from '../App'
import type {TaskDraft} from '../types'
import type {ExecutionState,SlotWorkspace} from '../api/generation'

const makeTask=(count:number):TaskDraft=>({id:'batch-task',title:'TEST_ONLY batch',mode:'quick_a',status:'draft',can_generate:false,
 materials_ready:true,readiness_reason:'ready',created_at:'2026-11-13T00:00:00Z',
 assets:['product_image',...Array(count).fill('marketing_reference')].map((role,i)=>({id:`asset-${i}`,task_id:'batch-task',
  role,filename:`test-${i}.png`,sha256:'a'.repeat(64),size_bytes:10,width:10,height:10,media_type:'image/png',
  created_at:'2026-11-13T00:00:00Z',url:`/api/tasks/batch-task/assets/asset-${i}/content`})),
 slots:Array.from({length:count},(_,i)=>({id:`slot-${i}`,task_id:'batch-task',kind:'scene',position:i,version:3,
  reference_asset_id:`asset-${i+1}`,output_size:'1024x1024',requirements:`SLOT_${i}`,inputs_ready:true}))})
const task=makeTask(10)
const json=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}})
function workspace(slot:string,state:ExecutionState='NOT_STARTED',operation?:string):SlotWorkspace{
 return {task_id:task.id,slot_id:slot,state,candidates:[],executions:operation?[{operation_id:operation,
  run_id:null,attempt_id:null,state,created_at:task.created_at,finished_at:null,model:'test',execution_mode:'TEST_SUBSTITUTE',
  candidate_ids:[],http_status:null,duration_ms:null,error:state==='FAILED'?{code:'TEST_FAILURE',message:'TEST_ONLY model error'}:null}]:[],
  readiness:{can_generate:['NOT_STARTED','SUCCEEDED','FAILED'].includes(state),reason:'TEST_ONLY ready',quote_token:'a'.repeat(64),model:'test',
   max_cost_usd:null,estimate_usd:null,decision_snapshot_id:null,product_asset_id:'asset-0',reference_asset_id:'asset-1'}}
}

it.each([0,1,4,13,17])('submits all current valid slots dynamically for N=%i',async(count)=>{
 const dynamic=makeTask(count),posts:{slot:string;operation:string}[]=[]
 const states:Record<string,SlotWorkspace>={}
 vi.stubGlobal('fetch',vi.fn(async(path:string,options?:RequestInit)=>{
  const slot=path.split('/')[5]
  if(options?.method==='POST'){
   const body=JSON.parse(String(options.body));posts.push({slot,operation:body.operation_id})
   states[slot]=workspace(slot,'QUEUED',body.operation_id)
   return json({operation_id:body.operation_id,state:'QUEUED'},202)
  }
  return json(states[slot]||workspace(slot))
 }))
 render(<BatchGeneration task={dynamic} locked={false} onSelect={vi.fn()} onBusy={vi.fn()} onSubmitted={vi.fn()}/>)
 const all=screen.getByRole('button',{name:`生成全部有效图位（${count}）`})
 if(!count){expect(all).toBeDisabled();expect(screen.queryAllByRole('checkbox')).toHaveLength(0);return}
 act(()=>{fireEvent.click(all);fireEvent.click(all)})
 await waitFor(()=>expect(posts).toHaveLength(count))
 expect(new Set(posts.map(p=>p.operation)).size).toBe(count)
 expect(posts.map(p=>p.slot).sort()).toEqual(dynamic.slots.map(s=>s.id).sort())
 expect(all).toBeDisabled()
})

it('submits only the selected valid slots and updates counts when Task slots change',async()=>{
 const dynamic=makeTask(13),states:Record<string,SlotWorkspace>={}
 const posts:{slot:string;operation:string}[]=[]
 states['slot-2']=workspace('slot-2','SUCCEEDED','previous-success')
 // 已移除、缺少输入不计入数量；素材齐备的尺寸图与其他图位相同。
 dynamic.slots.push({...dynamic.slots[0],id:'archived',archived:true},
  {...dynamic.slots[0],id:'unready',reference_asset_id:null,inputs_ready:false},
  {...dynamic.slots[0],id:'dimensions',kind:'dimensions'})
 vi.stubGlobal('fetch',vi.fn(async(path:string,options?:RequestInit)=>{
  const slot=path.split('/')[5]
  if(options?.method==='POST'){
   const body=JSON.parse(String(options.body));posts.push({slot,operation:body.operation_id})
   states[slot]=workspace(slot,'QUEUED',body.operation_id)
   return json({operation_id:body.operation_id,state:'QUEUED'},202)
  }
  return json(states[slot]||workspace(slot))
 }))
 const props={task:dynamic,locked:false,onSelect:vi.fn(),onBusy:vi.fn(),onSubmitted:vi.fn()}
 const view=render(<BatchGeneration {...props}/>)
 expect(screen.getByRole('button',{name:'生成全部有效图位（14）'})).toBeEnabled()
 expect(screen.getAllByRole('checkbox')).toHaveLength(15)
 expect(screen.getByRole('checkbox',{name:'选择尺寸图'})).toBeEnabled()
 expect(screen.getByRole('button',{name:'生成选中图位（0）'})).toBeDisabled()
 await screen.findByText('成功')
 for(const ordinal of ['01','06','13'])fireEvent.click(screen.getByRole('checkbox',{name:`选择使用场景图 ${ordinal}`}))
 const selected=screen.getByRole('button',{name:'生成选中图位（3）'})
 act(()=>{fireEvent.click(selected);fireEvent.click(selected)})
 expect(JSON.parse(sessionStorage.getItem('aigc.batch/batch-task')!)['slot-2']).toEqual({operation:'previous-success',state:'SUCCEEDED'})
 await waitFor(()=>expect(posts).toHaveLength(3))
 expect(posts.map(p=>p.slot).sort()).toEqual(['slot-0','slot-12','slot-5'])
 expect(new Set(posts.map(p=>p.operation)).size).toBe(3)
 await waitFor(()=>expect(screen.getAllByText('尚未开始')).toHaveLength(11))
 posts.forEach(p=>{states[p.slot]=workspace(p.slot,'SUCCEEDED',p.operation)})
 await waitFor(()=>expect(screen.getAllByText('成功')).toHaveLength(4),{timeout:3500})
 const changed={...dynamic,slots:dynamic.slots.map(s=>s.id==='slot-5'?{...s,inputs_ready:false,reference_asset_id:null}:s)}
 view.rerender(<BatchGeneration {...props} task={changed}/>)
 expect(screen.getByRole('button',{name:'生成全部有效图位（13）'})).toBeEnabled()
 expect(screen.getByRole('button',{name:'生成选中图位（2）'})).toBeEnabled()
 expect(posts).toHaveLength(3)
})

it('submits all four slots including dimensions without any special confirmation',async()=>{
 const four=makeTask(4);four.slots[3]={...four.slots[3],kind:'dimensions'}
 const posts:string[]=[],states:Record<string,SlotWorkspace>={}
 vi.stubGlobal('fetch',vi.fn(async(path:string,options?:RequestInit)=>{
  const slot=path.split('/')[5]
  if(options?.method==='POST'){
   posts.push(slot);const operation=JSON.parse(String(options.body)).operation_id
   states[slot]=workspace(slot,'QUEUED',operation);return json({operation_id:operation,state:'QUEUED'},202)
  }
  return json(states[slot]||workspace(slot))
 }))
 const select=vi.fn()
 render(<BatchGeneration task={four} locked={false} onSelect={select} onBusy={vi.fn()} onSubmitted={vi.fn()}/>)
 expect(screen.getAllByRole('checkbox')).toHaveLength(4)
 expect(screen.getByRole('checkbox',{name:'选择尺寸图'})).toBeEnabled()
 expect(screen.getByText(/共 4 个图位 · 可生成 4 个 · 暂不可生成 0 个/)).toBeInTheDocument()
 expect(screen.queryByText(/尺寸核对与生成流程尚未开放/)).not.toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'查看尺寸图'}));expect(select).toHaveBeenCalledWith('slot-3')
 fireEvent.click(screen.getByRole('button',{name:'生成全部有效图位（4）'}))
 await waitFor(()=>expect(posts).toHaveLength(4))
 expect(posts.sort()).toEqual(['slot-0','slot-1','slot-2','slot-3'])
})

it('keeps missing-input and server-blocked slots visible with their own reasons',async()=>{
 const three=makeTask(3);three.slots[1]={...three.slots[1],inputs_ready:false,reference_asset_id:null}
 vi.stubGlobal('fetch',vi.fn(async(path:string)=>{
  const slot=path.split('/')[5],data=workspace(slot)
  if(slot==='slot-2')data.readiness={...data.readiness,can_generate:false,reason:'TEST_ONLY existing outcome unknown; do not retry'}
  return json(data)
 }))
 render(<BatchGeneration task={three} locked={false} onSelect={vi.fn()} onBusy={vi.fn()} onSubmitted={vi.fn()}/>)
 expect(screen.getByText(/请为本图位上传并绑定营销参考图/)).toBeInTheDocument()
 await screen.findByText(/TEST_ONLY existing outcome unknown/)
 expect(screen.getByRole('button',{name:'生成全部有效图位（1）'})).toBeEnabled()
 expect(screen.getByRole('checkbox',{name:'选择使用场景图 02'})).toBeDisabled()
 expect(screen.getByRole('checkbox',{name:'选择使用场景图 03'})).toBeDisabled()
})

it('one click submits ten unique operations, shows queue/results, and never resubmits on refresh or remount',async()=>{
 const states:Record<string,SlotWorkspace>={}
 const posts:{slot:string;body:Record<string,string>}[]=[]
 const fetch=vi.fn(async(path:string,options?:RequestInit)=>{
  const slot=path.split('/')[5]
  if(options?.method==='POST'){
   const body=JSON.parse(String(options.body));posts.push({slot,body});states[slot]=workspace(slot,'QUEUED',body.operation_id)
   return json({operation_id:body.operation_id,state:'QUEUED'},202)
  }
  return json(states[slot]||workspace(slot))
 });vi.stubGlobal('fetch',fetch)
 const select=vi.fn(),submitted=vi.fn()
 const props={task,locked:false,onSelect:select,onBusy:vi.fn(),onSubmitted:submitted}
 const first=render(<BatchGeneration {...props}/>)
 const button=screen.getByRole('button',{name:'生成全部有效图位（10）'})
 act(()=>{fireEvent.click(button);fireEvent.click(button)})
 await waitFor(()=>expect(posts).toHaveLength(10))
 expect(new Set(posts.map(p=>p.body.operation_id)).size).toBe(10)
 expect(posts.every(p=>Object.keys(p.body).sort().join(',')==='operation_id,quote_token')).toBe(true)
 expect(button).toBeDisabled()
 await waitFor(()=>expect(screen.getAllByText('排队中')).toHaveLength(10))
 posts.forEach((p,i)=>{states[p.slot]=workspace(p.slot,i===4?'FAILED':i===7?'OUTCOME_UNKNOWN':'SUCCEEDED',p.body.operation_id)})
 await screen.findByText(/TEST_ONLY model error/,{}, {timeout:3500})
 await waitFor(()=>expect(screen.getAllByText('成功')).toHaveLength(8),{timeout:3500})
 expect(screen.getByText('结果待核对')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'查看使用场景图 01'}));expect(select).toHaveBeenCalledWith('slot-0')
 expect(submitted).toHaveBeenCalledTimes(1)
 first.unmount();render(<BatchGeneration {...props}/>)
 await screen.findByText(/TEST_ONLY model error/)
 expect(posts).toHaveLength(10)
})

it('an uncertain POST is queried with the original identity and is not automatically posted again',async()=>{
 const one={...task,slots:task.slots.slice(0,1)}
 let operation='',posts=0
 vi.stubGlobal('fetch',vi.fn(async(_path:string,options?:RequestInit)=>{
  if(options?.method==='POST'){posts++;operation=JSON.parse(String(options.body)).operation_id;throw new TypeError('TEST_ONLY network result unknown')}
  return json(workspace('slot-0',operation?'OUTCOME_UNKNOWN':'NOT_STARTED',operation||undefined))
 }))
 render(<BatchGeneration task={one} locked={false} onSelect={vi.fn()} onBusy={vi.fn()} onSubmitted={vi.fn()}/>)
 fireEvent.click(screen.getByRole('button',{name:'生成全部有效图位（1）'}))
 await screen.findByText('结果待核对')
 await new Promise(resolve=>setTimeout(resolve,1700))
 expect(posts).toBe(1)
})

it('sends the read Slot version, displays 409, and requires an explicit refresh before saving a newer version',async()=>{
 let saved=structuredClone({...task,slots:task.slots.slice(0,1)})
 const writes:number[]=[]
 vi.stubGlobal('fetch',vi.fn(async(path:string,options?:RequestInit)=>{
  if(options?.method==='PUT'){
   const body=JSON.parse(String(options.body));writes.push(body.expected_version)
   if(body.expected_version!==saved.slots[0].version)return json({error:{message:'图位数据已变化，未覆盖服务器。'}},409)
   saved={...saved,slots:[{...saved.slots[0],requirements:body.requirements,version:saved.slots[0].version!+1}]}
   return json(saved)
  }
  if(path.endsWith('/workspace'))return json(workspace('slot-0'))
  return json(saved)
 }))
 render(<MemoryRouter initialEntries={['/workspace/batch-task']}><App/></MemoryRouter>)
 await screen.findByTestId('task-id')
 const textarea=screen.getByLabelText('补充制作要求')
 fireEvent.change(textarea,{target:{value:'MY_UNSAVED_REQUIREMENT'}})
 // 第二页面先保存，当前页面持有版本 3。
 saved={...saved,slots:[{...saved.slots[0],requirements:'OTHER_PAGE_NEW',version:4}]}
 fireEvent.click(screen.getByRole('button',{name:'保存使用场景图 01配置'}))
 await screen.findByText('图位数据已变化，未覆盖服务器。')
 expect(writes).toEqual([3]);expect(textarea).toHaveValue('MY_UNSAVED_REQUIREMENT')
 fireEvent.click(screen.getByRole('button',{name:'重新读取服务器配置（放弃本页未保存修改）'}))
 await waitFor(()=>expect(textarea).toHaveValue('OTHER_PAGE_NEW'))
 fireEvent.change(textarea,{target:{value:'AFTER_EXPLICIT_REFRESH'}})
 fireEvent.click(screen.getByRole('button',{name:'保存使用场景图 01配置'}))
 await waitFor(()=>expect(writes).toEqual([3,4]))
 expect(saved.slots[0].requirements).toBe('AFTER_EXPLICIT_REFRESH')
})
