// Disposable rules UI fixtures; no production jobs or rules are modified.
const {chromium} = require('playwright');
const http = require('node:http'), fs = require('node:fs'), path = require('node:path');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '..'), output = path.join(root, '.runtime', 'rules-ui');
const jobs = [{id:'fixture', title:'重复检测示例', version:'v1', kind:'legacy'}];
let rules = [{id:'p1', pinyin:'xian huo', enabled:true, note:'现货'}];
let submitted, fail = false, slow = false, browser;
const fixtureRows = Array.from({length:20},(_,i)=>({id:i,text:i===0?'这一件超级的难做':i===19?'超级难做':`第${i}段独立信息`,start:i*3,end:i*3+3,usable:true}));
fixtureRows.push({id:20,text:'是不是超级超级好看',start:60,end:63,usable:true},
  {id:21,text:'有没有没买到我这个粉色羽绒服的',start:63,end:66,usable:true},
  {id:'removed-first',text:'超级难做',start:-3,end:0,usable:false,reason:'duration_gate'},
  {id:'water-removed',text:'可以水洗吗?',start:6.93,end:7.63,usable:false,reason:'duration_gate'},
  {id:66,text:'可以水洗吗',start:173.02,end:173.98,usable:true});
const matchTrial = options => JSON.parse(execFileSync(process.env.LIVECUT_PYTHON||'python', ['-X','utf8','-c',
  'import json,sys; from agent_video.repetition_rules import match_rows,trial_options; data=json.load(sys.stdin); print(json.dumps(match_rows(data["rows"],trial_options(data["options"])),ensure_ascii=True))'],
  {cwd:root,input:JSON.stringify({rows:fixtureRows,options}),encoding:'utf8',windowsHide:true}));
const server = http.createServer(async (req, res) => {
  const route = new URL(req.url, 'http://localhost').pathname;
  if (route.startsWith('/api/')) {
    res.setHeader('Content-Type', 'application/json');
    if (route.endsWith('/jobs')) return res.end(JSON.stringify({jobs}));
    if (route === '/api/rules/pinyin') {
      if (req.method === 'POST') {
        let body=''; for await (const chunk of req) body += chunk;
        rules.push({...JSON.parse(body),id:'p2'});
      }
      return res.end(JSON.stringify({rules}));
    }
    if (route === '/api/rules/repetition/test') {
      let body=''; for await (const chunk of req) body += chunk;
      submitted = JSON.parse(body);
      if (fail) {res.statusCode=400;return res.end(JSON.stringify({error:'任务 S2 数据不存在'}));}
      if (slow) await new Promise(resolve => setTimeout(resolve, 300));
      return res.end(JSON.stringify(matchTrial(submitted)));
    }
    if (route === '/api/rules/pinyin/test') return res.end(JSON.stringify({total:1,matched:1,newly_rejected:1,rows:[
      {id:0,text:'现货',pinyin:'xian huo',start:0,end:3,s2_usable:true,hits:[{pinyin:'xian huo',text:'现货'}]}
    ]}));
    return res.end('{}');
  }
  const file = path.join(root, 'web', route === '/' ? 'index.html' : route);
  if (!file.startsWith(path.join(root,'web')+path.sep) || !fs.existsSync(file)) {res.statusCode=404;return res.end();}
  res.setHeader('Content-Type', ({'.html':'text/html','.css':'text/css','.js':'text/javascript'})[path.extname(file)] || 'application/octet-stream');
  res.end(fs.readFileSync(file));
});
async function run() {
  fs.mkdirSync(output,{recursive:true});
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  browser = await chromium.launch({headless:true,...(process.env.LIVECUT_BROWSER ? {executablePath:process.env.LIVECUT_BROWSER} : process.platform === 'win32' ? {channel:'msedge'} : {})});
  const page = await browser.newPage({viewport:{width:1440,height:1000}}), errors=[];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/#/rules`);
  await page.getByRole('tab',{name:'拼音限制'}).waitFor();
  assert.equal(await page.getByRole('tab').count(),4);
  assert.equal(await page.locator('#pinyinPanel').isVisible(),true);
  assert.equal(await page.locator('#repetitionPanel').isVisible(),false);
  await page.locator('#pinyinTestRule').selectOption('p1');
  await page.locator('#pinyinJob').selectOption('0');
  await page.locator('#pinyinTest').click();
  await page.locator('#pinyinResults').getByText('现货',{exact:false}).first().waitFor();
  await page.getByRole('tab',{name:'重复限制'}).click();
  assert.equal(await page.locator('#pinyinPanel').isVisible(),false);
  await page.locator('#repetitionTest').click();
  assert.equal(await page.locator('#repetitionJob').evaluate(el=>el.validity.valueMissing),true);
  await page.locator('#repetitionJob').selectOption('0');
  await page.locator('#repetitionTest').click();
  await page.locator('#repetitionRows').waitFor();
  assert.equal(submitted.mode,'all'); assert.equal(submitted.min_count,2);
  assert.equal(await page.locator('#repetitionRows tbody tr').count(),2);
  assert.equal(await page.locator('[data-repeat-group]').count(),1);
  assert.deepEqual(await page.locator('[data-repeat-group] mark').allTextContents(),['超级的难做','超级难做']);
  assert.match(await page.locator('[data-repeat-group]').innerText(),/57.00–60.00s/);
  assert.doesNotMatch(await page.locator('[data-repeat-group]').innerText(),/可以水洗吗|removed-first/);
  assert.match(await page.locator('#repetitionResults').innerText(),/已剔除 2 条不参与/);
  assert.match(await page.locator('#repetitionResults').innerText(),/首次：0.00–3.00s/);
  await page.locator('#repetitionHitsOnly').uncheck();
  assert.equal(await page.locator('#repetitionRows tbody tr').count(),25);
  const removedWater = page.locator('#repetitionRows tbody tr').filter({hasText:'water-removed'});
  assert.match(await removedWater.innerText(),/已剔除，不参与重复检测/);
  const keptWater = page.locator('#repetitionRows tbody tr').filter({hasText:'173.02–173.98s'});
  assert.match(await keptWater.innerText(),/未命中/);
  await page.screenshot({path:path.join(output,'repetition-desktop.png'),fullPage:true});
  await page.getByRole('tab',{name:'拼音限制'}).click();
  assert.equal(await page.locator('#pinyinTestRule').inputValue(),'p1');
  assert.equal(await page.locator('#pinyinResults tbody tr').count(),1);
  await page.locator('#pinyinTab').focus(); await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('#repetitionTab').getAttribute('aria-selected'),'true');
  await page.locator('#repetitionCount').fill('3');
  assert.match(await page.locator('#repetitionResults').innerText(),/请重新测试/);
  await page.locator('#repetitionMode').selectOption('sentence');
  fail=true; await page.locator('#repetitionTest').click();
  await page.getByText('任务 S2 数据不存在',{exact:true}).waitFor();
  assert.equal(await page.locator('#repetitionTest').isEnabled(),true);
  fail=false; slow=true; await page.locator('#repetitionTest').click();
  await page.locator('#repetitionCount').fill('4');
  await page.waitForFunction(()=>!document.querySelector('#repetitionTest').disabled);
  assert.match(await page.locator('#repetitionResults').innerText(),/请重新测试/);
  slow=false; await page.locator('#repetitionTest').click();
  await page.locator('#repetitionRows').waitFor();
  assert.equal(await page.locator('[data-repeat-group]').count(),0);
  assert.equal(submitted.mode,'sentence'); assert.equal(submitted.min_count,4);
  await page.locator('#repetitionMode').selectOption('all');
  await page.locator('#repetitionCount').fill('2');
  await page.locator('#repetitionTest').click();
  await page.locator('[data-repeat-group]').waitFor();
  fixtureRows.push({id:100,text:'五分裤两个色,一个黑,然后一个看。',start:200,end:203,usable:true},
    {id:101,text:'我们给大家两个颜色我现在穿的是黑色',start:300,end:303,usable:true},
    {id:102,text:'褲子黑跟卡兩個色',start:400,end:403,usable:true},
    {id:103,text:'我现在给到的这件皮衣两个色',start:500,end:503,usable:true});
  await page.locator('#repetitionMode').selectOption('fact');
  await page.locator('#repetitionTest').click();
  await page.locator('[data-repeat-group] summary').filter({hasText:'可选颜色：2种'}).waitFor();
  assert.equal(submitted.mode,'fact');
  assert.equal(await page.locator('[data-repeat-group]').count(),1);
  assert.deepEqual(await page.locator('[data-repeat-group] mark').allTextContents(),['两个色','两个颜色','兩個色']);
  assert.match(await page.locator('[data-repeat-group]').innerText(),/重复信息点.*3 条子句/s);
  assert.match(await page.locator('[data-repeat-group]').innerText(),/集合未完整识别/);
  assert.match(await page.locator('[data-repeat-group]').innerText(),/我现在穿的是黑色/);
  assert.doesNotMatch(await page.locator('[data-repeat-group]').innerText(),/皮衣/);
  await page.screenshot({path:path.join(output,'color-fact-desktop.png'),fullPage:true});
  for (const width of [768,390,320]) {
    await page.setViewportSize({width,height:1000});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Overflow at '+width);
    if (width===390) await page.screenshot({path:path.join(output,'repetition-mobile.png'),fullPage:true});
  }
  assert.deepEqual(errors,[]);
  console.log('Rules UI passed: tabs, pinyin regression, phrase and color-fact trials, excluded rows, filtering, errors, stale responses and responsive layout.');
}
run().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{await browser?.close();server.close();});
