import {observedStatus,filterRequests,actionableWarning} from './viewmodel.mjs';
import {MonitorClient} from './client.mjs';
const $ = id=>document.getElementById(id);
const mode=document.documentElement.dataset.monitorMode;
if(!['authenticated','mock'].includes(mode))throw new Error('UNSUPPORTED_MONITOR_MODE');
let model=null, filter='all', error=false, loading=false, authError=null;
const client=new MonitorClient({mode,onChange:onClientState});
const formats={time:new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}),date:new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})};
const warnings={
 AUTHENTICATION_REQUIRED:['인증이 필요하거나 세션이 만료되었습니다.','기존 관측 기록을 화면에서 지웠습니다. 별도로 제공된 로그인 경로에서 인증한 뒤 상태를 다시 확인하세요.'],
 ACCESS_DENIED:['이 세션에는 조회 권한이 없습니다.','기존 관측 기록을 화면에서 지웠습니다. 서버에서 허용된 소유자·계좌 범위를 확인해야 합니다.'],
 READ_FAILED:['관측을 읽지 못했습니다. 현재 상태는 미확인입니다.','아래 기록이 남아 있다면 마지막으로 읽은 기록입니다. 최신 상태로 판단하거나 재전송하지 마세요.'],
 NO_SNAPSHOT:['확인할 수 있는 관측이 없습니다.','엔진, 브로커, 주문 상태를 추정하지 않습니다. 모든 제어 기능은 잠겨 있습니다.'],
 UNKNOWN_OUTCOME:['제출 결과 미확인. 같은 주문을 다시 보내면 안 됩니다.','브로커가 주문을 받았을 수 있습니다. 정확한 주문 이력 대조 전까지 미확인 상태를 유지합니다.'],
 SNAPSHOT_NOT_FRESH:['관측의 최신성 기준을 넘었습니다.','마지막 관측 시각을 유지하며, 새 증거 없이 정상 상태로 바꾸지 않습니다.'],
 WORKER_NOT_FRESH:['엔진의 최근 생존 신호를 확인할 수 없습니다.','대기열과 체결 기록은 마지막 관측입니다. 엔진 생존과 브로커 동기화는 별도 증거입니다.'],
 BROKER_NOT_FRESH:['브로커 대조 기록이 최신이 아닙니다.','엔진 heartbeat가 있어도 브로커 이력이 최신임을 뜻하지 않습니다. 주문 결과를 추정하지 않습니다.'],
 HALTED:['새 제출이 중지된 기록입니다.','중지해도 이미 제출된 주문이 취소되지는 않습니다. 자동 재개나 자동 재전송은 없습니다.'],
 READ_ONLY:['시뮬레이션 관측을 읽고 있습니다.','계좌 중지가 해제된 기록이어도 실거래 준비 상태를 뜻하지 않습니다. 전략·거래 세션·소유자 인증은 별도 확인이 필요합니다.'],
};
const freshness={fresh:['최근 관측','good'],stale:['오래된 관측','warn'],missing:['관측 없음','neutral'],clock_error:['시각 불일치','bad']};
function text(id,value){$(id).textContent=value;}
function badge(id,label,kind){text(id,label);$(id).className=`badge ${kind}`;}
function elapsed(){return client.elapsedSeconds();}
function formatDate(stamp){return stamp ? `${formats.date.format(new Date(stamp))} KST` : '관측 정보 없음';}
function renderHealth(){
 const age=elapsed();
 const code=authError||actionableWarning(model,age,error), [title,copy]=warnings[code];
 text('notice-title',title);text('notice-copy',copy);
 if(!model){return;}
 for(const [prefix,evidence] of [['worker',model.heartbeat],['broker',model.broker_sync]]){
   const status=error?'missing':observedStatus(evidence,age);
   const [label,kind]=error?['현재 미확인','neutral']:freshness[status];badge(`${prefix}-badge`,label,kind);
   const seconds=evidence.age_seconds;
   text(`${prefix}-age`,seconds===null?'미확인':seconds<0?'시각 오류':`${Math.floor(seconds+age)}초 전`);
   text(`${prefix}-time`,formatDate(evidence.observed_at));
 }
 badge('control-badge',error?'마지막 관측':model.control.halted?'중지 기록':'시뮬레이션',error?'neutral':model.control.halted?'warn':'neutral');
 text('control-status',model.control.halted?'새 제출 중지':'중지 해제 기록');
 text('revision',`제어 버전 ${model.control.revision} · 관측된 기록`);
 text('loaded-at',`${loading?'읽는 중 · ':error?'읽기 실패 · ':''}${mode==='mock'?'모의 재생':'조회'} ${Math.floor(age)}초`);
}
function rowBadge(row){const span=document.createElement('span');span.className=`badge ${row.group==='unknown'?'bad':row.group==='filled'?'good':row.group==='pending'?'warn':'neutral'}`;span.textContent=row.label;return span;}
function renderRows(){
 const body=$('requests-body');body.replaceChildren();
 if(!model){return;}
 const selected=location.hash.slice(1);
 const rows=filterRequests(model.requests,filter);
 for(const row of rows){
   const tr=document.createElement('tr');if(selected===row.request_id)tr.className='selected';
   const id=document.createElement('td'),button=document.createElement('button');button.className='request-link';button.textContent=`REQ · ${row.request_id.slice(-8)}`;button.setAttribute('aria-label',`요청 ${row.request_id} 상세 보기`);button.setAttribute('aria-expanded',String(selected===row.request_id));button.setAttribute('aria-controls','detail');button.addEventListener('click',()=>{location.hash=row.request_id;});id.append(button);
   const state=document.createElement('td'),code=document.createElement('span');code.className='state-code mono';code.textContent=row.state;state.append(rowBadge(row),code);
   const quantity=document.createElement('td');quantity.className='numeric';quantity.textContent=`${row.filled_quantity}주`;
   const updated=document.createElement('td');updated.className='time';updated.textContent=formats.time.format(new Date(row.updated_at));
   tr.append(id,state,quantity,updated);body.append(tr);
 }
 $('empty').hidden=rows.length>0;
 text('count-all',model.requests.length);for(const group of ['pending','unknown','filled','closed'])text(`count-${group}`,model.counts[group]);
 text('window-count',`${model.requests.length}건 표시 · 전체 합계 아님`);
 for(const button of $('filters').querySelectorAll('button'))button.setAttribute('aria-pressed',String(button.dataset.filter===filter));
}
function renderDetail(focus=false){
 const id=location.hash.slice(1),row=model?.requests.find(row=>row.request_id===id);
 $('detail').hidden=!row;
 if(!row){$('detail-values').replaceChildren();text('detail-title','요청 상세');text('cancel-reason','조회 전용 모니터에서는 실행할 수 없습니다.');return;}
 text('detail-title',`${row.label} · 요청 상세`);
 const entries=[['요청 ID',row.request_id],['정확한 상태',row.state],['판정 이유',row.reason],['제출 시도',`${row.attempts}회 · 재전송 버튼 없음`],['확인된 체결 수량',`${row.filled_quantity}주 · 마지막 확인 기록, 전체 주문 수량 미제공`],['생성 시각',formatDate(row.created_at)],['최종 변경',formatDate(row.updated_at)]];
 const dl=$('detail-values');dl.replaceChildren();for(const [key,value] of entries){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key;dd.textContent=value;dl.append(dt,dd);}
 text('cancel-reason',row.can_cancel_before_admission?'원본 상태상 제출 전 취소 대상이지만, 이 모니터의 실행 경로는 잠겨 있습니다.':'이미 제출되었거나 종료된 요청입니다. 이 화면에서 취소·재전송할 수 없습니다.');
 if(focus){$('close-detail').focus({preventScroll:true});$('detail').scrollIntoView({block:'nearest',behavior:'smooth'});}
}
function clearDetail(){const id=location.hash.slice(1);history.pushState(null,'',location.pathname+location.search);renderRows();renderDetail();const previous=[...document.querySelectorAll('.request-link')].find(button=>button.getAttribute('aria-label').includes(id));previous?.focus();}
function clearPrivateView(){
 $('requests-body').replaceChildren();$('detail').hidden=true;$('detail-values').replaceChildren();$('empty').hidden=true;
 text('detail-title','요청 상세');text('cancel-reason','조회 전용 모니터에서는 실행할 수 없습니다.');
 history.replaceState(null,'',location.pathname+location.search);
 for(const id of ['all','pending','unknown','filled','closed'])text(`count-${id}`,'—');
 for(const prefix of ['worker','broker']){badge(`${prefix}-badge`,'미확인','neutral');text(`${prefix}-age`,'—');text(`${prefix}-time`,'인증된 관측 없음');}
 badge('control-badge','미확인','neutral');text('control-status','—');text('revision','제어 버전 —');text('window-count','인증된 관측 없음');
 text('loaded-at','인증된 관측 없음');
}
function onClientState(state){
 const old=model;model=state.model;loading=state.loading;error=Boolean(state.error);
 authError=['AUTHENTICATION_REQUIRED','ACCESS_DENIED'].includes(state.error)?state.error:null;
 text('auth-state',state.error&&!authError?'현재 확인 불가':state.authorization==='authenticated'?'읽기 인증됨':state.authorization==='mock'?'모의 세션 확인':'인증 필요');
 if(!model)clearPrivateView();
 if(model!==old){renderRows();renderDetail();}
 renderHealth();
}
async function load(){
 if(mode==='authenticated'&&document.visibilityState!=='visible')return;
 await client.read($('scenario').value);
}
if(mode==='mock'){
 $('scenario-picker').hidden=false;
 text('source-banner-label','MOCK / SIMULATION ONLY');
 text('source-banner-copy','인증 동작 모의 검증 · 가상 코어 · 실제 로그인 없음');
 text('source-description','실제 ControlAPI를 임시 테스트 세션과 가상 코어로 호출한 응답입니다. 브라우저 로그인이나 실계좌 연결이 아니며, 시나리오를 읽을 때 표본 시계가 재시작됩니다.');
}else{
 text('source-banner-copy','시뮬레이션 코어 조회 · 주문·활성화 기능 없음');
}
$('scenario').addEventListener('change',()=>{history.replaceState(null,'',location.pathname+location.search);renderDetail();load();});
$('refresh').addEventListener('click',load);
$('filters').addEventListener('click',event=>{const button=event.target.closest('button[data-filter]');if(button){filter=button.dataset.filter;renderRows();}});
$('close-detail').addEventListener('click',clearDetail);
window.addEventListener('hashchange',()=>{renderRows();renderDetail(true);});
window.addEventListener('popstate',()=>{renderRows();renderDetail();});
window.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('detail').hidden)clearDetail();});
document.addEventListener('visibilitychange',()=>{
 if(mode==='authenticated'){
   if(document.visibilityState==='hidden')client.suspend();
   else if(client.mayPoll())load();
 }
 renderHealth();
});
// Do not restore account evidence from the back/forward cache before revalidation.
window.addEventListener('pagehide',()=>client.suspend());
window.addEventListener('pageshow',event=>{if(event.persisted&&client.mayPoll())load();});
setInterval(renderHealth,1000);
// No hidden-tab polling and no automatic retry after authorization failure.
setInterval(()=>{if(mode==='authenticated'&&client.mayPoll()&&document.visibilityState==='visible')load();},5000);
load();
