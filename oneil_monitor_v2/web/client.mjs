import {validModel} from './viewmodel.mjs';

const sources={authenticated:'AUTHENTICATED_SIMULATION_CORE',mock:'MOCK_AUTHENTICATED_SIMULATION'};
const mockCases=new Set(['authenticated','expired','forbidden','unavailable','stale_worker','stale_broker','unknown']);
export class MonitorClient {
  #sequence=0;
  #controller=null;
  constructor({mode='authenticated',fetchImpl=(...args)=>globalThis.fetch(...args),clock=()=>performance.now(),onChange=()=>{},timeoutMs=10000}={}) {
    if (!Object.hasOwn(sources,mode)) throw new Error('UNSUPPORTED_MONITOR_MODE');
    if(!Number.isInteger(timeoutMs)||timeoutMs<1||timeoutMs>30000)throw new Error('INVALID_READ_TIMEOUT');
    this.timeoutMs=timeoutMs;
    this.mode=mode;this.fetch=fetchImpl;this.clock=clock;this.onChange=onChange;
    this.state={model:null,error:null,authorization:'unverified',loading:false,receivedAt:0,transitSeconds:0};
  }
  publish(update){this.state={...this.state,...update};this.onChange(this.state);}
  async read(scenario='authenticated') {
    if (this.mode==='mock'&&!mockCases.has(scenario)) throw new Error('UNKNOWN_MOCK_SCENARIO');
    const sequence=++this.#sequence;
    this.#controller?.abort();this.#controller=new AbortController();
    const started=this.clock();
    let timedOut=false;
    const requestController=this.#controller;
    const timeout=setTimeout(()=>{timedOut=true;requestController.abort();},this.timeoutMs);
    // Never clear an error or re-authorize retained data merely by starting a read.
    this.publish({loading:true});
    try {
      const endpoint=this.mode==='authenticated'?'/api/v2/status':`fixtures/${scenario}.json`;
      const response=await this.fetch(endpoint,{method:'GET',credentials:this.mode==='authenticated'?'same-origin':'omit',cache:'no-store',redirect:'error',signal:this.#controller.signal,headers:{Accept:'application/json'}});
      if(sequence!==this.#sequence)return;
      if(this.mode==='authenticated'&&(response.status===401||response.status===403)){
        this.publish({model:null,error:response.status===401?'AUTHENTICATION_REQUIRED':'ACCESS_DENIED',authorization:'required',receivedAt:0,transitSeconds:0});
        return;
      }
      const payload=await response.json();
      if(sequence!==this.#sequence)return;
      // Mock response envelopes come from in-process ControlAPI requests. The
      // UI marks them as mock; no browser credentials or login are simulated.
      const status=this.mode==='mock'?payload.http_status:response.status;
      const body=this.mode==='mock'?payload.body:payload;
      if(!response.ok||!Number.isInteger(status))throw new Error('READ_FAILED');
      if(status===401||status===403){
        this.publish({model:null,error:status===401?'AUTHENTICATION_REQUIRED':'ACCESS_DENIED',authorization:'required',receivedAt:0,transitSeconds:0});
        return;
      }
      if(status!==200)throw new Error('READ_FAILED');
      if(!validModel(body,sources[this.mode]))throw new Error('INVALID_SNAPSHOT');
      const finished=this.clock();
      this.publish({model:body,error:null,authorization:this.mode==='mock'?'mock':'authenticated',receivedAt:finished,transitSeconds:Math.max(0,(finished-started)/1000)});
    } catch(error) {
      if(sequence!==this.#sequence||(error?.name==='AbortError'&&!timedOut))return;
      this.publish({error:'READ_FAILED'});
    } finally {
      clearTimeout(timeout);
      if(sequence===this.#sequence)this.publish({loading:false});
    }
  }
  suspend(){
    ++this.#sequence;this.#controller?.abort();
    this.publish({model:null,loading:false,receivedAt:0,transitSeconds:0,authorization:this.state.authorization==='required'?'required':'unverified'});
  }
  mayPoll(){return this.state.authorization!=='required'&&!this.state.loading;}
  stop(){++this.#sequence;this.#controller?.abort();this.publish({loading:false});}
  elapsedSeconds(){return this.state.model?this.state.transitSeconds+Math.max(0,(this.clock()-this.state.receivedAt)/1000):0;}
}
