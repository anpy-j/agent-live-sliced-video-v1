const {chromium}=require('playwright');
const {spawn,execFileSync}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),work=path.join(root,'.runtime','postprocess-ui-'+Date.now()),token='postprocess-ui';
fs.mkdirSync(work,{recursive:true});let backend,browser,origin;
async function run(){
  const source=path.join(work,'剪映导出.mp4'),music=path.join(work,'配乐.wav');
  execFileSync('ffmpeg',['-v','error','-f','lavfi','-i','color=blue:s=160x240:r=30:d=2','-f','lavfi','-i','sine=duration=2','-c:v','libx264','-c:a','aac',source]);
  execFileSync('ffmpeg',['-v','error','-f','lavfi','-i','sine=frequency=600:duration=0.7',music]);
  const original=fs.readFileSync(source);
  backend=spawn(process.env.LIVECUT_PYTHON||'python',['-m','agent_video','--root',work,'--port','0'],{cwd:root,env:{...process.env,PYTHONIOENCODING:'utf-8',PYTHONUNBUFFERED:'1',LIVECUT_ASSET_ROOT:root,LIVECUT_DESKTOP_TOKEN:token},windowsHide:true});
  await new Promise((resolve,reject)=>{let buffer='';const timeout=setTimeout(()=>reject(new Error('Startup timeout')),20000);backend.stdout.on('data',b=>{buffer+=b.toString();const match=buffer.match(/LIVECUT_READY (\{.*\})/);if(match){clearTimeout(timeout);origin='http://127.0.0.1:'+JSON.parse(match[1]).port;resolve();}});backend.stderr.on('data',b=>fs.appendFileSync(path.join(work,'backend.log'),b));backend.once('error',reject);});
  browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
  const page=await browser.newPage({viewport:{width:1500,height:1100},extraHTTPHeaders:{'X-LiveCut-Desktop':token}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.goto(origin+'/#/postprocess');await page.locator('#ppImportSource').waitFor();
  async function importPath(button,file){await page.locator(button).click();await page.locator('#postPaths').fill(file);await page.locator('dialog:has(#postPaths) form button').first().click();}
  await importPath('#ppImportSource',source);await page.locator('#ppSources').getByText(/剪映导出/).waitFor();
  await page.locator('#ppGenerateTexture').click();await page.waitForFunction(()=>document.querySelector('#ppPip').value!=='');
  await page.locator('#ppSticker').selectOption({label:'闪烁星星.gif'});await page.locator('#ppStyle').selectOption('striped');
  await importPath('#ppImportAsset',music);await page.locator('#ppMusic option').filter({hasText:'配乐.wav'}).waitFor({state:'attached'});await page.locator('#ppMusic').selectOption({label:'配乐.wav'});
  await page.locator('#ppOutput').fill(path.join(work,'完成版'));await page.screenshot({path:path.join(work,'module.png'),fullPage:true});
  assert.equal(await page.locator('.pp-corner').count(),4);assert.equal(await page.locator('.pp-pip').evaluate(el=>el.style.opacity),'0.01');
  await page.locator('#ppSubmit').click();await page.locator('#ppJobs video').waitFor({timeout:30000});
  const result=await page.evaluate(async()=>({jobs:await fetch('/api/postprocess/jobs').then(r=>r.json()),editor:await fetch('/api/editor/projects').then(r=>r.json())}));
  assert.equal(result.jobs.exports[0].status,'completed',JSON.stringify(result.jobs));assert.ok(fs.existsSync(result.jobs.exports[0].output));assert.equal(result.editor.projects.length,0);assert.deepEqual(fs.readFileSync(source),original);assert.deepEqual(errors,[]);
  await page.reload();await page.locator('#ppJobs video').waitFor();assert.ok(await page.locator('#ppPip option').count()>3);
  console.log(JSON.stringify({passed:true,screenshot:path.join(work,'module.png'),output:result.jobs.exports[0].output}));
}
run().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{await browser?.close();if(backend){try{await fetch(origin+'/api/desktop/shutdown',{method:'POST',headers:{'Content-Type':'application/json','X-LiveCut-Desktop':token},body:'{}'});}catch{}await new Promise(resolve=>{if(backend.exitCode!==null)return resolve();const timer=setTimeout(()=>{backend.kill();resolve();},12000);backend.once('exit',()=>{clearTimeout(timer);resolve();});});}});
