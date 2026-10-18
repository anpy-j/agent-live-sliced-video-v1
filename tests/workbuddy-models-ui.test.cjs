// Temporary settings fixture only: no production settings or paid model calls.
const {chromium}=require('playwright');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const output=path.join(root,'.runtime','workbuddy-models-ui');
let browser,refreshCount=0,hold=false,release;
const errors=[],saves=[];
const settings={ai_provider:'workbuddy',ai_model:'saved-model',engine_path:'fixture',ai_engine:'llm',
  ai_models:{workbuddy:[{id:'auto',name:'自动选择'},{id:'old-model',name:'Old'}],codex:[{id:'auto',name:'默认配置'}]},
  ai_model_catalogs:{workbuddy:{source:'builtin',count:2,confirmed:false}}};
const server=http.createServer(async(req,res)=>{
 const url=new URL(req.url,'http://localhost'),route=url.pathname;
 if(route.startsWith('/api/')){
  res.setHeader('Content-Type','application/json');
  if(route==='/api/settings'&&req.method==='PUT'){
   let body='';for await(const chunk of req)body+=chunk;saves.push(JSON.parse(body));return res.end('{}');
  }
  if(route==='/api/settings'){
   if(url.searchParams.get('refresh_models')==='1'){
    refreshCount++;if(hold)await new Promise(resolve=>release=resolve);
    return res.end(JSON.stringify({...settings,ai_models:{...settings.ai_models,workbuddy:[{id:'auto',name:'自动选择'},{id:'glm-5.3',name:'GLM-5.3'}]},ai_model_catalogs:{workbuddy:{source:'cli_desktop',count:2,confirmed:true}}}));
   }
   return res.end(JSON.stringify(settings));
  }
  if(route==='/api/ai/access')return res.end(JSON.stringify({codex:{available:false,logged_in:false,login:{status:'idle'}},api:{api_key_configured:false}}));
  return res.end('{}');
 }
 const file=path.join(root,'web',route==='/'?'index.html':route);
 if(!file.startsWith(path.join(root,'web')+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end();}
 res.setHeader('Content-Type',({'.html':'text/html','.css':'text/css','.js':'text/javascript'})[path.extname(file)]||'text/plain');res.end(fs.readFileSync(file));
});
async function run(){
 fs.mkdirSync(output,{recursive:true});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({headless:true,...(process.env.LIVECUT_BROWSER?{executablePath:process.env.LIVECUT_BROWSER}:process.platform==='win32'?{channel:'msedge'}:{})});
 const page=await browser.newPage({viewport:{width:1280,height:1000}});page.on('pageerror',e=>errors.push(e.message));
 const origin='http://127.0.0.1:'+server.address().port;
 await page.goto(origin+'/#/settings');
 await page.locator('#aiModel').waitFor();
 assert.equal(await page.locator('.settings-tabs a').first().textContent(),'AI 配置');
 assert.equal(await page.locator('.settings-tabs [aria-current="page"]').textContent(),'AI 配置');
 assert.equal(await page.locator('.settings-card').count(),4);
 assert.equal(await page.locator('[name="jev_base_url"]').isVisible(),false);
 await page.locator('[name="ai_engine"]').selectOption('jev');
 assert.equal(await page.locator('[name="jev_base_url"]').isVisible(),true);
 await page.locator('[name="ai_engine"]').selectOption('llm');
 await page.locator('#fallbackProvider').selectOption('codex');
 await page.locator('#fallbackModel').fill('gpt-fixture');await page.locator('#addFallback').click();
 await page.locator('#fallbackProvider').selectOption('workbuddy');
 await page.locator('#fallbackModel').fill('hy-fixture');await page.locator('#addFallback').click();
 await page.locator('#fallbackProvider').selectOption('api');
 await page.locator('#fallbackModel').fill('api-fixture');await page.locator('#addFallback').click();
 assert.equal(await page.locator('#fallbackList li').count(),3);
 await page.locator('#addFallback').click();assert.match(await page.locator('#fallbackError').textContent(),/已在列表/);
 await page.locator('[data-fallback-up="2"]').click();
 assert.deepEqual(await page.locator('.settings-fallback-model b').allTextContents(),['gpt-fixture','api-fixture','hy-fixture']);
 await page.locator('[data-fallback-down="0"]').click();
 await page.locator('[data-fallback-remove="2"]').click();
 assert.deepEqual(await page.locator('.settings-fallback-model b').allTextContents(),['api-fixture','gpt-fixture']);
 await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo(0,0)});
 await page.screenshot({path:path.join(output,'settings-desktop.png'),fullPage:true});
 await page.locator('#aiModel').waitFor();assert.equal(await page.locator('#aiModel').inputValue(),'saved-model');
 await page.locator('#refreshModels').click();await page.locator('#aiModel option[value="glm-5.3"]').waitFor({state:'attached'});
 assert.equal(await page.locator('#aiModel').inputValue(),'saved-model','Missing saved model must not silently reset');
 assert.match(await page.locator('#modelCatalogStatus').textContent(),/客户端目录/);
 await page.locator('#aiModel').selectOption('glm-5.3');await page.locator('#settingsForm button[type="submit"]').click();
 await page.waitForFunction(()=>document.querySelector('#toast').textContent==='设置已保存');
 assert.equal(saves.at(-1).ai_model,'glm-5.3');assert.equal(saves.at(-1).ai_provider,'workbuddy');
 assert.deepEqual(saves.at(-1).ai_fallbacks,[{provider:'api',model:'api-fixture'},{provider:'codex',model:'gpt-fixture'}]);
 hold=true;await page.locator('#refreshModels').click();await page.waitForFunction(()=>document.querySelector('#refreshModels').disabled);
 await page.locator('#aiProvider').selectOption('codex');release();hold=false;
 await page.waitForFunction(()=>!document.querySelector('#refreshModels').disabled);
 assert.equal(await page.locator('#aiModel').inputValue(),'auto');assert.equal(await page.locator('#modelRefreshRow').isVisible(),false);
 await page.locator('#aiProvider').selectOption('workbuddy');assert.equal(await page.locator('#modelRefreshRow').isVisible(),true);
 for(const width of [1280,390,320]){
  await page.setViewportSize({width,height:1000});await page.locator('#refreshModels').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Overflow '+width);
  if(width===1280||width===390)await page.screenshot({path:path.join(output,'models-'+width+'.png')});
 }
 await page.setViewportSize({width:1280,height:1000});
 await page.locator('#aiConnectionForm [name="model"]').fill('fixture-model');
 await page.locator('#aiConnectionForm [type="submit"]').click();
 await page.waitForFunction(()=>document.querySelector('#aiConnectionForm [name="model"]')?.value==='');
 await page.locator('.settings-tabs').waitFor();
 assert.equal(await page.locator('.settings-tabs').count(),1,'API save must preserve navigation');
 assert.equal(await page.locator('.settings-tabs [aria-current="page"]').textContent(),'AI 配置');
 await page.goto(origin+'/#/settings/runtime');await page.locator('[name="engine_python"]').waitFor();
 assert.equal(await page.locator('#aiProvider').count(),0,'Runtime form must contain only runtime fields');
 await page.locator('[name="engine_python"]').fill('fixture-python');
 await page.locator('#settingsForm button[type="submit"]').click();
 await page.waitForFunction(()=>document.querySelector('#toast').textContent==='设置已保存');
 assert.equal(saves.at(-1).engine_python,'fixture-python');
 assert.equal('ai_model' in saves.at(-1),false,'Runtime save must not overwrite AI selection');
 assert.equal('ai_fallbacks' in saves.at(-1),false,'Runtime save must not overwrite fallback list');
 assert.equal(typeof saves.at(-1).mcp_enabled,'boolean');
 assert.equal(refreshCount,2);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,refreshCount,savedModel:saves.find(save=>save.ai_model)?.ai_model,screenshots:output}));
}
run().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{release?.();server.closeAllConnections();server.close();await browser?.close()});
