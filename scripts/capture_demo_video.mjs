/** Capture genuine app states for the edited walkthrough, using an isolated demo server.
 * Requires Node 22+, local Chrome, and SHARK_WEB_PORT/SHARK_API_PORT (defaults 3012/8012).
 * No external services, paid extraction, or real customer data are used.
 */
import {spawn} from 'node:child_process';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const out=resolve(process.env.VIDEO_CAPTURES||'.cache/video-capture');
const base=`http://127.0.0.1:${process.env.SHARK_WEB_PORT||3012}`;
const api=`http://127.0.0.1:${process.env.SHARK_API_PORT||8012}`;
const port=Number(process.env.VIDEO_DEBUG_PORT||9237);
const pause=ms=>new Promise(r=>setTimeout(r,ms));
await mkdir(out,{recursive:true});
const chrome=spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',[
  '--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--hide-scrollbars',
  `--remote-debugging-port=${port}`,`--user-data-dir=${out}/chrome-profile`,'about:blank'
],{stdio:'ignore'});
let ws;
const pending=new Map();let seq=0;
async function call(method,params={}){
  const id=++seq;
  const promise=new Promise((resolve,reject)=>pending.set(id,{resolve,reject}));
  ws.send(JSON.stringify({id,method,params}));return promise;
}
async function evaluate(expression){
  const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
  if(r.exceptionDetails)throw Error(r.exceptionDetails.text+' '+(r.exceptionDetails.exception?.description||''));
  return r.result.value;
}
async function until(expression,timeout=30000){
  const start=Date.now();while(Date.now()-start<timeout){if(await evaluate(expression))return;await pause(200);}
  throw Error('Timed out: '+expression);
}
async function navigate(query=''){
  await call('Page.navigate',{url:base+'/'+query});
  await until(`document.readyState==='complete' && Boolean(document.querySelector('main'))`);
  await pause(1200);
}
async function clickText(text,tag='button'){
  const expression=`[...document.querySelectorAll(${JSON.stringify(tag)})].find(e=>e.textContent.trim().includes(${JSON.stringify(text)}))`;
  await until(`Boolean(${expression}) && !(${expression}).disabled`);
  await evaluate(`(${expression}).scrollIntoView({block:'center',behavior:'instant'})`);
  await pause(300);
  const b=await evaluate(`(()=>{let r=(${expression}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',...b});
  await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...b});
  await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...b});
  await pause(450);
  await until(`!document.querySelector('.spin')`);
}
async function scroll(selector,block='start',offset=0){
  await evaluate(`(()=>{let e=document.querySelector(${JSON.stringify(selector)});if(!e)throw Error('Missing ${selector}');e.scrollIntoView({block:${JSON.stringify(block)},behavior:'instant'});window.scrollBy(0,${offset});})()`);
  await pause(300);
}
const shots=[];
async function shot(name,targets={}){
  await evaluate(`document.querySelector('nextjs-portal')?.remove()`);
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:0,y:0});
  await pause(250);
  const boxes={};
  for(const [key,selector]of Object.entries(targets))boxes[key]=await evaluate(`(()=>{let e=document.querySelector(${JSON.stringify(selector)});if(!e)return null;let r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};})()`);
  const result=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
  await writeFile(`${out}/${name}.png`,Buffer.from(result.data,'base64'));
  const text=await evaluate('document.body.innerText');
  await writeFile(`${out}/${name}.txt`,text);
  shots.push({name,viewport:{width:1440,height:900,scale:2},boxes,url:await evaluate('location.href')});
  await writeFile(`${out}/shots.json`,JSON.stringify(shots,null,2)+'\n');
  console.log('Captured '+name);
}
try{
  let target;
  for(let i=0;i<50;i++){
    try{target=(await(await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t=>t.type==='page');if(target)break;}catch{}
    await pause(200);
  }
  if(!target)throw Error('Chrome did not start');
  ws=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});
  ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id&&pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result);}};
  await call('Page.enable');await call('Runtime.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:2,mobile:false});
  await navigate();
  await until(`document.body.innerText.includes('Start demo')`);
  await shot('01-overview',{entry:'.demo-entry',stats:'.summary-grid'});
  await clickText('Start demo','a');
  await until(`document.querySelectorAll('.scan-row').length===3`);
  await clickText('Reset portfolio replay');
  await until(`document.querySelector('.scan-supported')?.innerText.includes('$900.00')`);
  await evaluate('window.scrollTo(0,0)');
  await shot('02-portfolio',{totals:'.scan-totals',queue:'.scan-list',guide:'.portfolio-guide'});
  await scroll('.scan-totals','start',-80);
  await shot('02b-queue',{totals:'.scan-totals',queue:'.scan-list'});
  await clickText('See why Cobalt is excluded');
  await until(`Boolean(document.querySelector('.proposal-detail'))`);
  await shot('03-excluded',{row:'.scan-row.ineligible',detail:'.proposal-detail',reason:'.proposal-reason'});
  await clickText('Harbor Logistics');
  await shot('04-missing',{row:'.scan-row.missing',blockers:'.proposal-blockers'});
  await clickText("Add Harbor’s missing ticket");
  await until(`document.querySelector('.scan-supported')?.innerText.includes('$1,380.00')`);
  await evaluate('window.scrollTo(0,0)');
  await shot('05-ticket-arrived',{totals:'.scan-totals',queue:'.scan-list'});
  await clickText('Harbor Logistics');
  await scroll('.proposal-calculation','start',-100);
  await shot('06-calculation',{calc:'.proposal-calculation',draft:'.proposal-draft'});
  await scroll('.proposal-draft pre','start',-80);
  await shot('07-request',{draft:'.proposal-draft pre'});
  await scroll('.proposal-accept','center');
  await shot('08-accept-before',{accept:'.proposal-accept'});
  await clickText('I reviewed this evidence and request.','label');
  await shot('09-accept-checked',{accept:'.proposal-accept'});
  await clickText('Accept proposal and prepare draft');
  await until(`Boolean([...document.querySelectorAll('button')].find(e=>e.textContent.includes('Open saved case')))`);
  await scroll('.proposal-detail','end');
  await shot('10-draft-saved',{detail:'.proposal-detail'});
  await clickText('Open saved case');
  await until(`document.querySelectorAll('.challenge-option').length>0`);
  await scroll('.portfolio-inspection','start',-35);
  await shot('11-challenges',{panel:'.challenge-panel',header:'.portfolio-inspection .recovery-panel-head'});
  await clickText('Remove the support ticket');
  await until(`Boolean(document.querySelector('.challenge-comparison'))`);
  await scroll('.challenge-panel','start',-25);
  await shot('12-ticket-challenge',{comparison:'.challenge-comparison',verdict:'.challenge-verdict',clause:'.challenge-clause'});
  await clickText('Import the same evidence twice');
  await until(`document.querySelector('.challenge-verdict')?.innerText.includes('does not create more credit')`);
  await scroll('.challenge-panel','start',-25);
  await shot('13-duplicate-challenge',{comparison:'.challenge-comparison',verdict:'.challenge-verdict'});
  const state=await(await fetch(`${api}/api/recovery/portfolio`)).json();
  const harbor=state.items.find(i=>i.customer.startsWith('Harbor'));
  const bundle=await fetch(`${api}/api/recovery/portfolio/cases/${harbor.case_id}/bundle`);
  if(!bundle.ok)throw Error(await bundle.text());
  await writeFile(`${out}/harbor-evidence.zip`,Buffer.from(await bundle.arrayBuffer()));
  await writeFile(`${out}/harbor-case.json`,JSON.stringify(await(await fetch(`${api}/api/recovery/portfolio/cases/${harbor.case_id}`)).json(),null,2));
  await navigate('?view=agreements&read=atlassian');
  await until(`document.body.innerText.includes('Saved model run')`);
  await shot('14-agreement',{reader:'.reader',source:'.reader-results'});
  await scroll('.term-grid','start',-30);
  await shot('14b-terms',{terms:'.term-grid'});
  await navigate('?view=demo');
  if(await evaluate(`Boolean([...document.querySelectorAll('button')].find(e=>e.textContent.includes('Reset walkthrough')&&!e.disabled))`))await clickText('Reset walkthrough');
  await until(`document.body.innerText.includes('Detect simulated outage')`);
  for(const label of ['Detect simulated outage','Record simulated support ticket','Measure monthly evidence','Approve and simulate submission','Match the sample invoice']){
    await clickText(label);await pause(700);
  }
  await until(`document.body.innerText.includes('Credit matched')`);
  await evaluate('window.scrollTo(0,0)');
  await shot('15-reconciled',{numbers:'.demo-calculation',reconciled:'.demo-reconciled',timeline:'.demo-timeline'});
  await scroll('.demo-reconciled','center');
  await shot('16-credit-matched',{reconciled:'.demo-reconciled',numbers:'.demo-calculation'});
  console.log('Capture complete: '+shots.length+' real app states.');
}catch(error){
  console.error(error);
  if(ws?.readyState===1){try{await shot('debug-failure');console.error((await evaluate('document.body.innerText')).slice(0,7000));}catch{}}
  process.exitCode=1;
}finally{ws?.close();chrome.kill('SIGTERM');}
