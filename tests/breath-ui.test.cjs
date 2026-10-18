// Real HTTP, FFmpeg, and browser comparison in a disposable data root.
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'), temporary=fs.mkdtempSync(path.join(os.tmpdir(),'livecut-breath-'));
const output=path.join(root,'.runtime','breath-ui');
const source=String.raw`
import json, subprocess, sys
from pathlib import Path
from agent_video.server import Application, Server
app=Application(Path(sys.argv[1]),Path(sys.argv[2]))
work=Path(sys.argv[2])/'fixture';work.mkdir()
media=work/'source.mp4'
subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=0x544b72:s=320x480:r=25:d=3','-f','lavfi','-i',r'aevalsrc=if(between(t\,0.5\,2.5)\,0.4*sin(2*PI*440*t)\,0):s=16000:d=3','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(media)],check=True)
clips=[{'id':i,'text':t,'start':0,'end':3,'usable':True} for i,t in enumerate(['羊毛质感柔软，上身轻盈舒服。','收腰的线条很显比例，搭配裙子也好看。'])]
(work/'order.json').write_text(json.dumps({'ordered_ids':[0,1]}))
(work/'manifest.json').write_text(json.dumps({'segments':clips}))
(work/'clauses.json').write_text(json.dumps({'words':[{'w':'语音','s':.5,'e':2.5}]}))
job=app.store.create_job(title='秋日羊毛外套 · 气口对比样本',source_path=str(media),workspace=str(work))
app.store.update_job(job,status='completed')
server=Server(('127.0.0.1',0),app)
print('READY:'+str(server.server_port),flush=True)
server.serve_forever()
`;
let child,browser;
(async()=>{
  fs.mkdirSync(output,{recursive:true});
  child=spawn(process.env.LIVECUT_PYTHON||'python',['-u','-X','utf8','-c',source,root,temporary],{cwd:root,windowsHide:true,env:{...process.env,NO_PROXY:'*'}});
  const port=await new Promise((resolve,reject)=>{
    let stdout='',stderr='';const timer=setTimeout(()=>reject(new Error(stderr||'startup timeout')),20000);
    child.stdout.on('data',d=>{stdout+=d;const m=stdout.match(/READY:(\d+)/);if(m){clearTimeout(timer);resolve(+m[1]);}});
    child.stderr.on('data',d=>stderr+=d);child.on('error',reject);child.on('exit',c=>{clearTimeout(timer);reject(new Error('exit '+c+stderr));});
  });
  browser=await chromium.launch({headless:true,...(process.env.LIVECUT_BROWSER?{executablePath:process.env.LIVECUT_BROWSER}:process.platform==='win32'?{channel:'msedge'}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${port}/#/rules`);
  await page.getByRole('tab',{name:'气口检测',exact:true}).click();
  const toggle=page.getByRole('switch',{name:'气口检测加入 V1 S4'});
  await page.waitForFunction(()=>!document.querySelector('[data-rule-pipeline=breath]').disabled);
  assert.equal(await toggle.getAttribute('aria-checked'),'false');
  await toggle.click();
  await page.waitForFunction(()=>document.querySelector('[data-rule-pipeline=breath]').getAttribute('aria-checked')==='true');
  await page.reload();await page.getByRole('tab',{name:'气口检测',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[data-rule-pipeline=breath]').getAttribute('aria-checked')==='true');
  await toggle.click();
  await page.waitForFunction(()=>document.querySelector('[data-rule-pipeline=breath]').getAttribute('aria-checked')==='false');
  const config=JSON.parse(fs.readFileSync(path.join(temporary,'data/rule_pipeline.json'),'utf8'));
  assert.equal(config.features.breath.enabled,false);assert.equal(config.features.filler.enabled,true);
  await page.screenshot({path:path.join(output,'desktop-empty.png'),fullPage:true});
  await page.locator('#breathJob').selectOption({index:1});
  await page.locator('#breathTest').click();
  await page.locator('.breath-metrics').waitFor({timeout:60000});
  assert.equal(await page.locator('.breath-pairs>details').count(),2);
  assert.match(await page.locator('.breath-metrics').innerText(),/1.6[0-9]/);
  const before=page.locator('.breath-compare').first().locator('video').first();
  await before.evaluate(v=>new Promise((resolve,reject)=>{if(v.readyState>=1)resolve();else{v.onloadedmetadata=resolve;v.onerror=()=>reject(new Error('video load failed'));}}));
  assert.ok(await before.evaluate(v=>v.duration)>5.9);
  const link=page.locator('.breath-compare').first().locator('a').first();
  const res=await page.request.get(new URL(await link.getAttribute('href'),page.url()).href,{headers:{Range:'bytes=0-1023'}});
  assert.equal(res.status(),206);assert.equal((await res.body()).length,1024);
  await page.locator('.breath-compare').first().locator('video').evaluateAll(videos=>Promise.all(videos.map(v=>new Promise(resolve=>{v.onseeked=resolve;v.currentTime=.1;}))));
  await page.evaluate(()=>{document.activeElement.blur();window.scrollTo(0,0);});
  await page.screenshot({path:path.join(output,'desktop-results.png'),fullPage:true});
  await page.locator('.breath-pairs summary').first().click();
  assert.equal(await page.locator('.breath-pairs video').first().isVisible(),true);
  for(const width of [768,390,320]){
    await page.setViewportSize({width,height:1000});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'overflow '+width);
    await page.evaluate(()=>{document.activeElement.blur();window.scrollTo(0,0);});
    await page.screenshot({path:path.join(output,`mobile-${width}.png`),fullPage:true});
  }
  await page.reload();await page.getByRole('tab',{name:'气口检测',exact:true}).click();
  await page.locator('.breath-metrics').waitFor();
  assert.deepEqual(errors,[]);
  console.log('Breath UI passed: persisted independent switch, trial while off, all real video pairs, playable media, range requests, result recovery and responsive screenshots.');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(child)child.kill();fs.rmSync(temporary,{recursive:true,force:true});});
