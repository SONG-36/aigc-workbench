import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import { App } from '../App'
import type { TaskDraft } from '../types'
import { json, taskHttp } from './newTaskFixture'
const task: TaskDraft = {id:'4f078019-c9a4-4d88-9ab7-169385b39154',title:'TEST_ONLY requirements', mode:'quick_a',
  status:'draft',can_generate:false,materials_ready:false,assets:[],slots:[],readiness_reason:'missing_slots',created_at:'2026-11-06T00:00:00Z',
  overall_requirement:{id:crypto.randomUUID(),task_id:'4f078019-c9a4-4d88-9ab7-169385b39154',version:1,text:'SAVED_V1',scope:'TASK_ALL_SLOTS',saved_at:'2026-11-06T00:00:00Z',source:'EXPLICIT_OPERATOR_REQUIREMENT'}}
const mount = () => render(<MemoryRouter initialEntries={['/workspace/'+task.id]}><App /></MemoryRouter>)

it('edits and saves a version, restores it on remount, and never dispatches a model', async () => {
 const http=taskHttp(task),fetchMock=vi.fn(http.handle);vi.stubGlobal('fetch',fetchMock)
 const page=mount();const user=userEvent.setup();await screen.findByTestId('task-id')
 expect(screen.getByLabelText('整体运营要求')).toHaveValue('SAVED_V1')
 await user.clear(screen.getByLabelText('整体运营要求'));await user.type(screen.getByLabelText('整体运营要求'),'NEW_V2')
 expect(screen.getByText(/整体要求尚未保存/)).toBeInTheDocument()
 await user.click(screen.getByRole('button',{name:'保存整体运营要求'}))
 await screen.findByText('整体运营要求已保存到服务器。')
 expect(http.saved().overall_requirement).toMatchObject({text:'NEW_V2',version:2})
 const writes=fetchMock.mock.calls.filter(([,o])=>o?.method==='PUT');expect(writes).toHaveLength(1)
 expect(JSON.parse(writes[0][1]!.body as string)).toMatchObject({text:'NEW_V2',expected_version:1,operation_id:expect.any(String)})
 page.unmount();mount();await screen.findByTestId('task-id');expect(screen.getByLabelText('整体运营要求')).toHaveValue('NEW_V2')
 expect(fetchMock.mock.calls.some(([p])=>/generations|revisions/.test(p))).toBe(false)
})

it('retains draft on a stale-version conflict and permits a read without silently replacing input', async () => {
 const http=taskHttp(task);const fetchMock=vi.fn((path:string,options?:RequestInit)=> options?.method==='PUT'
  ? Promise.resolve(json({error:{message:'要求版本已变化，请读取'}},409)):http.handle(path,options))
 vi.stubGlobal('fetch',fetchMock);mount();const user=userEvent.setup();await screen.findByTestId('task-id')
 await user.clear(screen.getByLabelText('整体运营要求'));await user.type(screen.getByLabelText('整体运营要求'),'UNSAVED_DRAFT')
 await user.click(screen.getByRole('button',{name:'保存整体运营要求'}))
 expect(await screen.findByRole('alert')).toHaveTextContent('要求版本已变化')
 expect(screen.getByLabelText('整体运营要求')).toHaveValue('UNSAVED_DRAFT')
 await user.click(screen.getByRole('button',{name:'读取服务器要求版本'}))
 await waitFor(()=>expect(screen.queryByRole('alert')).not.toBeInTheDocument())
 expect(screen.getByLabelText('整体运营要求')).toHaveValue('UNSAVED_DRAFT')
 expect(http.saved().overall_requirement?.version).toBe(1)
})

it('retries a lost response with the same operation and does not create another requirement version', async () => {
 const http=taskHttp(task);let lose=true
 const fetchMock=vi.fn(async(path:string,options?:RequestInit)=>{
  const response=await http.handle(path,options)
  if(lose&&options?.method==='PUT'){lose=false;throw Error('lost response')};return response
 });vi.stubGlobal('fetch',fetchMock);mount();const user=userEvent.setup();await screen.findByTestId('task-id')
 await user.clear(screen.getByLabelText('整体运营要求'));await user.type(screen.getByLabelText('整体运营要求'),'SAME_OPERATION')
 await user.click(screen.getByRole('button',{name:'保存整体运营要求'}));await screen.findByRole('alert')
 expect(screen.getByLabelText('整体运营要求')).toBeDisabled()
 await user.click(screen.getByRole('button',{name:'安全重试本次保存'}));await screen.findByText('整体运营要求已保存到服务器。')
 const puts=fetchMock.mock.calls.filter(([,o])=>o?.method==='PUT');expect(puts).toHaveLength(2)
 expect(puts[0][1]?.body).toBe(puts[1][1]?.body);expect(http.saved().overall_requirement?.version).toBe(2)
})

it('blocks generation while overall text is unsaved, then refreshes readiness after saving', async () => {
 const readyTask={...task,materials_ready:true,slots:[{id:crypto.randomUUID(),task_id:task.id,kind:'main' as const,
  position:0,reference_asset_id:null,output_size:'1024x1536' as const,requirements:'slot requirement',inputs_ready:true}]}
 const http=taskHttp(readyTask)
 const fetchMock=vi.fn(async(path:string,options?:RequestInit)=>path.endsWith('/workspace')
  ?json({task_id:task.id,slot_id:readyTask.slots[0].id,state:'NOT_STARTED',candidates:[],executions:[],
    readiness:{can_generate:true,quote_token:'a'.repeat(64),reason:'TEST_ONLY ready'}}):http.handle(path,options))
 vi.stubGlobal('fetch',fetchMock);mount();const user=userEvent.setup();await screen.findByTestId('task-id')
 await waitFor(()=>expect(screen.getByRole('button',{name:'生成一张候选'})).not.toBeDisabled())
 await user.type(screen.getByLabelText('整体运营要求'),' UNSAVED_CHANGE')
 expect(screen.getByRole('button',{name:'生成一张候选'})).toBeDisabled()
 await user.click(screen.getByRole('button',{name:'保存整体运营要求'}));await screen.findByText('整体运营要求已保存到服务器。')
 await waitFor(()=>expect(screen.getByRole('button',{name:'生成一张候选'})).not.toBeDisabled())
 expect(fetchMock.mock.calls.some(([p])=>p.endsWith('/generations'))).toBe(false)
})
