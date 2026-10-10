// Isolated UI checks: no local drafts or production jobs are read or modified.
const {chromium}=require('playwright');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),output=path.join(root,'.runtime','product-ui');
const names=['【百搭公式】素材','梦中安妮0806','【神裤】素材','【心机裤】3797','【D家高定】素材','【人鱼】素材','【巴黎香颂】素材','老钱主义','【肯豆同款·皮衣】素材','静奢老钱1004','冬日旋律·菱奔','温柔格调'];
let drafts=names.map((name,i)=>({id:String(i),name,suggested_title:name+'1010',modified_at:new Date(Date.UTC(2026,9,10,14-i)).toISOString(),active_job_count:i===0?1:0,cover_url:i===1?'/missing-cover.png':null,recommended_timeline:i===4?null:{name:'时间线01',timeline_duration:298+i*177,path:'fixture-'+i},timeline_error:i===4?'草稿文件不可读取':''}));
let submitted,failPost=false,browser;
let jobs=Array.from({length:32},(_,i)=>({id:'queue-'+i,title:names[i%names.length]+'1010'+(i?'-'+i:''),source_path:'D:\\直播素材\\'+names[i%names.length]+'.mp4',job_type:'timeline',target_seconds:'120-180',status:i===0?'running':i===1?'queued':i===2?'failed':'completed',progress:i===0?64:i<3?0:100,current_stage:i===0?'order':'render',updated_at:'2026-10-10T14:45:00Z',deliverables:{exists:i>=3},edit_count:i>=3?1:0}));
const queueActions=[];
const server=http.createServer(async(req,res)=>{
  const route=new URL(req.url,'http://localhost').pathname;
  if(route.startsWith('/api/')){
    res.setHeader('Content-Type','application/json');
    if(route==='/api/health')return res.end(JSON.stringify({version:'0.2.1'}));
    if(route==='/api/jianying/drafts')return res.end(JSON.stringify({drafts,defaults:{target_min:120,target_max:180,export_mode:'segments',export_dir:'D:\\作品\\LiveCut'}}));
    if(route==='/api/jobs'&&req.method==='POST'){
      let body='';for await(const chunk of req)body+=chunk;submitted=JSON.parse(body);
      if(failPost){res.statusCode=500;return res.end(JSON.stringify({error:'测试队列不可用'}));}
      return res.end(JSON.stringify({id:'test-job',title:submitted.title}));
    }
    if(route==='/api/dashboard')return res.end(JSON.stringify({active:0,counts:{completed:12},jobs:[]}));
    if(route==='/api/jobs')return res.end(JSON.stringify({jobs}));
    if(route.startsWith('/api/jobs/')&&req.method==='POST'){
      queueActions.push(route);const job=jobs.find(j=>j.id===route.split('/')[3]);
      if(route.endsWith('/delivered'))job.delivered=true;
      return res.end('{}');
    }
    return res.end('{}');
  }
  const file=path.join(root,'web',route==='/'?'index.html':route);
  if(!file.startsWith(path.join(root,'web')+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end();}
  res.setHeader('Content-Type',({'.html':'text/html','.css':'text/css','.js':'text/javascript'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));
});
async function run(){
  fs.mkdirSync(output,{recursive:true});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin='http://127.0.0.1:'+server.address().port;
  browser=await chromium.launch({headless:true,...(process.env.LIVECUT_BROWSER?{executablePath:process.env.LIVECUT_BROWSER}:process.platform==='win32'?{channel:'msedge'}:{})});
  const page=await browser.newPage({viewport:{width:1484,height:935}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(origin+'/#/jianying');await page.locator('.compact-draft').first().waitFor();
  assert.equal(await page.locator('.compact-draft').count(),12);
  await page.screenshot({path:path.join(output,'drafts-desktop.png'),fullPage:true});
  await page.locator('#draftSearch').fill('老钱');assert.equal(await page.locator('.compact-draft').count(),2);
  await page.locator('#draftSearch').fill('没有这个素材');await page.locator('#clearDraftFilters').click();assert.equal(await page.locator('.compact-draft').count(),12);
  assert.equal(await page.locator('.hero,.library-summary,.library-results,.jianying-defaults,#draftSort,.view-switch').count(),0);
  assert.equal(await page.locator('.topbar').isVisible(),false);
  assert.equal(await page.locator('[data-edit-jianying="4"]').isDisabled(),true);
  await page.locator('#draftSearch').focus();await page.keyboard.press('Tab');
  await page.waitForFunction(()=>document.activeElement.matches('.draft-edit')&&Number(getComputedStyle(document.activeElement.querySelector('span')).opacity)>.99);
  await page.locator('#draftSearch').fill(names[2]);await page.locator('[data-edit-jianying="2"]').click();
  await page.locator('.draft-state').filter({hasText:'剪辑中'}).waitFor();
  assert.equal(submitted.draft_path,'fixture-2');assert.equal(submitted.target_min,120);assert.equal(submitted.export_mode,'segments');
  assert.equal(await page.locator('#draftSearch').inputValue(),names[2]);
  failPost=true;await page.locator('[data-edit-jianying="2"]').click();await page.getByText('测试队列不可用',{exact:true}).waitFor();assert.equal(await page.locator('[data-edit-jianying="2"]').isEnabled(),true);
  await page.locator('#draftSearch').fill('');
  const fixture=drafts;
  drafts=Array.from({length:90},(_,i)=>({...fixture[i%12],id:'dense-'+i,name:fixture[i%12].name+' '+i}));
  await page.locator('[data-refresh-jianying]').click();await page.waitForFunction(()=>document.querySelectorAll('.compact-draft').length===90);
  const density=await page.locator('.compact-draft').evaluateAll(cards=>({fullyVisible:cards.filter(c=>c.getBoundingClientRect().bottom<=innerHeight).length,firstHeight:cards[0].getBoundingClientRect().height,columns:cards.filter(c=>c.getBoundingClientRect().top===cards[0].getBoundingClientRect().top).length}));
  assert.ok(density.fullyVisible>=45,JSON.stringify(density));assert.ok(density.columns>=10);assert.ok(density.firstHeight<170);
  await page.screenshot({path:path.join(output,'drafts-compact.png')});
  for(const width of [1920,1280,1024,768,390,320]){
    await page.setViewportSize({width,height:900});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Horizontal overflow at '+width);
    const outside=await page.locator('[data-edit-jianying]').evaluateAll(buttons=>buttons.some(b=>{const r=b.getBoundingClientRect(),c=b.closest('article').getBoundingClientRect();return r.right>c.right+1||r.left<c.left-1;}));assert.equal(outside,false,'Clipped button at '+width);
    if(width===390){await page.screenshot({path:path.join(output,'drafts-mobile.png')});await page.locator('#draftMenu').click();assert.equal(await page.locator('.sidebar').evaluate(el=>el.classList.contains('open')),true);await page.keyboard.press('Escape');assert.equal(await page.locator('.sidebar').evaluate(el=>el.classList.contains('open')),false);}
  }
  await page.setViewportSize({width:1484,height:935});await page.goto(origin+'/#/dashboard');await page.locator('.stat-card').first().waitFor();assert.equal(await page.locator('.topbar').isVisible(),true);await page.screenshot({path:path.join(output,'dashboard.png'),fullPage:true});
  await page.locator('#newJobButton').click();await page.locator('#newJobDialog[open]').waitFor();await page.screenshot({path:path.join(output,'new-job.png')});await page.locator('[data-close-new-job]').first().click();
  await page.goto(origin+'/#/queue');await page.locator('.queue-row').first().waitFor();
  assert.equal(await page.locator('.topbar').isVisible(),false);assert.equal(await page.locator('.hero,.panel-head').count(),0);assert.equal(await page.locator('[data-new-job]').count(),1);
  assert.equal(await page.locator('[role="progressbar"]').count(),1);assert.equal(await page.locator('[role="progressbar"]').getAttribute('aria-valuenow'),'64');
  const queueDensity=await page.locator('.queue-row').evaluateAll(rows=>({visible:rows.filter(r=>r.getBoundingClientRect().bottom<=innerHeight).length,height:rows[0].getBoundingClientRect().height}));assert.ok(queueDensity.visible>=14,JSON.stringify(queueDensity));
  await page.waitForFunction(()=>!document.querySelector('#toast').classList.contains('show'));await page.screenshot({path:path.join(output,'queue-desktop.png')});
  await page.locator('[data-queue-menu="queueActions3"]').click();await page.locator('#queueActions3').waitFor({state:'visible'});
  await page.locator('#queueActions3 [data-open-folder]').click();assert.equal(queueActions.at(-1),'/api/jobs/queue-3/open-folder');assert.equal(await page.locator('#queueActions3').isVisible(),false);
  await page.locator('[data-queue-menu="queueActions3"]').click();await page.keyboard.press('Escape');assert.equal(await page.locator('#queueActions3').isVisible(),false);
  await page.locator('[data-queue-menu="queueActions3"]').click();page.once('dialog',d=>d.dismiss());await page.locator('#queueActions3 [data-delete-job]').click();assert.equal(queueActions.length,1);
  await page.locator('[data-mark-delivered="queue-3"]').click();await page.waitForFunction(()=>document.querySelectorAll('.queue-table .status.delivered').length===1);assert.equal(queueActions.at(-1),'/api/jobs/queue-3/delivered');
  await page.locator('[data-new-job]').click();await page.locator('#newJobDialog[open]').waitFor();await page.locator('[data-close-new-job]').first().click();
  for(const width of [1280,1024,768,390,320]){
    await page.setViewportSize({width,height:935});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Queue overflow at '+width);
    await page.locator('[data-queue-menu="queueActions3"]').click();const bounds=await page.locator('#queueActions3').boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width);await page.keyboard.press('Escape');
    if(width===390)await page.screenshot({path:path.join(output,'queue-mobile.png')});
  }
  jobs=[];await page.goto(origin+'/#/dashboard');await page.setViewportSize({width:1484,height:935});await page.goto(origin+'/#/queue');await page.getByText('还没有剪辑任务',{exact:true}).waitFor();assert.equal(await page.locator('[data-new-job]').count(),1);
  drafts=[];await page.goto(origin+'/#/jianying');await page.locator('[data-refresh-jianying]').click();await page.getByText('还没有剪映草稿',{exact:true}).waitFor();
  assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,density,queueDensity,screenshots:output}));
}
run().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{await browser?.close();server.close()});
