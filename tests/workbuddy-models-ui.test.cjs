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
 browser=await chromium.launch({headless:true,...(process.env.LIVECUT_BROWSER?{executablePath:process.env.LIVECUT_BROWSER}:{})});
 const page=await browser.newPage({viewport:{width:1280,height:1000}});page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port+'/#/settings');
 await page.locator('#aiModel').waitFor();assert.equal(await page.locator('#aiModel').inputValue(),'saved-model');
 await page.locator('#refreshModels').click();await page.locator('#aiModel option[value="glm-5.3"]').waitFor({state:'attached'});
 assert.equal(await page.locator('#aiModel').inputValue(),'saved-model','Missing saved model must not silently reset');
 assert.match(await page.locator('#modelCatalogStatus').textContent(),/客户端目录/);
 await page.locator('#aiModel').selectOption('glm-5.3');await page.locator('#settingsForm button[type="submit"]').click();
 await page.waitForFunction(()=>document.querySelector('#toast').textContent==='设置已保存');
 assert.equal(saves.at(-1).ai_model,'glm-5.3');assert.equal(saves.at(-1).ai_provider,'workbuddy');
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
 assert.equal(refreshCount,2);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,refreshCount,savedModel:saves.at(-1).ai_model,screenshots:output}));
}
run().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{release?.();server.closeAllConnections();server.close();await browser?.close()});
