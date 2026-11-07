import type {Execution} from '../api/generation'
const labels={NOT_STARTED:'尚未开始',RUNNING:'执行中',SUCCEEDED:'成功并产生候选',FAILED:'执行失败',OUTCOME_UNKNOWN:'结果未知'}
export function ExecutionDetails({executions}:{executions:Execution[]}) {
 if(!executions.length)return null
 return <details className="execution-history"><summary>执行详情 · {executions.length} 条记录</summary>
  {executions.map(e=><div key={e.operation_id} className="execution-row"><p>{labels[e.state]} · {new Date(e.created_at).toLocaleString()}{e.execution_mode==='TEST_SUBSTITUTE'?' · 测试替代工具':''}</p>
   <p>{e.model} · Run {e.run_id||'尚未进入模型执行'}{e.duration_ms!==null?` · ${(e.duration_ms/1000).toFixed(2)} 秒`:''}</p>
   {e.usage&&<p>实际 Usage：输入 {e.usage.input_tokens??'未知'}，输出 {e.usage.output_tokens??'未知'} tokens。</p>}
   {e.usage_cost_estimate&&<p>用量费用估算 USD {e.usage_cost_estimate.usd}（费率日期 {e.usage_cost_estimate.checked_on}，非最终账单）。</p>}
   {e.error&&<p className="error">{e.error.message}（{e.error.code}）</p>}
   {e.progress?.steps?.map(s=><p key={s.capability}>{s.capability} · {s.status}{s.reused?' · 复用有效结果':''} · Run {s.run_id||'准备中'}</p>)}
  </div>)}
 </details>
}
