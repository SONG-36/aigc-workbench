import {render,screen,waitFor} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {MemoryRouter} from 'react-router-dom'
import {it,expect,vi} from 'vitest'
import {App} from '../App'
import type {TaskDraft} from '../types'
import {json,taskHttp} from './newTaskFixture'

vi.mock('../utils/newTaskFiles',()=>({newTaskFiles:async()=>null}))
const task:TaskDraft={id:'7cf98726-af3c-4adc-9a77-eec3ff7ff783',title:'TEST_ONLY independent slots',mode:'quick_a',
  status:'draft',can_generate:false,materials_ready:false,readiness_reason:'missing_reference_assets',created_at:'2026-11-06T00:00:00Z',assets:[],
  slots:[1,2].map(n=>({id:`slot-${n}`,task_id:'7cf98726-af3c-4adc-9a77-eec3ff7ff783',kind:'selling_point',position:n,
    reference_asset_id:null,output_size:'1536x1536',requirements:`requirement-${n}`,inputs_ready:false}))}
const mount=()=>render(<MemoryRouter initialEntries={[`/workspace/${task.id}`]}><App/></MemoryRouter>)
const file=(name:string)=>new File([name],name,{type:'image/png',lastModified:1})

it.each([false,true])('recovers an uploaded reference binding (reload=%s) without duplicate assets, then replaces and removes only its own slot',async reload=>{
  const http=taskHttp(task);let reject=true
  const calls=vi.fn(async(path:string,options?:RequestInit)=>{
    if(reject&&options?.method==='PUT'){reject=false;return json({error:{message:'TEST_ONLY binding failure'}},503)}
    return http.handle(path,options)
  });vi.stubGlobal('fetch',calls);const first=mount();await screen.findByTestId('task-id');const user=userEvent.setup()
  await user.upload(screen.getByLabelText('卖点图 01参考图片'),file('one.png'))
  expect(await screen.findByRole('alert')).toHaveTextContent('TEST_ONLY binding failure')
  expect(http.saved().assets).toHaveLength(1);expect(http.saved().slots[0].reference_asset_id).toBeNull()
  expect(screen.queryByText('已上传并绑定到当前图位')).not.toBeInTheDocument()
  if(reload){
    first.unmount();mount();await screen.findByTestId('task-id')
    await user.click(await screen.findByRole('button',{name:'继续上传并绑定卖点图 01'}))
  }else await user.click(screen.getByRole('button',{name:'安全重试本次保存'}))
  await screen.findByText('已上传并绑定到当前图位')
  expect(calls.mock.calls.filter(([p])=>p.includes('/assets?'))).toHaveLength(1)
  const writes=calls.mock.calls.filter(([,o])=>o?.method).length
  await user.click(screen.getByRole('button',{name:'放大卖点图 01参考图'}))
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  await user.click(screen.getByRole('button',{name:'关闭'}))
  expect(calls.mock.calls.filter(([,o])=>o?.method)).toHaveLength(writes)
  await user.click(screen.getByRole('button',{name:'卖点图 02'}))
  await user.upload(screen.getByLabelText('卖点图 02参考图片'),file('two.png'))
  await waitFor(()=>expect(http.saved().slots[1].reference_asset_id).not.toBeNull())
  const second=structuredClone(http.saved().slots[1])
  await user.click(screen.getByRole('button',{name:'卖点图 01'}))
  await user.upload(screen.getByLabelText('卖点图 01参考图片'),file('replacement.png'))
  await waitFor(()=>expect(http.saved().assets).toHaveLength(3))
  await waitFor(()=>expect(http.saved().slots[0].reference_asset_id).toBe(http.saved().assets[2].id))
  expect(http.saved().slots[1]).toEqual(second)
  await user.click(screen.getByRole('button',{name:'移除卖点图 01参考'}))
  await waitFor(()=>expect(http.saved().slots[0].reference_asset_id).toBeNull())
  expect(http.saved().assets).toHaveLength(3);expect(http.saved().slots[1]).toEqual(second)
  await user.click(screen.getByRole('button',{name:'删除卖点图 01'}))
  expect(http.saved().slots[0].archived).not.toBe(true)
  await user.click(screen.getByRole('button',{name:'确认删除卖点图 01'}))
  await waitFor(()=>expect(screen.queryByLabelText('卖点图 01参考图片')).not.toBeInTheDocument())
  expect(screen.getByLabelText('卖点图 02参考图片')).toBeInTheDocument();expect(http.saved().slots[1]).toEqual(second)
  expect(http.saved().slots[0].archived).toBe(true)
})
