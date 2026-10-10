const {spawnSync}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const local=path.join(root,'.runtime','desktop-build',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const python=process.env.LIVECUT_BUILD_PYTHON||(fs.existsSync(local)?local:process.platform==='win32'?'python':'python3');
const result=spawnSync(python,[path.join(__dirname,'build_runtime.py')],{cwd:root,stdio:'inherit',windowsHide:true});
if(result.error)console.error(result.error.message);
process.exit(result.status??1);
