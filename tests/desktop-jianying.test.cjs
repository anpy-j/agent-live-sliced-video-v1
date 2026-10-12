// Opt-in integration check: reads installed Jianying drafts, never queues editing jobs.
const {_electron}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),work=path.join(root,'.runtime','jianying-ui-'+Date.now());
let app;
async function run(){
  assert.equal(process.platform,'win32','Encrypted Jianying integration requires Windows');
  const workspace=path.join(work,'profile','workspace');
  fs.mkdirSync(path.join(workspace,'data'),{recursive:true});
  if(process.env.LIVECUT_JIANYING_CACHE)fs.copyFileSync(process.env.LIVECUT_JIANYING_CACHE,path.join(workspace,'data','jianying_draft_cache.json'));
  const env={...process.env,LIVECUT_USER_DATA:path.join(work,'profile'),LIVECUT_TEST_WINDOW:'1'};
  delete env.LIVECUT_SMOKE;delete env.LIVECUT_WORKSPACE;delete env.LIVECUT_DESKTOP_PORT;
  app=await _electron.launch({...(process.env.LIVECUT_NATIVE_EXE?{executablePath:process.env.LIVECUT_NATIVE_EXE,args:[]}:{args:[root]}),env,timeout:30000});
  const page=await app.firstWindow();await page.locator('#newEdit').waitFor();
  await page.locator('[data-nav="jianying"]').click();
  await page.locator('[data-refresh-jianying]').waitFor({timeout:120000});
  const cards=page.locator('[data-edit-jianying]');assert.ok(await cards.count()>0,'No local drafts found');
  const disabled=await page.locator('[data-edit-jianying]:disabled').count();
  assert.equal(disabled,0,'Local draft parsing failed: '+await page.locator('.jianying-card p').allTextContents());
  await page.screenshot({path:path.join(work,'jianying.png'),fullPage:true});
  await page.screenshot({path:path.join(work,'jianying-viewport.png')});
  console.log(JSON.stringify({passed:true,drafts:await cards.count(),disabled,screenshot:path.join(work,'jianying.png')}));
}
run().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{await app?.close();});
