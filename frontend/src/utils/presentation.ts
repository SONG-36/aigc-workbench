import type {Candidate,SlotWorkspace} from '../api/generation'
export interface SlotPresentation {slotId:string;label:string;tone:string}
export function versionName(candidates:Candidate[],id:string){const index=candidates.findIndex(c=>c.image_version_id===id);return index<0?'历史版本':`V${candidates.length-index}`}
export function reviewName(status?:string){return status==='PASS'?'Pass · 已通过':status==='REJECT'?'Reject · 已拒绝':'待审核'}
const phases:Record<string,string>={PREPARING:'准备中',C01:'商品理解中',C02:'参考理解中',C03_C04_C05:'制作决定中',C06:'图片生成中',C07:'图片修改中'}
export function executionPresentation(data:SlotWorkspace|null,candidate?:Candidate){
 if(!data)return {label:'读取服务器状态…',tone:'neutral'}
 if(data.state==='QUEUED')return {label:'排队中',tone:'active'}
 if(data.state==='RUNNING')return {label:phases[data.executions[0]?.progress?.stage||'']||'执行中',tone:'active'}
 if(data.state==='OUTCOME_UNKNOWN')return {label:'结果未知',tone:'warning'}
 if(data.state==='FAILED')return {label:'执行失败',tone:'danger'}
 if(candidate)return {label:reviewName(candidate.review_status),tone:candidate.review_status==='PASS'?'success':candidate.review_status==='REJECT'?'danger':'neutral'}
 return {label:data.readiness.can_generate?'可生成':'尚未开始',tone:'neutral'}
}
