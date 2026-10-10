// Real UI + FFmpeg smoke, using disposable data and the host browser.
const {chromium}=require('playwright');
const {spawn,execFileSync}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),work=path.join(root,'.runtime','editor-ui-test-'+Date.now());
fs.mkdirSync(work,{recursive:true});
const token='editor-ui-test',errors=[];
let backend,browser,origin;
async function api(route,method='GET',data){const r=await fetch(origin+'/api/editor/'+route,{method,headers:{'Content-Type':'application/json','X-LiveCut-Desktop':token},body:data===undefined?undefined:JSON.stringify(data)});const d=await r.json();assert.ok(r.ok,JSON.stringify(d));return d;}
async function main(){
  const video=path.join(work,'source.mp4');
  execFileSync('ffmpeg',['-v','error','-f','lavfi','-i','color=blue:s=160x240:r=30:d=3','-f','lavfi','-i','sine=duration=3','-c:v','libx264','-c:a','aac',video]);
  const frozen=process.env.LIVECUT_BACKEND;
  backend=spawn(frozen||process.env.LIVECUT_PYTHON||'python',[...(frozen?[]:['-m','agent_video']),'--root',work,'--port','0'],{cwd:root,env:{...process.env,PYTHONIOENCODING:'utf-8',PYTHONUNBUFFERED:'1',LIVECUT_ASSET_ROOT:frozen?path.join(path.dirname(frozen),'_internal'):root,LIVECUT_DESKTOP_TOKEN:token},windowsHide:true});
  await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(new Error('Backend start timeout')),20000);let buffer='';backend.stdout.on('data',b=>{buffer+=b.toString();const match=buffer.match(/LIVECUT_READY (\{.*\})/);if(match){clearTimeout(timeout);origin='http://127.0.0.1:'+JSON.parse(match[1]).port;resolve();}});backend.once('exit',code=>{clearTimeout(timeout);reject(new Error('Backend exited '+code+'; see '+path.join(work,'backend.log')));});backend.on('error',reject);backend.stderr.on('data',b=>fs.appendFileSync(path.join(work,'backend.log'),b));});
  browser=await chromium.launch({headless:true,...(process.env.LIVECUT_BROWSER?{executablePath:process.env.LIVECUT_BROWSER}:process.platform==='win32'?{channel:'msedge'}:{})});
  const page=await browser.newPage({viewport:{width:1500,height:1100},extraHTTPHeaders:{'X-LiveCut-Desktop':token}});
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept(d.message().includes('字幕')?'测试字幕':'UI 测试项目'));
  await page.goto(origin+'/#/editor');await page.locator('#newEdit').click();await page.locator('#addMedia').waitFor();
  const projectId=page.url().split('/').at(-1);
  const imported=await api(`projects/${projectId}/assets`,'POST',{path:video});
  imported.project.width=160;imported.project.height=240;
  await api('projects/'+projectId,'PUT',imported.project);await page.reload();
  await page.locator('[data-add]').click();await page.locator('.edit-clip').waitFor();
  await page.locator('#editSeek').evaluate(e=>{e.value='1';e.dispatchEvent(new Event('input'));});
  await page.locator('[data-action="split"]').click();assert.equal(await page.locator('.edit-clip').count(),2);
  await page.locator('[data-action="undo"]').click();assert.equal(await page.locator('.edit-clip').count(),1);
  await page.locator('[data-action="redo"]').click();assert.equal(await page.locator('.edit-clip').count(),2);
  await page.locator('#addSubtitle').click();assert.equal(await page.locator('.edit-clip').count(),3);
  await page.locator('[data-property="style"]').selectOption('yellow');
  await page.locator('[data-property="font_size"]').fill('20');await page.locator('[data-property="font_size"]').dispatchEvent('change');
  await page.locator('#copyTimeline').click();assert.equal(await page.locator('[data-timeline]').count(),2);
  await page.locator('#saveEdit').click();await page.waitForFunction(()=>document.querySelector('#editorSaveState').textContent==='已保存');
  await page.screenshot({path:path.join(work,'editor.png'),fullPage:true});
  const saved=await api('projects/'+projectId);assert.equal(saved.timelines.length,2);assert.equal(saved.timelines[1].tracks[1].clips[0].style,'yellow');
  await api('settings','PUT',{output_dir:path.join(work,'output'),audio_bitrate:'192k'});
  await page.locator('#exportEdit').click();
  await page.waitForFunction(()=>document.querySelector('#exportList').textContent.includes('已完成'),null,{timeout:30000});
  const jobs=await api('exports');assert.equal(jobs.exports[0].status,'completed');assert.ok(fs.existsSync(jobs.exports[0].output));
  await page.goto(origin+'/#/settings');await page.locator('.settings-tabs').waitFor();assert.equal(await page.locator('.settings-tabs a').count(),6);
  assert.equal(await page.locator('[data-nav="smart-v3"],[data-nav="label"],[data-nav="skill"],[data-nav="mcp"]').count(),0);
  await page.goto(origin+'/#/settings/editor');await page.locator('#editorSettings').waitFor();
  assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,projectId,screenshot:path.join(work,'editor.png'),output:jobs.exports[0].output}));
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{await browser?.close();if(backend){try{await fetch(origin+'/api/desktop/shutdown',{method:'POST',headers:{'Content-Type':'application/json','X-LiveCut-Desktop':token},body:'{}'});}catch{}await new Promise(resolve=>{if(backend.exitCode!==null)return resolve();const timer=setTimeout(()=>{backend.kill();resolve();},12000);backend.once('exit',()=>{clearTimeout(timer);resolve();});});}});
