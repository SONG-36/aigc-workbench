import {act,fireEvent,render,screen,waitFor,within} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import {expect,it,vi} from 'vitest'
import {TaskCenter} from '../pages/TaskCenter'
import type {TaskDraft} from '../types'

const fixture=(id:string,title:string):TaskDraft=>({id,title,mode:'quick_a',status:'draft',can_generate:false,
 materials_ready:false,readiness_reason:'missing_product_and_reference_assets',created_at:'2026-11-13T00:00:00Z',
 assets:[],slots:[],can_delete:true,delete_blocked_reason:null})
const json=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}})
function server(){
 const tasks=[fixture('one','FIRST'),fixture('two','OTHER')],deleted=new Set<string>()
 const writes:{path:string;options:RequestInit}[]=[]
 const handler=vi.fn(async(path:string,options?:RequestInit)=>{
  if(!options?.method)return json(tasks.filter(t=>!deleted.has(t.id)))
  writes.push({path,options})
  const task=tasks.find(t=>path==='/api/tasks/'+t.id)!
  if(options.method==='PATCH'){
   const body=JSON.parse(String(options.body))
   if(body.expected_title!==task.title)return json({error:{message:'任务名称已变化，请刷新列表。'}},409)
   task.title=body.title;return json(task)
  }
  if(task.can_delete===false)return json({error:{message:'任务正在生成，暂不能删除。'}},409)
  deleted.add(task.id);return new Response(null,{status:204})
 })
 vi.stubGlobal('fetch',handler)
 return {tasks,deleted,writes,handler}
}
function mount(){return render(<MemoryRouter><TaskCenter/></MemoryRouter>)}
async function menu(title='FIRST'){
 fireEvent.click(await screen.findByRole('button',{name:'任务操作：'+title}))
}

it('renames just the chosen task and keeps the name after refresh and remount',async()=>{
 const api=server(),first=mount()
 await menu();fireEvent.click(screen.getByRole('menuitem',{name:'重命名'}))
 fireEvent.change(screen.getByLabelText('任务新名称'),{target:{value:'  NEW NAME  '}})
 fireEvent.click(screen.getByRole('button',{name:'保存名称'}))
 await screen.findByRole('link',{name:'NEW NAME'})
 expect(api.tasks.map(t=>t.title)).toEqual(['NEW NAME','OTHER'])
 expect(api.writes).toHaveLength(1)
 expect(JSON.parse(String(api.writes[0].options.body))).toEqual({title:'NEW NAME',expected_title:'FIRST'})
 fireEvent.click(screen.getByRole('button',{name:'刷新列表'}))
 await screen.findByRole('button',{name:'任务操作：NEW NAME'})
 first.unmount();mount();await screen.findByRole('link',{name:'NEW NAME'})
 expect(screen.getByRole('link',{name:'OTHER'})).toBeInTheDocument()
})

it('requires a second confirmation, hides only the deleted task, and stays hidden after refresh',async()=>{
 const api=server(),first=mount()
 await menu();fireEvent.click(screen.getByRole('menuitem',{name:'删除'}))
 let dialog=screen.getByRole('dialog',{name:'确认删除任务'})
 expect(dialog).toHaveTextContent('素材、历史版本、Pass 和执行证据仍保留')
 expect(api.writes).toHaveLength(0)
 fireEvent.click(within(dialog).getByRole('button',{name:'取消删除'}))
 expect(api.writes).toHaveLength(0)
 fireEvent.click(screen.getByRole('menuitem',{name:'删除'}))
 dialog=screen.getByRole('dialog',{name:'确认删除任务'})
 const confirm=within(dialog).getByRole('button',{name:'确认删除'})
 act(()=>{fireEvent.click(confirm);fireEvent.click(confirm)})
 await waitFor(()=>expect(screen.queryByRole('link',{name:'FIRST'})).not.toBeInTheDocument())
 expect(api.writes).toHaveLength(1);expect(api.writes[0].options.method).toBe('DELETE')
 expect(api.tasks).toHaveLength(2);expect(api.deleted.has('one')).toBe(true)
 expect(screen.getByRole('link',{name:'OTHER'})).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'刷新列表'}));await screen.findByRole('link',{name:'OTHER'})
 first.unmount();mount();await screen.findByRole('link',{name:'OTHER'})
 expect(screen.queryByRole('link',{name:'FIRST'})).not.toBeInTheDocument()
})

it('disables deletion of an active task with the backend reason',async()=>{
 const api=server();api.tasks[0].can_delete=false;api.tasks[0].delete_blocked_reason='任务排队中，请先核对执行状态。'
 mount();await menu()
 expect(screen.getByRole('menuitem',{name:'删除'})).toBeDisabled()
 expect(screen.getByText('任务排队中，请先核对执行状态。')).toBeInTheDocument()
 expect(screen.getByRole('menuitem',{name:'重命名'})).toBeEnabled()
 expect(api.writes).toHaveLength(0)
})

it('keeps a task when generation begins after the list was read and delete returns 409',async()=>{
 const api=server();mount();await menu();fireEvent.click(screen.getByRole('menuitem',{name:'删除'}))
 api.tasks[0].can_delete=false
 fireEvent.click(screen.getByRole('button',{name:'确认删除'}))
 await screen.findByRole('alert')
 expect(screen.getByRole('alert')).toHaveTextContent('任务正在生成')
 expect(screen.getByRole('link',{name:'FIRST'})).toBeInTheDocument()
 expect(api.deleted.size).toBe(0)
})

it('does not retry an uncertain deletion and reconciles by refreshing the list',async()=>{
 const api=server(),original=api.handler.getMockImplementation()!
 api.handler.mockImplementation(async(path:string,options?:RequestInit)=>{
  const response=await original(path,options)
  if(options?.method==='DELETE')throw new TypeError('TEST_ONLY lost response')
  return response
 })
 mount();await menu();fireEvent.click(screen.getByRole('menuitem',{name:'删除'}))
 fireEvent.click(screen.getByRole('button',{name:'确认删除'}))
 expect(await screen.findByRole('alert')).toHaveTextContent('不会自动重发')
 expect(api.writes).toHaveLength(1)
 fireEvent.click(screen.getByRole('button',{name:'刷新列表'}));await screen.findByRole('link',{name:'OTHER'})
 expect(screen.queryByRole('link',{name:'FIRST'})).not.toBeInTheDocument()
 expect(api.writes).toHaveLength(1)
})

it('preserves a rename draft on a stale-title conflict without overwriting the newer name',async()=>{
 const api=server();mount();await menu();fireEvent.click(screen.getByRole('menuitem',{name:'重命名'}))
 fireEvent.change(screen.getByLabelText('任务新名称'),{target:{value:'MY DRAFT'}})
 api.tasks[0].title='OTHER PAGE NAME'
 fireEvent.click(screen.getByRole('button',{name:'保存名称'}))
 expect(await screen.findByRole('alert')).toHaveTextContent('任务名称已变化')
 expect(screen.getByLabelText('任务新名称')).toHaveValue('MY DRAFT')
 expect(api.tasks[0].title).toBe('OTHER PAGE NAME')
 expect(api.writes).toHaveLength(1)
})
