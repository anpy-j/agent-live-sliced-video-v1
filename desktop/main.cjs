const {app,BrowserWindow,ipcMain,dialog,session,Menu,shell}=require('electron');
const {spawn}=require('node:child_process');
const path=require('node:path'),fs=require('node:fs'),crypto=require('node:crypto');
let backend,window,origin='',workspace='',quitting=false;
const configPath=()=>path.join(app.getPath('userData'),'desktop.json');
const token=crypto.randomBytes(32).toString('hex');
if(process.env.LIVECUT_USER_DATA)app.setPath('userData',path.resolve(process.env.LIVECUT_USER_DATA));
if(!app.requestSingleInstanceLock())app.quit();
app.on('second-instance',()=>{window?.show();window?.focus();});

function startBackend(){
  let config={};try{config=JSON.parse(fs.readFileSync(configPath(),'utf8'));}catch{}
  workspace=process.env.LIVECUT_WORKSPACE||config.workspace||path.join(app.getPath('userData'),'workspace');fs.mkdirSync(workspace,{recursive:true});
  const source=path.resolve(__dirname,'..');
  const runtime=path.join(process.resourcesPath,'runtime');
  const localPython=path.join(source,'.runtime','desktop-build',process.platform==='win32'?'Scripts/python.exe':'bin/python');
  const executable=app.isPackaged?path.join(runtime,'livecut-backend',process.platform==='win32'?'livecut-backend.exe':'livecut-backend'):(process.env.LIVECUT_PYTHON||(fs.existsSync(localPython)?localPython:process.platform==='win32'?'python':'python3'));
  const args=app.isPackaged?[]:['-m','agent_video'];
  const port=Number(process.env.LIVECUT_DESKTOP_PORT??config.port??0);
  if(!Number.isInteger(port)||port<0||port>65535)throw new Error('本地服务端口需要在 0–65535 之间');
  args.push('--root',workspace,'--port',String(port));
  const bin=app.isPackaged?path.join(runtime,'bin'):'';
  const assets=app.isPackaged?path.join(runtime,'livecut-backend','_internal'):source;
  const skill=path.join(workspace,'integrations','skill','SKILL.md');
  if(!fs.existsSync(skill)){fs.mkdirSync(path.dirname(skill),{recursive:true});fs.copyFileSync(path.join(assets,'integrations','skill','SKILL.md'),skill);}
  const env={...process.env,PYTHONIOENCODING:'utf-8',PYTHONUNBUFFERED:'1',LIVECUT_DESKTOP_TOKEN:token,
    LIVECUT_ASSET_ROOT:assets,
    PATH:[bin,process.env.PATH||'',process.platform==='darwin'?'/opt/homebrew/bin:/usr/local/bin':''].filter(Boolean).join(path.delimiter)};
  const log=fs.createWriteStream(path.join(workspace,'desktop-backend.log'),{flags:'a'});
  return new Promise((resolve,reject)=>{
    let buffer='',ready=false;
    backend=spawn(executable,args,{cwd:app.isPackaged?workspace:source,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    const timeout=setTimeout(()=>{reject(new Error('本地服务启动超时，查看 desktop-backend.log'));backend.kill();},60000);
    backend.stdout.on('data',data=>{log.write(data);buffer+=data.toString();let index;while((index=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,index).trim();buffer=buffer.slice(index+1);if(line.startsWith('LIVECUT_READY ')){clearTimeout(timeout);ready=true;const actualPort=JSON.parse(line.slice(14)).port;origin='http://127.0.0.1:'+actualPort;fs.mkdirSync(app.getPath('userData'),{recursive:true});fs.writeFileSync(configPath(),JSON.stringify({...config,port:actualPort}));resolve();}}});
    backend.stderr.on('data',data=>log.write(data));
    backend.on('error',err=>{clearTimeout(timeout);reject(err);});
    backend.on('exit',code=>{clearTimeout(timeout);log.end();if(!ready)reject(new Error(`本地服务退出（${code}），查看 desktop-backend.log`));else if(!quitting){dialog.showErrorBox('LiveCut 服务停止','本地服务意外停止，请重启应用。');app.quit();}});
  });
}
function trusted(event){if(!event.senderFrame||new URL(event.senderFrame.url).origin!==origin)throw new Error('不允许的桌面请求');}
app.whenReady().then(async()=>{
  await startBackend();
  session.defaultSession.webRequest.onBeforeSendHeaders((details,callback)=>{
    if(details.url.startsWith(origin+'/'))details.requestHeaders['X-LiveCut-Desktop']=token;
    callback({requestHeaders:details.requestHeaders});
  });
  session.defaultSession.setPermissionRequestHandler((_webContents,permission,callback)=>callback(permission==='clipboard-sanitized-write'));
  ipcMain.handle('livecut:info',event=>{trusted(event);return {version:app.getVersion(),platform:process.platform,workspace,origin};});
  ipcMain.handle('livecut:files',async event=>{trusted(event);const result=await dialog.showOpenDialog(window,{properties:['openFile','multiSelections'],filters:[{name:'视频、音频与图片',extensions:['mp4','mov','mkv','webm','avi','wav','mp3','m4a','flac','aac','ogg','png','jpg','jpeg','webp','gif']}]});return result.filePaths;});
  ipcMain.handle('livecut:directory',async event=>{trusted(event);const result=await dialog.showOpenDialog(window,{properties:['openDirectory','createDirectory']});return {cancelled:result.canceled,path:result.filePaths[0]};});
  ipcMain.handle('livecut:workspace',async event=>{trusted(event);const result=await dialog.showOpenDialog(window,{title:'选择工作目录（随后重启 LiveCut）',properties:['openDirectory','createDirectory']});if(result.canceled)return false;let config={};try{config=JSON.parse(fs.readFileSync(configPath(),'utf8'));}catch{}fs.mkdirSync(app.getPath('userData'),{recursive:true});fs.writeFileSync(configPath(),JSON.stringify({...config,workspace:result.filePaths[0]}));app.relaunch();app.quit();return true;});
  ipcMain.handle('livecut:reveal',(event,file)=>{trusted(event);if(typeof file!=='string'||!path.isAbsolute(file)||!fs.existsSync(file))throw new Error('文件不存在');shell.showItemInFolder(file);});
  window=new BrowserWindow({width:1500,height:1000,minWidth:1000,minHeight:700,show:!(process.env.LIVECUT_SMOKE||process.env.LIVECUT_TEST_WINDOW),
    title:'LiveCut',backgroundColor:'#101722',webPreferences:{preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
  window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  window.webContents.on('will-navigate',(event,url)=>{if(new URL(url).origin!==origin)event.preventDefault();});
  const template=[...(process.platform==='darwin'?[{role:'appMenu'}]:[]),{label:'编辑',submenu:[{role:'undo'},{role:'redo'},{role:'cut'},{role:'copy'},{role:'paste'},{role:'selectAll'}]},{label:'视图',submenu:[{role:'reload'},{role:'togglefullscreen'}]}];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
  const errors=[];window.webContents.on('console-message',(_e,level,message)=>{if(level>=3)errors.push(message);});
  await window.loadURL(origin+'/#/editor');
  if(process.env.LIVECUT_SMOKE){
    const result=await window.webContents.executeJavaScript(`Promise.all([fetch('/api/health').then(r=>r.json()),window.livecutDesktop.info(),fetch('/api/editor/projects').then(r=>r.json())]).then(([health,desktop,editor])=>({health,desktop,editor,editorLoaded:!!window.LiveCutEditor}))`);
    fs.writeFileSync(process.env.LIVECUT_SMOKE,JSON.stringify({...result,errors},null,2));app.quit();
  }
}).catch(err=>{dialog.showErrorBox('LiveCut 启动失败',err.message);app.quit();});
app.on('window-all-closed',()=>app.quit());
app.on('before-quit',event=>{
  if(quitting||!backend||backend.exitCode!==null)return;event.preventDefault();quitting=true;
  const timeout=setTimeout(()=>{backend.kill();app.exit();},12000);
  fetch(origin+'/api/desktop/shutdown',{method:'POST',headers:{'Content-Type':'application/json','X-LiveCut-Desktop':token},body:'{}'}).catch(()=>backend.kill());
  backend.once('exit',()=>{clearTimeout(timeout);app.exit();});
});
