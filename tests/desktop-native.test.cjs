const {_electron}=require('playwright');
const {execFileSync}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),work=path.join(root,'.runtime','native-ui-'+Date.now());
fs.mkdirSync(work,{recursive:true});
let app;
async function run(){
  const media=path.join(work,'video.mp4');
  execFileSync('ffmpeg',['-v','error','-f','lavfi','-i','color=blue:s=160x240:r=30:d=2','-f','lavfi','-i','sine=duration=2','-c:v','libx264','-c:a','aac',media]);
  const env={...process.env,LIVECUT_USER_DATA:path.join(work,'profile'),LIVECUT_TEST_WINDOW:'1'};
  delete env.LIVECUT_SMOKE;delete env.LIVECUT_WORKSPACE;delete env.LIVECUT_DESKTOP_PORT;
  app=await _electron.launch({...(process.env.LIVECUT_NATIVE_EXE?{executablePath:process.env.LIVECUT_NATIVE_EXE,args:[]}:{args:[root]}),env,timeout:30000});
  const page=await app.firstWindow(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.locator('#newEdit').waitFor();
  const answer=async text=>{await page.locator('#editorPromptValue').fill(text);await page.locator('#editorPromptConfirm').click();};
  await app.evaluate(({ipcMain},source)=>{ipcMain.removeHandler('livecut:files');ipcMain.handle('livecut:files',()=>[source]);},media);
  await page.locator('#newEdit').click();await answer('桌面实际导出');await page.locator('#addMedia').click();await page.locator('[data-add]').click();
  const projectId=page.url().split('/').at(-1);
  // Keep test renders small; preserve the exact desktop HTTP and bundled FFmpeg path.
  await page.evaluate(async ({id,output})=>{
    const request=async(route,method='GET',data)=>{const r=await fetch('/api/editor/'+route,{method,headers:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});const d=await r.json();if(!r.ok)throw new Error(d.error);return d;};
    await new Promise(resolve=>setTimeout(resolve,1200));
    const project=await request('projects/'+id);project.width=160;project.height=240;await request('projects/'+id,'PUT',project);
    await request('settings','PUT',{output_dir:output,audio_bitrate:'192k'});
  },{id:projectId,output:path.join(work,'output')});
  await page.reload();await page.locator('.edit-clip').waitFor();
  await page.locator('#editSeek').evaluate(e=>{e.value='1';e.dispatchEvent(new Event('input'));});
  await page.locator('.edit-clip').click();await page.locator('[data-action="split"]').click();assert.equal(await page.locator('.edit-clip').count(),2);
  await page.locator('#addSubtitle').click();await answer('桌面字幕');await page.locator('[data-property="font_size"]').fill('20');await page.locator('[data-property="font_size"]').dispatchEvent('change');
  await page.locator('#postProgress').check();await page.locator('#exportEdit').click();
  await page.waitForFunction(()=>document.querySelector('#exportList').textContent.includes('已完成'),null,{timeout:30000});
  const result=await page.evaluate(async()=>({desktop:await window.livecutDesktop.info(),exports:await fetch('/api/editor/exports').then(r=>r.json())}));
  assert.equal(result.exports.exports[0].status,'completed');assert.ok(fs.existsSync(result.exports.exports[0].output));assert.deepEqual(errors,[]);
  await page.screenshot({path:path.join(work,'desktop.png'),fullPage:true});
  console.log(JSON.stringify({passed:true,desktop:result.desktop,output:result.exports.exports[0].output,screenshot:path.join(work,'desktop.png')}));
}
run().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{await app?.close();});
