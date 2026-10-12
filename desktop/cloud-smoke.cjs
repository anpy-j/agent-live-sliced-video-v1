// Verify the packaged cloud module against an empty, isolated workspace.
const { _electron } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const root = path.resolve(__dirname, '..');
  const profile = path.join(root,'.runtime',`cloud-pack-smoke-${Date.now()}`);
  const executablePath = process.env.LIVECUT_NATIVE_EXE || path.join(root,'desktop/dist/win-unpacked/LiveCut.exe');
  const app = await _electron.launch({executablePath,args:[],env:{...process.env,
    LIVECUT_USER_DATA:profile,LIVECUT_TEST_WINDOW:'1',LIVECUT_WORKSPACE:path.join(profile,'workspace'),
    LIVECUT_DESKTOP_PORT:'0'},timeout:45000});
  try {
    const page = await app.firstWindow();
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.locator('[data-nav=cloud]').click();
    const frame = page.frameLocator('iframe[title="LiveCut 云端管理"]');
    await frame.getByRole('button',{name:'连接并登录'}).waitFor();
    const catalog = await page.evaluate(async()=>{const r=await fetch('/api/cloud/catalog');return {status:r.status,data:await r.json()};});
    assert.equal(catalog.status,200);assert.deepEqual(catalog.data.items,[]);
    assert.deepEqual(errors,[]);
    fs.mkdirSync(path.join(root,'build/cloud-qa'),{recursive:true});
    await page.screenshot({path:path.join(root,'build/cloud-qa/packaged-windows.png')});
    console.log(JSON.stringify({passed:true,cloud:true,catalog:true,executablePath}));
  } finally {await app.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
