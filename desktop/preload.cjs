const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('livecutDesktop',Object.freeze({
  info:()=>ipcRenderer.invoke('livecut:info'),
  pickFiles:()=>ipcRenderer.invoke('livecut:files'),
  pickDirectory:()=>ipcRenderer.invoke('livecut:directory'),
  changeWorkspace:()=>ipcRenderer.invoke('livecut:workspace'),
  reveal:file=>ipcRenderer.invoke('livecut:reveal',file)
}));
