/** Continuous browser recording of the real local app. No camera crops or title cards.
 * Start the isolated local demo on 3012/8012, then run this with Node 22+.
 * --dry-run validates the route without recording or timed pauses.
 */
import {spawn} from 'node:child_process';
import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {performance} from 'node:perf_hooks';

const dry=process.argv.includes('--dry-run');
const out=resolve('.cache/standard-video-capture');
const base='http://127.0.0.1:3012',api='http://127.0.0.1:8012';
const duration=178,port=9238;
mkdirSync(`${out}/frames`,{recursive:true});mkdirSync(`${out}/checks`,{recursive:true});mkdirSync(`${out}/downloads`,{recursive:true});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function request(path,body,method='POST',headers={}){
  const r=await fetch(api+'/api'+path,{method,headers:{'Content-Type':'application/json',...headers},body:body===undefined?undefined:body instanceof Buffer?body:JSON.stringify(body)});
  if(!r.ok)throw Error(path+': '+await r.text());return r.json();
}
// Prepare fictional, clearly labelled workspace records so the other feature pages are useful.
const uploaded=await request('/documents?kind=invoice&filename=sample-invoice.pdf',readFileSync('data/fixtures/documents/sample-invoice.pdf'),'POST',{'Content-Type':'application/pdf'});
const invoice=uploaded.confirmed?uploaded:await request(`/documents/${uploaded.id}/confirm`,{...Object.fromEntries(Object.entries(uploaded.suggestions).map(([k,v])=>[k,v.value])),reviewed:true});
const read=await request('/agreements/read',{catalog_id:'atlassian'});
let vendor=(await request('/vendors',undefined,'GET')).vendors.find(v=>v.simulated&&v.invoice_id===invoice.id);
if(!vendor){const body=Object.fromEntries(['customer','vendor','product','plan','account_reference','billing_cycle'].map(k=>[k,invoice.confirmed[k]]));
  vendor=await request('/vendors',{...body,region:'US East',owner_name:'Demo finance',owner_email:'finance@example.com',read_id:read.read_id,invoice_id:invoice.id,confirmed:true,simulated:true});}
await request('/recovery/portfolio/reset',{});
await request('/demo/walkthrough/reset',{});
const chrome=spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',[
  '--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--hide-scrollbars',
  `--remote-debugging-port=${port}`,`--user-data-dir=${out}/chrome-profile`,'about:blank'
],{stdio:'ignore'});
let ws,id=0,recording=false,startTime=0;
let pointer={x:1150,y:650};
const requests=new Map(),frames=[],chapters=[];
async function call(method,params={}){const key=++id;const p=new Promise((resolve,reject)=>requests.set(key,{resolve,reject}));ws.send(JSON.stringify({id:key,method,params}));return p;}
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;}
async function until(expression,timeout=20000){const t=performance.now();while(performance.now()-t<timeout){if(await js(expression))return;await sleep(120);}throw Error('Timed out: '+expression);}
async function move(x,y,seconds=.55){const origin={...pointer};const steps=dry?1:Math.round(seconds*30);for(let i=1;i<=steps;i++){const p=i/steps;const k=p*p*(3-2*p);await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:origin.x+(x-origin.x)*k,y:origin.y+(y-origin.y)*k});if(!dry)await sleep(1000/30);}pointer={x,y};}
function findText(value,selector='button'){return `[...document.querySelectorAll(${JSON.stringify(selector)})].find(e=>e.textContent.trim().includes(${JSON.stringify(value)}))`;}
async function scrollElement(expression,block='center'){
  await js(`(${expression}).scrollIntoView({block:${JSON.stringify(block)},behavior:${JSON.stringify(dry?'instant':'smooth')}})`);
  await sleep(dry?100:850);
}
async function scroll(selector,block='center'){await until(`Boolean(document.querySelector(${JSON.stringify(selector)}))`);await scrollElement(`document.querySelector(${JSON.stringify(selector)})`,block);}
async function clickExpression(expression){
  await until(`Boolean(${expression}) && !(${expression}).disabled`);
  const visible=await js(`(()=>{let e=${expression},r=e.getBoundingClientRect();return r.top>=65&&r.bottom<710&&e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));})()`);
  if(!visible)await scrollElement(expression);
  const target=await js(`(()=>{let r=(${expression}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
  await move(target.x,target.y);
  await sleep(dry?30:150);
  await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...target});
  await sleep(dry?30:100);
  await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...target});
  await sleep(dry?200:450);
  await until(`!document.querySelector('.spin')`);
}
async function click(value,selector='button'){await clickExpression(findText(value,selector));}
async function nav(name){await clickExpression(`[...document.querySelectorAll('nav .nav-item')].find(e=>e.textContent.trim()===${JSON.stringify(name)})`);await sleep(dry?250:550);}
async function check(name){
  const shot=await call('Page.captureScreenshot',{format:'jpeg',quality:88,captureBeyondViewport:false});
  writeFileSync(`${out}/checks/${name}.jpg`,Buffer.from(shot.data,'base64'));
  writeFileSync(`${out}/checks/${name}.txt`,await js('document.body.innerText'));
}
async function beat(seconds,name,action){
  if(!dry){const remaining=seconds*1000-(performance.now()-startTime);if(remaining>0)await sleep(remaining);}
  const t=dry?seconds:(performance.now()-startTime)/1000;
  chapters.push({planned:seconds,start:t,name});console.log(`${t.toFixed(1)}s ${name}`);
  if(action)await action();await check(name);
}
const cursorScript=`(()=>{
  function mount(){
    if(document.getElementById('recording-pointer'))return;
    const style=document.createElement('style');style.textContent='nextjs-portal{display:none!important}';document.head.append(style);
    const p=document.createElement('div');p.id='recording-pointer';p.setAttribute('aria-hidden','true');
    p.style.cssText='position:fixed;left:1150px;top:650px;width:25px;height:32px;z-index:2147483647;pointer-events:none;filter:drop-shadow(0 1px 1px #0008)';
    p.innerHTML='<svg xmlns="http://www.w3.org/2000/svg" width="25" height="32" viewBox="0 0 25 32"><path d="M2 2L3 25L9 19L15 30L20 27L14 17L23 16Z" fill="white" stroke="#111" stroke-width="1.5"/></svg>';
    document.body.append(p);document.addEventListener('mousemove',e=>{p.style.left=e.clientX+'px';p.style.top=e.clientY+'px';},true);
  }
  document.readyState==='loading'?document.addEventListener('DOMContentLoaded',mount):mount();
})()`;
try{
  let target;for(let i=0;i<60;i++){try{target=(await(await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t=>t.type==='page');if(target)break;}catch{}await sleep(200);}
  if(!target)throw Error('Chrome did not start');
  ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});
  ws.onmessage=({data})=>{
    const m=JSON.parse(data);
    if(m.id&&requests.has(m.id)){const p=requests.get(m.id);requests.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result);}
    if(m.method==='Page.screencastFrame'){
      if(recording){const file=`frames/${String(frames.length).padStart(6,'0')}.jpg`;const t=(performance.now()-startTime)/1000;writeFileSync(`${out}/${file}`,Buffer.from(m.params.data,'base64'));frames.push({t,file});}
      void call('Page.screencastFrameAck',{sessionId:m.params.sessionId});
    }
  };
  await call('Page.enable');await call('Runtime.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1280,height:720,deviceScaleFactor:1.5,mobile:false});
  await call('Page.addScriptToEvaluateOnNewDocument',{source:cursorScript});
  await call('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:out+'/downloads'});
  await call('Page.navigate',{url:base+'/'});
  await until(`document.body?.innerText.includes('Start demo')&&!document.querySelector('.spin')`);await sleep(900);
  startTime=performance.now();recording=!dry;
  if(!dry)await call('Page.startScreencast',{format:'jpeg',quality:90,maxWidth:1920,maxHeight:1080,everyNthFrame:1});
  await beat(0,'overview');
  await beat(5,'start-recovery',async()=>{await click('Start demo','a');await until(`document.querySelectorAll('.scan-row').length===3`);});
  await beat(10,'three-outcomes',async()=>{await scroll('.scan-totals','start');});
  await beat(16,'missing-ticket',async()=>{await click('Harbor Logistics');await scroll('.proposal-blockers');});
  await beat(22,'maintenance-excluded',async()=>{await click('Cobalt Studio');await scroll('.proposal-detail');});
  await beat(28,'add-support-ticket',async()=>{await click('Add Harbor’s missing ticket');await until(`document.querySelector('.scan-supported')?.innerText.includes('$1,380.00')`);});
  await beat(34,'updated-total',async()=>{await scroll('.scan-totals','start');});
  await beat(41,'explain-calculation',async()=>{await click('Harbor Logistics');await scroll('.proposal-calculation');});
  await beat(52,'prepared-email',async()=>{await scroll('.proposal-draft pre','start');});
  await beat(60,'accept-draft',async()=>{await scroll('.proposal-accept');await click('I reviewed this evidence and request.','label');await click('Accept proposal and prepare draft');});
  await beat(67,'saved-case',async()=>{await click('Open saved case');await until(`document.querySelectorAll('.challenge-option').length>0`);await scroll('.portfolio-inspection','start');});
  await beat(73,'remove-ticket-challenge',async()=>{await click('Remove the support ticket');await scroll('.challenge-comparison','start');});
  await beat(82,'duplicate-evidence-challenge',async()=>{await click('Import the same evidence twice');await scroll('.challenge-comparison','start');});
  await beat(90,'download-evidence',async()=>{await click('Download evidence bundle');await scroll('.bundle-control');});
  await beat(98,'pdf-documents',async()=>{await nav('Documents');await click('Invoice with credit');await until(`Boolean(document.querySelector('.invoice-review'))`);});
  await beat(108,'pdf-source-text',async()=>{await click('Extracted text','summary');await scroll('.pdf-pages');});
  await beat(115,'agreement-citations',async()=>{await nav('Agreements');await click('Atlassian','.saved-runs button');await until(`Boolean(document.querySelector('.term-grid'))`);await scroll('.term-grid','start');});
  await beat(123,'credit-table-source',async()=>{await scrollElement(`[...document.querySelectorAll('.term-card')].find(e=>e.querySelector('strong')?.textContent==='Credit table')`);});
  await beat(130,'datadog-setup',async()=>{await nav('Vendors');await click('Atlassian','.document-list-row');await click('Datadog monitoring','summary');await scroll('.monitoring-panel','start');});
  await beat(141,'outage-to-invoice',async()=>{await nav('Guided demo');await until(`document.body.innerText.includes('Detect simulated outage')`);});
  await beat(145,'detect-outage',async()=>{await click('Detect simulated outage');await scroll('.demo-stage','start');});
  await beat(150,'record-ticket',async()=>{await click('Record simulated support ticket');await scroll('.demo-stage','start');});
  await beat(155,'measure-evidence',async()=>{await click('Measure monthly evidence');await scroll('.demo-calculation');});
  await beat(162,'simulate-approval',async()=>{await click('Approve and simulate submission');await scroll('.demo-calculation');});
  await beat(168,'match-invoice-credit',async()=>{await click('Match the sample invoice');await scroll('.demo-reconciled');});
  await beat(174,'credit-matched',async()=>{await move(1150,650);});
  if(!dry){const left=duration*1000-(performance.now()-startTime);if(left>0)await sleep(left);recording=false;await call('Page.stopScreencast');}
  writeFileSync(`${out}/${dry?'rehearsal':'recording'}.json`,JSON.stringify({duration:dry?duration:Math.max(duration,(performance.now()-startTime)/1000),viewport:[1280,720],output:[1920,1080],frames,chapters},null,2)+'\n');
  console.log(dry?'Rehearsal complete.':`Recorded ${frames.length} actual browser frames.`);
}catch(error){console.error(error);recording=false;process.exitCode=1;if(ws?.readyState===1){try{await check('failure');console.error((await js('document.body.innerText')).slice(-5000));}catch{}}}
finally{ws?.close();chrome.kill('SIGTERM');}
