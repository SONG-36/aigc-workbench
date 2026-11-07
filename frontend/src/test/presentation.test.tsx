import {expect,it,vi} from 'vitest'
import {render,screen} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {MemoryRouter} from 'react-router-dom'
import {App} from '../App'
import {executionPresentation} from '../utils/presentation'
import type {Candidate,SlotWorkspace} from '../api/generation'
import type {TaskDraft} from '../types'
import {taskHttp} from './newTaskFixture'

it('shows the actual Pass or Reject after execution, while persisted active and unknown execution take precedence',()=>{
 const c={review_status:'PASS'} as Candidate
 const data={state:'SUCCEEDED',executions:[{progress:{stage:'REVIEW_PENDING'}}]} as SlotWorkspace
 expect(executionPresentation(data,c)).toMatchObject({label:'Pass · 已通过',tone:'success'})
 expect(executionPresentation(data,{...c,review_status:'REJECT'})).toMatchObject({label:'Reject · 已拒绝'})
 expect(executionPresentation({...data,state:'RUNNING',executions:[{progress:{stage:'C07'}}] as SlotWorkspace['executions']},c).label).toBe('图片修改中')
 expect(executionPresentation({...data,state:'OUTCOME_UNKNOWN'},c).label).toBe('结果未知')
})

it('keeps per-slot unsaved settings mounted across local navigation and never saves them on selection',async()=>{
 const task:TaskDraft={id:'local-ui-task',title:'UI settings draft',mode:'quick_a',status:'draft',can_generate:false,materials_ready:false,readiness_reason:'missing_reference_assets',created_at:'2026-11-07T00:00:00Z',assets:[],
  slots:[1,2].map(n=>({id:`slot-${n}`,task_id:'local-ui-task',kind:'selling_point',position:n,output_size:'1024x1536',requirements:`saved-${n}`,reference_asset_id:null,inputs_ready:false}))}
 const http=taskHttp(task),calls=vi.fn(http.handle);vi.stubGlobal('fetch',calls)
 render(<MemoryRouter initialEntries={['/workspace/'+task.id]}><App/></MemoryRouter>)
 await screen.findByTestId('task-id');const user=userEvent.setup()
 await user.type(screen.getByRole('textbox',{name:'补充制作要求'}),' first local draft')
 await user.click(screen.getByRole('button',{name:'卖点图 02'}))
 expect(screen.getByRole('textbox',{name:'补充制作要求'})).toHaveValue('saved-2')
 await user.type(screen.getByRole('textbox',{name:'补充制作要求'}),' second local draft')
 await user.click(screen.getByRole('button',{name:'卖点图 01'}))
 expect(screen.getByRole('textbox',{name:'补充制作要求'})).toHaveValue('saved-1 first local draft')
 expect(calls.mock.calls.every(([,options])=>!options?.method)).toBe(true)
 expect(http.saved().slots.map(s=>s.requirements)).toEqual(['saved-1','saved-2'])
})

it('keeps the current source when an invalid file is selected and gives an accessible error',async()=>{
 const {FilePicker}=await import('../components/FilePicker'),onSelect=vi.fn()
 render(<FilePicker label="测试参考" action="选择参考图片" disabled={false} onSelect={onSelect}/>)
 const user=userEvent.setup({applyAccept:false})
 await user.upload(screen.getByLabelText('测试参考'),new File(['not an image'],'invalid.txt',{type:'text/plain'}))
 expect(await screen.findByRole('alert')).toHaveTextContent('当前素材未被替换')
 expect(onSelect).not.toHaveBeenCalled()
 await user.upload(screen.getByLabelText('测试参考'),new File(['image'],'valid.png',{type:'image/png'}))
 expect(onSelect).toHaveBeenCalledTimes(1);expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
