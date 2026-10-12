// Disposable API fixture: never enqueue production jobs or access real drafts.
const {chromium}=require('playwright');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),output=path.join(root,'.runtime','pipeline-v2-ui');
let browser,resolvePost,holdPost=false,supportsV2=true;
const submissions=[],errors=[];
const stages=['asr','filter','judge','order','review','render'];
const fixtureJob={id:'review-fixture',title:'V2验收测试',source_path:'fixture.mp4',status:'failed',pipeline_version:'v2',
  job_type:'timeline',target_seconds:'110-125',current_stage:'review',progress:75,runtime:{worker_alive:true},
  stages:stages.map((stage_id,n)=>({stage_id,name:stage_id,position:n,status:n<4?'succeeded':n===4?'failed':'pending',progress:n<4?1:0})),
  events:[],artifacts:[],deliverables:{exists:false}};
const server=http.createServer(async(req,res)=>{
  const route=new URL(req.url,'http://localhost').pathname;
  if(route.startsWith('/api/')){
    res.setHeader('Content-Type','application/json');
    if(route==='/api/jianying/drafts')return res.end(JSON.stringify({pipeline_versions:supportsV2?['v1','v2']:undefined,drafts:[{id:'one',name:'版本测试素材',suggested_title:'版本测试素材1010',recommended_timeline:{path:'fixture.json',timeline_duration:300}}],defaults:{target_min:110,target_max:125,export_mode:'segments'}}));
    if(route==='/api/jobs'&&req.method==='POST'){
      let body='';for await(const chunk of req)body+=chunk;
      const data=JSON.parse(body);submissions.push(data);
      if(holdPost)await new Promise(resolve=>resolvePost=resolve);
      return res.end(JSON.stringify({id:'fixture-'+submissions.length,title:data.title,pipeline_version:data.pipeline_version}));
    }
    if(route==='/api/jobs')return res.end(JSON.stringify({jobs:[]}));
    if(route==='/api/jobs/review-fixture')return res.end(JSON.stringify(fixtureJob));
    if(route==='/api/jobs/review-fixture/clauses')return res.end(JSON.stringify({ready:false,clauses:[]}));
    if(route==='/api/dashboard')return res.end(JSON.stringify({counts:{},jobs:[]}));
    return res.end('{}');
  }
  const file=path.join(root,'web',route==='/'?'index.html':route);
  if(!file.startsWith(path.join(root,'web')+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end();}
  res.setHeader('Content-Type',({'.html':'text/html','.css':'text/css','.js':'text/javascript'})[path.extname(file)]||'text/plain');
  res.end(fs.readFileSync(file));
});
async function run(){
  fs.mkdirSync(output,{recursive:true});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin='http://127.0.0.1:'+server.address().port;
  browser=await chromium.launch({headless:true,...(process.env.LIVECUT_BROWSER?{executablePath:process.env.LIVECUT_BROWSER}:process.platform==='win32'?{channel:'msedge'}:{})});
  const page=await browser.newPage({viewport:{width:1280,height:900}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(origin+'/#/jianying');
  const v1=page.locator('[data-pipeline-version="v1"]'),v2=page.locator('[data-pipeline-version="v2"]');
  await v1.waitFor();assert.equal(await v1.getAttribute('aria-pressed'),'true');
  const picker=await page.locator('.pipeline-version-picker').boundingBox(),search=await page.locator('#draftSearch').boundingBox();
  assert.ok(picker.x+picker.width<=search.x,'Picker must be left of search');
  await page.locator('[data-edit-jianying]').click();
  await page.waitForFunction(()=>!document.querySelector('[data-edit-jianying]').disabled);
  assert.equal(submissions.at(-1).pipeline_version,'v1');
  await v2.click();holdPost=true;await page.locator('[data-edit-jianying]').click();
  await page.waitForFunction(()=>document.querySelector('[data-edit-jianying]').disabled);
  await v1.click();resolvePost();holdPost=false;
  await page.waitForFunction(()=>!document.querySelector('[data-edit-jianying]').disabled);
  assert.equal(submissions.at(-1).pipeline_version,'v2','Switch cannot change in-flight job');
  await page.locator('[data-edit-jianying]').click();
  await page.waitForFunction(()=>!document.querySelector('[data-edit-jianying]').disabled);
  assert.equal(submissions.at(-1).pipeline_version,'v1');
  await v2.click();await page.locator('[data-refresh-jianying]').click();
  await page.locator('[data-pipeline-version="v2"][aria-pressed="true"]').waitFor();
  await page.screenshot({path:path.join(output,'drafts-v2-desktop.png')});
  for(const width of [1024,768,390,320]){
    await page.setViewportSize({width,height:900});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Overflow '+width);
    const button=await page.locator('[data-pipeline-version="v2"]').boundingBox();
    const input=await page.locator('#draftSearch').boundingBox();
    assert.ok(button.x+button.width<=input.x,'Picker left of search '+width);
    if(width===390)await page.screenshot({path:path.join(output,'drafts-v2-mobile.png')});
  }
  await page.reload();await page.locator('[data-pipeline-version="v1"][aria-pressed="true"]').waitFor();
  supportsV2=false;await page.locator('[data-refresh-jianying]').click();
  await page.locator('[data-pipeline-version="v2"]:disabled').waitFor();
  assert.equal(await page.locator('[data-pipeline-version="v1"]').getAttribute('aria-pressed'),'true');
  assert.deepEqual(submissions.map(s=>s.pipeline_version),['v1','v2','v1']);
  await page.setViewportSize({width:1280,height:900});
  await page.goto(origin+'/#/jobs/review-fixture');
  await page.locator('[data-stage-select="review"]').waitFor();
  assert.equal(await page.locator('#jobWorkflow .stage').count(),6);
  assert.match(await page.locator('#jobStageDetail h2').textContent(),/V2 成片验收/);
  assert.equal(await page.locator('[data-rerun-stage="review"]').count(),1);
  await page.screenshot({path:path.join(output,'v2-review-detail.png')});
  await page.setViewportSize({width:390,height:900});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'V2 detail overflow');
  assert.equal(await page.locator('.pipeline-version-tag').evaluate(el=>getComputedStyle(el).fontSize),'11px');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,submissions:submissions.map(s=>s.pipeline_version),screenshots:output}));
}
run().catch(error=>{console.error(error);process.exitCode=1}).finally(async()=>{resolvePost?.();server.closeAllConnections();server.close();await browser?.close()});
