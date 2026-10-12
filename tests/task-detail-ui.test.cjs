// Isolated fixtures; never connects to production tasks.
const {chromium}=require('playwright');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),output=path.join(root,'.runtime','task-detail-ui');
let phase=0,browser;
const job={id:'detail-fixture',title:'【夜幕星空】素材1012-1剪映草稿',source_path:'【夜幕星空】素材 / 时间线01（约16分钟）',job_type:'timeline',pipeline_version:'v1',target_seconds:'120-180',status:'running',progress:40,current_stage:'judge',created_at:'2026-10-12T12:00:00Z',updated_at:'2026-10-12T12:01:00Z',runtime:{worker_alive:true},ai_configuration:{ai_provider:'workbuddy',ai_model:'deepseek-v4.1-flash'},events:[{id:'event-1',stage_id:'judge',level:'info',created_at:'2026-10-12T12:01:00Z',message:'AI 可用性判定已启动'}],artifacts:[],stages:['asr','filter','judge','order','render'].map((stage_id,i)=>({stage_id,status:i<2?'succeeded':i===2?'running':'pending',progress:i<2?1:0,started_at:i===2?'2026-10-12T12:01:00Z':null}))};
function clauses(){
  const rows=Array.from({length:80},(_,i)=>({id:i,start:i*2,end:i*2+2,text:'这款上身很显气质，黑色可以搭配白衬衫 '+i,s2_usable:i!==79,s2_reason:i===79?'stage_chatter':'',usable:phase&&i<3?i!==1:null,s3_judged:!!phase&&i<3,reason:i===1?'场控话术':'',order:phase===2&&i===0?0:null,s4_selected:phase===2&&i<3&&i!==1?i===0:null}));
  return {job_id:job.id,ready:true,counts:{total:80,s2_passed:79,s2_rejected:1,usable:phase?2:0,rejected_by_s3:phase?1:0,s3_pending:phase?76:79,s4_selected:phase===2?1:0,s4_rejected:phase===2?1:0},clauses:rows};
}
const server=http.createServer((req,res)=>{
  const route=new URL(req.url,'http://localhost').pathname;
  if(route.startsWith('/api/')){res.setHeader('Content-Type','application/json');return res.end(JSON.stringify(route.endsWith('/clauses')?clauses():route==='/api/jobs/'+job.id?job:route==='/api/health'?{version:'fixture'}:route==='/api/dashboard'?{jobs:[],counts:{},active:0}:{}));}
  const file=path.join(root,'web',route==='/'?'index.html':route);
  if(!file.startsWith(path.join(root,'web')+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end();}
  res.setHeader('Content-Type',({'.html':'text/html','.css':'text/css','.js':'text/javascript'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));
});
async function run(){
  fs.mkdirSync(output,{recursive:true});await new Promise(r=>server.listen(0,'127.0.0.1',r));
  browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
  const page=await browser.newPage({viewport:{width:1667,height:922}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port+'/#/jobs/'+job.id);await page.locator('.lb-row').first().waitFor();
  assert.equal(await page.locator('.lb-row.no').count(),0);assert.equal(await page.locator('.lb-row.pending').count(),79);
  assert.equal(await page.locator('#jobControls').evaluate(el=>el.closest('.top-actions')!==null),true);
  assert.ok((await page.locator('#deleteJob').boundingBox()).x<(await page.locator('#newJobButton').boundingBox()).x);
  const workspace=await page.locator('.task-workspace').boundingBox(),results=await page.locator('.task-results').boundingBox();assert.ok(results.width>workspace.width*.6);
  await page.locator('.clause-list').evaluate(el=>el.scrollTop=350);await page.evaluate(()=>{window.savedRow=document.querySelector('[data-clause-id="10"]');window.savedWorkflow=document.querySelector('#jobWorkflow');window.savedApp=document.querySelector('.task-detail-page');});
  phase=1;
  await page.waitForFunction(()=>document.querySelector('[data-clause-id="1"]').textContent.includes('S3 剔除'));
  assert.equal(await page.locator('.lb-row.pending').count(),76);
  assert.equal(await page.evaluate(()=>savedRow===document.querySelector('[data-clause-id="10"]')&&savedWorkflow===document.querySelector('#jobWorkflow')&&savedApp===document.querySelector('.task-detail-page')),true);
  assert.equal(await page.locator('.clause-list').evaluate(el=>el.scrollTop),350);
  await page.locator('[data-clause-filter="s3rej"]').click();assert.equal(await page.locator('.lb-row').count(),1);
  phase=2;await page.waitForFunction(()=>document.querySelector('[data-clause-filter="s4"] em').textContent==='1');assert.equal(await page.locator('[data-clause-filter="s3rej"]').getAttribute('aria-pressed'),'true');
  for(const [filter,count] of [['s4',1],['s4rej',1],['s3',2],['s2rej',1],['pending',76],['all',80],['s2',79]]){await page.locator('[data-clause-filter="'+filter+'"]').click();assert.equal(await page.locator('.lb-row').count(),count);}
  job.current_stage='render';job.progress=84;job.stages[2].status='succeeded';job.stages[3].status='succeeded';
  const rendering=job.stages[4];rendering.status='running';rendering.progress=.2;rendering.result={completed:3,total:15,phase:'导出片段'};rendering.message='导出片段 3/15 · 20%';
  await page.waitForFunction(()=>document.querySelector('[data-stage-select="render"] small').textContent==='3/15 · 20%');
  assert.equal(await page.locator('[data-stage-select="render"]').getAttribute('aria-current'),'step');
  rendering.result.completed=4;rendering.progress=.26;
  await page.waitForFunction(()=>document.querySelector('[data-stage-select="render"] small').textContent==='4/15 · 26%');
  assert.equal(await page.locator('[data-stage-select="render"]').evaluate(el=>el.classList.contains('succeeded')),false);
  await page.screenshot({path:path.join(output,'render-progress.png')});
  await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
  for(const width of [1280,1024,768,390,320]){await page.setViewportSize({width,height:922});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'overflow at '+width);if(width===390)await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});}
  await page.goto('http://127.0.0.1:'+server.address().port+'/#/dashboard');await page.locator('.stat-card').first().waitFor();assert.equal(await page.locator('#jobControls button').count(),0);
  assert.deepEqual(errors,[]);console.log('Task detail UI passed: live verdicts, stable nodes/scroll/filter, S2/S3/S4 lists, toolbar and responsive layout.');
}
run().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{await browser?.close();server.close();});
