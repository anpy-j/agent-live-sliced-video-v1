// Real HTTP backend, disposable data directory, no production jobs/rules are changed.
const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'livecut-filler-ui-'));
const output = path.join(root, '.runtime', 'filler-ui');
const source = `
import json, sys, subprocess
from pathlib import Path
from agent_video.server import Application, Server
app = Application(Path(sys.argv[1]), Path(sys.argv[2]))
work = Path(sys.argv[2]) / 'fixture'
work.mkdir()
texts = ['你秋冬真的这样穿又洋气又好看真的。', '其实面料很舒服。']
clauses = [{'id': i, 'text': t, 'start': i*3, 'end': i*3+3, 'usable': True, 'unit': i} for i,t in enumerate(texts)]
for name in ['timeline.json', 'clauses.filtered.json']:
    (work/name).write_text(json.dumps({'clauses': clauses}, ensure_ascii=False), encoding='utf-8')
(work/'clauses.json').write_text(json.dumps({'clauses': clauses, 'words': [
    {'w':'你秋冬', 's':0, 'e':.6}, {'w':'真的', 's':.6, 'e':1},
    {'w':'这样穿又洋气又好看', 's':1, 'e':2.6}, {'w':'真的。', 's':2.6, 'e':3},
    {'w':texts[1], 's':3, 'e':6}]}), encoding='utf-8')
(work/'order.json').write_text(json.dumps({'ordered_ids':[0,1]}), encoding='utf-8')
media = work / 'fixture.mp4'
subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                'color=c=blue:size=160x120:rate=25:duration=6', '-f', 'lavfi', '-i',
                'sine=frequency=440:duration=6', '-c:v', 'libx264', '-c:a', 'aac',
                '-shortest', str(media)], check=True, capture_output=True)
job_id = app.store.create_job(title='口头词测试素材', source_path=str(media), workspace=str(work))
app.store.update_job(job_id, status='completed')
server = Server(('127.0.0.1',0),app)
print('READY:'+str(server.server_port),flush=True)
# Synthetic tone fixture: ASR decisions are covered by test_filler_audio.py.
from unittest.mock import patch
def checked(report, *args, **kwargs):
    for row in report['rows']:
        row['audio_verification'] = {'status': 'verified'}
    return report
with patch('agent_video.filler_trial.refine_cleanup', side_effect=checked), patch('agent_video.filler_trial.verify_export', side_effect=lambda path, text: text):
    server.serve_forever()
`;
let child, browser;
async function run() {
  fs.mkdirSync(output, {recursive:true});
  child = spawn(process.env.LIVECUT_PYTHON || 'python', ['-u','-X','utf8','-c',source,root,temporary],
    {cwd:root, windowsHide:true, env:{...process.env, NO_PROXY:'*'}});
  const port = await new Promise((resolve,reject) => {
    let stdout='', stderr='';
    const timer=setTimeout(()=>reject(new Error('Backend startup timed out: '+stderr)),20000);
    child.stdout.on('data', data => {stdout+=data; const match=stdout.match(/READY:(\d+)/); if(match){clearTimeout(timer);resolve(Number(match[1]));}});
    child.stderr.on('data', data => {stderr+=data;});
    child.on('error',reject); child.on('exit',code=>{clearTimeout(timer);reject(new Error('Backend exit '+code+': '+stderr));});
  });
  browser = await chromium.launch({headless:true,...(process.env.LIVECUT_BROWSER ? {executablePath:process.env.LIVECUT_BROWSER} : process.platform==='win32' ? {channel:'msedge'} : {})});
  const page = await browser.newPage({viewport:{width:1440,height:1000}}), errors=[];
  page.on('pageerror', error=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${port}/#/rules`);
  await page.getByRole('tab',{name:'口头词清理'}).click();
  await page.getByText('暂无口头词规则，请人工添加。',{exact:true}).waitFor();
  assert.equal(await page.getByRole('tab').count(),4);
  assert.equal(await page.locator('#pinyinPanel').isVisible(),false);
  const save = async (phrase, scope) => {
    await page.locator('#fillerForm input[name=pinyin]').fill(phrase);
    if (scope) await page.locator('#fillerForm select[name=scope]').selectOption(scope);
    await page.locator('#fillerForm button[type=submit]').click();
    await page.locator('#fillerList .pinyin-rule-value').filter({hasText:phrase}).waitFor();
  };
  await save('zhen de'); await save('qi shi', 'leading');
  const savedRules = JSON.parse(fs.readFileSync(path.join(temporary,'data','filler_rules.json'),'utf8')).rules;
  assert.equal(savedRules.find(r=>r.pinyin==='zhen de').scope,'all');
  assert.equal(savedRules.find(r=>r.pinyin==='qi shi').scope,'leading');
  assert.match(await page.locator('#fillerList li').filter({hasText:'qi shi'}).innerText(),/句首/);
  const fillerSwitch = page.getByRole('switch', {name:'口头词清理加入 V1 S4'});
  await fillerSwitch.click();
  await page.waitForFunction(()=>document.querySelector('[data-rule-pipeline="filler"]').getAttribute('aria-checked')==='false');
  assert.equal(JSON.parse(fs.readFileSync(path.join(temporary,'data','rule_pipeline.json'),'utf8')).features.filler.enabled,false);
  await page.getByRole('tab',{name:'拼音限制'}).click();
  const pinyinSwitch=page.getByRole('switch',{name:'拼音限制加入 V1 S2'});
  await pinyinSwitch.click();
  await page.waitForFunction(()=>document.querySelector('[data-rule-pipeline="pinyin"]').getAttribute('aria-checked')==='false');
  await page.getByRole('tab',{name:'重复限制'}).click();
  const repetitionSwitch=page.getByRole('switch',{name:'重复限制加入 V1 S2'});
  assert.equal(await repetitionSwitch.getAttribute('aria-checked'),'false');
  await page.locator('#repetitionMode').selectOption('sentence');
  await page.locator('#repetitionCount').fill('3');
  await page.locator('#saveRepetitionPipeline').click();
  await page.getByText(/已保存：仅完整语句.*至少 3 条子句/).waitFor();
  await repetitionSwitch.click();
  await page.waitForFunction(()=>document.querySelector('[data-rule-pipeline="repetition"]').getAttribute('aria-checked')==='true');
  const config=JSON.parse(fs.readFileSync(path.join(temporary,'data','rule_pipeline.json'),'utf8'));
  assert.equal(config.features.repetition.options.min_count,3);
  assert.equal(config.features.pinyin.enabled,false); assert.equal(config.features.filler.enabled,false);
  await page.reload();
  await page.getByRole('tab',{name:'口头词清理'}).click();
  await page.waitForFunction(()=>document.querySelector('[data-rule-pipeline="filler"]').textContent.includes('已关闭'));
  await page.locator('#fillerList .pinyin-rule-value').first().waitFor();
  assert.equal(await fillerSwitch.getAttribute('aria-checked'),'false');
  await page.locator('#fillerText').fill('真的你秋冬这样穿又洋气又好看');
  await page.locator('#fillerTest').click();
  await page.locator('#fillerResults tbody tr').waitFor();
  assert.equal(await page.locator('#fillerResults tbody tr td').nth(1).innerText(),'你秋冬这样穿又洋气又好看');
  await page.locator('#fillerText').fill('你秋冬真得这样穿，真的好看，真的');
  await page.locator('#fillerTest').click();
  await page.locator('#fillerResults tbody tr').waitFor();
  assert.equal(await page.locator('#fillerResults tbody tr td').nth(1).innerText(),'你秋冬这样穿，好看');
  assert.match(await page.locator('#fillerResults').innerText(),/zhen de：真得/);
  assert.match(await page.locator('#fillerResults').innerText(),/字符 4–5/);
  // 句首规则 only cleans the clip start; in-sentence hits stay in the text.
  await page.locator('#fillerText').fill('你秋冬其实这样穿又洋气又好看');
  await page.locator('#fillerTest').click();
  await page.locator('#fillerResults tbody tr').waitFor();
  assert.equal(await page.locator('#fillerResults tbody tr td').nth(1).innerText(),'你秋冬其实这样穿又洋气又好看');
  assert.match(await page.locator('#fillerResults').innerText(),/命中 0 条/);
  await page.locator('#fillerText').fill('其实你秋冬这样穿又洋气又好看');
  await page.locator('#fillerTest').click();
  await page.locator('#fillerResults tbody tr').waitFor();
  assert.equal(await page.locator('#fillerResults tbody tr td').nth(1).innerText(),'你秋冬这样穿又洋气又好看');
  assert.match(await page.locator('#fillerResults').innerText(),/命中 1 条/);
  await page.locator('#fillerText').fill('你秋冬真得这样穿，真的好看，真的');
  const first = page.locator('#fillerList li').filter({hasText:'zhen de'});
  await first.getByRole('button',{name:'停用',exact:true}).click();
  await first.getByRole('button',{name:'启用',exact:true}).waitFor();
  await page.locator('#fillerTest').click();
  await page.locator('#fillerResults tbody tr').waitFor();
  assert.match(await page.locator('#fillerResults').innerText(),/命中 0 条/);
  const rule = JSON.parse(fs.readFileSync(path.join(temporary,'data','filler_rules.json'),'utf8')).rules[0];
  await page.locator('#fillerTestRule').selectOption(rule.id);
  await page.locator('#fillerTest').click();
  await page.locator('#fillerResults tbody tr').waitFor();
  assert.match(await page.locator('#fillerResults').innerText(),/命中 1 条/);
  await first.getByRole('button',{name:'启用',exact:true}).click();
  await first.getByRole('button',{name:'停用',exact:true}).waitFor();
  await page.locator('#fillerTestRule').selectOption('');
  await page.locator('#fillerTestMode').selectOption('job');
  assert.equal(await page.locator('#fillerTextField').isVisible(),false);
  await page.locator('#fillerJob').selectOption('0');
  await page.locator('#fillerTest').click();
  await page.locator('#fillerResults tbody tr').first().waitFor();
  assert.equal(await page.locator('#fillerResults tbody tr').count(),2);
  assert.match(await page.locator('#fillerResults').innerText(),/可按词级切点试剪 1 条/);
  assert.match(await page.locator('#fillerResults').innerText(),/0.000–0.600s \/ 1.000–2.600s/);
  assert.match(await page.locator('#fillerResults').innerText(),/同一词块/);
  const renderButtons = page.locator('[data-filler-render]');
  assert.equal(await renderButtons.count(),2);
  assert.equal(await renderButtons.nth(1).isDisabled(),true);
  const originalFiles = fs.readdirSync(path.join(temporary,'fixture')).map(name=>[name,fs.readFileSync(path.join(temporary,'fixture',name))]);
  await renderButtons.first().click();
  await page.locator('[data-filler-preview="0"]').getByText('本句已生成 2 段 · 导出音频复核通过',{exact:true}).waitFor({timeout:60000});
  assert.equal(await page.locator('#fillerResults video').count(),2);
  for (const [i, expected] of [[0,.6],[1,1.6]]) {
    const video = page.locator('#fillerResults video').nth(i);
    await video.evaluate(v => v.readyState >= 1 ? Promise.resolve() : new Promise((resolve,reject)=>{v.onloadedmetadata=resolve;v.onerror=reject;}));
    assert.ok(Math.abs(await video.evaluate(v=>v.duration)-expected)<.2);
    const response = await page.request.get(new URL(await video.getAttribute('src'),page.url()).href);
    assert.equal(response.status(),200);
  }
  for (const [name, content] of originalFiles) assert.deepEqual(fs.readFileSync(path.join(temporary,'fixture',name)),content);
  const trialsRoot = path.join(temporary,'data','filler_trials');
  const trialDirs = fs.readdirSync(trialsRoot);
  assert.equal(trialDirs.length,1);
  assert.equal(fs.readdirSync(path.join(trialsRoot,trialDirs[0])).filter(n=>n.endsWith('.mp4')).length,2);
  await page.locator('#fillerTab').focus();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
  for(const width of [768,390,320]){
    await page.setViewportSize({width,height:1000});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Overflow at '+width);
    if(width===390) await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
  }
  await page.setViewportSize({width:1440,height:1000});
  await first.getByRole('button',{name:'编辑',exact:true}).click();
  await page.locator('#fillerForm input[name=pinyin]').fill('wo gao su ni');
  await page.locator('#fillerForm button[type=submit]').click();
  await page.locator('#fillerList .pinyin-rule-value').filter({hasText:'wo gao su ni'}).waitFor();
  await page.locator('#fillerList li').filter({hasText:'wo gao su ni'}).getByRole('button',{name:'删除',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('#fillerList').textContent.includes('wo gao su ni'));
  await page.locator('#fillerForm input[name=pinyin]').fill('qi shi');
  await page.locator('#fillerForm button[type=submit]').click();
  await page.locator('#fillerMessage').getByText('该口头词规则已存在',{exact:true}).waitFor();
  await page.locator('#fillerTab').focus(); await page.keyboard.press('Home');
  assert.equal(await page.locator('#pinyinPanel').isVisible(),true);
  assert.equal(await page.locator('#fillerPanel').isVisible(),false);
  assert.deepEqual(errors,[]);
  console.log('Rule switches and filler UI passed: independent persistent V1 switches, saved repetition options, trials while off, real HTTP CRUD, S4 cut verification, keyboard tabs and mobile layout.');
}
run().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  await browser?.close();
  if(child){child.kill(); await new Promise(resolve=>child.exitCode!==null ? resolve() : child.once('exit',resolve));}
  fs.rmSync(temporary,{recursive:true,force:true});
});
