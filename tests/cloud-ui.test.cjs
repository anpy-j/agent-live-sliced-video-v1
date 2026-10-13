const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '../web/cloud');
  const server = http.createServer((req, res) => {
    const name = req.url.split('?')[0] === '/' ? 'index.html' : req.url.split('?')[0].slice(1);
    if (!['index.html','cloud.css','cloud.js'].includes(name)) {res.writeHead(404).end();return;}
    res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html');
    res.end(fs.readFileSync(path.join(root,name)));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({headless:true,channel:process.env.LIVECUT_TEST_BROWSER || 'msedge'});
    const page = await browser.newPage({viewport:{width:390,height:844}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    let product=false,uploaded=false,ready=false,fail=true,edits=0;
    await page.route('https://upload.test/**',async route=>{
      if(fail){fail=false;await route.abort('failed');return;}
      uploaded=true;await route.fulfill({status:200,body:'{}',headers:{'access-control-allow-origin':'*'}});
    });
    await page.route('http://192.168.1.240:8088/api/livecut/v1/**',async route=>{
      const request=route.request(),url=new URL(request.url()),p=url.pathname.replace('/api/livecut/v1','');
      let data={},status=200;
      if(request.method()==='OPTIONS'){await route.fulfill({status:200,headers:{'access-control-allow-origin':'*','access-control-allow-headers':'*','access-control-allow-methods':'*'}});return;}
      if(p==='/auth/login')data={token:'test-token'};
      else if(p==='/products'&&request.method()==='POST'){product=true;data={id:'product-1'};}
      else if(p==='/products')data={products:product?[{id:'product-1',sku:'A001',name:'针织衫',edit_count:edits,video_count:ready?1:0}]:[]};
      else if(p==='/stats')data={edit_count:edits,video_count:ready?1:0,ready_count:ready?1:0,pending_count:0};
      else if(p==='/uploads')data={id:'video-1',status:ready?'ready':'pending',key:'video-1.mp4',token:'ticket',upload_url:'https://upload.test/'};
      else if(p.endsWith('/complete')){if(uploaded){ready=true;data={status:'ready'};}else{status=409;data={detail:'文件尚未上传'};}}
      else if(p==='/videos')data={videos:ready?[{id:'video-1',product_name:'针织衫',title:'手机上传',size:10,created:1,edited_on:null,status:'ready',usage:'unused',version:1,note:''}]:[],total:ready?1:0};
      else if(p==='/edit-records')data={records:[]};
      await route.fulfill({status,json:data,headers:{'access-control-allow-origin':'*'}});
    });
    const url=`http://127.0.0.1:${server.address().port}`;
    await page.goto(url);
    await page.locator('[name=server]').fill('http://192.168.1.240:8088');
    await page.locator('[name=username]').fill('test');await page.locator('[name=password]').fill('test');
    await page.getByRole('button',{name:'连接并登录'}).click();
    await page.locator('#workspace').waitFor({state:'visible'});
    await page.locator('[data-tab=products]').click();
    await page.locator('[name=sku]').fill('A001');await page.locator('[name=name]').fill('针织衫');
    await page.getByRole('button',{name:'添加',exact:true}).click();await page.getByRole('heading',{name:'针织衫'}).waitFor();
    await page.locator('[data-tab=upload]').click();
    await page.locator('[name=product_id]').selectOption('product-1');await page.locator('[name=title]').fill('手机上传');
    await page.locator('[name=files]').setInputFiles({name:'video.mp4',mimeType:'video/mp4',buffer:Buffer.from('fake-video')});
    await page.getByRole('button',{name:'加入上传队列'}).click();
    await page.getByText('等待重试',{exact:false}).first().waitFor();
    await page.reload();await page.locator('#workspace').waitFor({state:'visible'});
    await page.locator('[data-tab=queue]').click();await page.locator('[data-retry]').waitFor();
    await page.locator('[data-retry]').click();await page.getByText('队列为空',{exact:true}).waitFor();
    await page.locator('[data-tab=videos]').click();await page.getByRole('button',{name:'播放',exact:true}).waitFor();
    assert.equal(edits,0,'manual upload must not create an edit');assert.equal(ready,true);
    assert.deepEqual(errors,[]);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'mobile layout must fit');
    fs.mkdirSync(path.resolve(__dirname,'../build/cloud-qa'),{recursive:true});
    await page.screenshot({path:path.resolve(__dirname,'../build/cloud-qa/mobile.png'),fullPage:true});
    await page.setViewportSize({width:1280,height:900});
    await page.screenshot({path:path.resolve(__dirname,'../build/cloud-qa/desktop.png'),fullPage:true});
    console.log('Cloud UI: login, product, durable failed upload, retry, archive and mobile layout passed.');
  } finally {if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
