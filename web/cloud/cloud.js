/* Shared desktop / Android cloud module. IndexedDB retains queued files across restarts. */
(() => {
  const $ = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const device = localStorage.livecutDevice || (localStorage.livecutDevice = crypto.randomUUID());
  const desktop = new URLSearchParams(location.search).has('desktop');
  let token = sessionStorage.livecutToken || '', server = localStorage.livecutServer || '', username = localStorage.livecutUsername || '';
  let products = [], tab = 'videos', filter = '', day = '', offset = 0, running = false, catalog = [];
  let shown = new Map(), activeQueue = '';
  const accountKey = () => `${server}|${username}`;
  let messageTimer;
  const say = msg => { clearTimeout(messageTimer);$('#message').textContent = msg;if(!msg.includes('\n'))messageTimer=setTimeout(()=>{$('#message').textContent='';},8000); };
  const formatDate = value => value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('zh-CN') : '未知';
  const dateCN = value => new Intl.DateTimeFormat('sv-SE', { timeZone:'Asia/Shanghai', year:'numeric',month:'2-digit',day:'2-digit' }).format(new Date(value));
  const bytes = n => `${(n/1048576).toFixed(1)} MB`;
  const options = () => products.map(p => `<option value="${esc(p.id)}">${esc(p.sku)} · ${esc(p.name)}</option>`).join('');
  const database = new Promise((resolve,reject) => {
    const request = indexedDB.open('livecut-cloud',1);
    request.onupgradeneeded = () => request.result.createObjectStore('queue',{keyPath:'id'});
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  async function queueStore(action,value) {
    const db = await database;
    return new Promise((resolve,reject) => {
      const transaction = db.transaction('queue',action==='getAll'?'readonly':'readwrite');
      const request = transaction.objectStore('queue')[action](value);
      transaction.oncomplete = () => resolve(request.result);
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error || new Error('本地空间不足，无法保存上传任务'));
    });
  }
  async function api(path,body,method) {
    const response = await fetch(`${server}/api/livecut/v1${path}`, {method:method || (body?'POST':'GET'),
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${token}`},body:body?JSON.stringify(body):undefined});
    const data = await response.json().catch(()=>({detail:'服务器未返回有效 JSON，请检查地址'}));
    if (!response.ok) {
      if(response.status===401 && path!=='/auth/login') {token='';sessionStorage.removeItem('livecutToken');showLogin();}
      const error = new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));error.status=response.status;throw error;
    }
    return data;
  }
  function showLogin() {
    $('#loginPanel').hidden=false;$('#workspace').hidden=true;
    $('#loginForm [name=server]').value=server;$('#loginForm [name=username]').value=username;
  }
  async function refresh() {
    products=(await api('/products')).products;
    const stats=await api('/stats');
    $('#stats').innerHTML=[['今日剪辑',stats.edit_count],['今日成片',stats.video_count],['云端成片',stats.ready_count],['待归档',stats.pending_count]].map(([label,value])=>`<div><small>${label}</small><strong>${value}</strong></div>`).join('');
    $('#loginPanel').hidden=true;$('#workspace').hidden=false;
    await render();
  }
  async function render() {
    $('#tabs').querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));
    const content=$('#content');
    if(tab==='products') {
      content.innerHTML=`<div class="panel"><h2>添加商品</h2><form id="productForm"><label>款号<input name="sku" required maxlength="100"></label><label>名称<input name="name" required maxlength="160"></label><button class="primary">添加</button></form></div><div class="grid" style="margin-top:20px">${products.map(p=>`<article class="card"><h3>${esc(p.name)}</h3><span class="tag">${esc(p.sku)}</span><p>剪辑 ${p.edit_count} 次 · 云端成片 ${p.video_count} 条</p><button data-product="${esc(p.id)}">查看成片</button></article>`).join('')}</div>`;
      $('#productForm').onsubmit=async e=>{e.preventDefault();const values=Object.fromEntries(new FormData(e.target));await act(async()=>{await api('/products',values);await refresh();say('商品已添加');});};
      content.querySelectorAll('[data-product]').forEach(b=>b.onclick=()=>{filter=b.dataset.product;tab='videos';offset=0;act(render);});return;
    }
    if(tab==='upload') {
      content.innerHTML=`<div class="panel"><h2>上传成片</h2><p>选择商品和视频。手机上传已有视频不会自动增加剪辑次数；剪辑日期可留空。</p><form id="uploadForm"><label>商品<select name="product_id" required><option value="">请选择商品</option>${options()}</select></label><label>标题<input name="title" required maxlength="200"></label><label>实际剪辑日期<input name="edited_on" type="date"></label><label>视频（单个最大 1 GB，可多选）<input name="files" type="file" accept="video/*,.mkv,.avi" multiple required></label><button class="primary">加入上传队列</button></form></div>${desktop?'<section class="panel" style="margin-top:20px"><h2>本机剪辑成片</h2><p>选择商品后，可以登记剪辑记录或批量上传。没有完成时间的任务采用文件修改时间，请核对。</p><button id="loadCatalog">读取本机成片</button><div id="catalog"></div></section>':''}`;
      $('#uploadForm').onsubmit=e=>{e.preventDefault();act(async()=>{
        const form=e.target,product=form.product_id.value,title=form.title.value,edited=form.edited_on.value;
        if(!form.files.files.length)throw new Error('请选择视频');
        for(const file of form.files.files)await enqueue(file,{product_id:product,title:form.files.files.length>1?`${title} · ${file.name}`.slice(0,200):title,edited_on:edited||null});
        tab='queue';await render();say('已保存到本地队列；保持此页面打开即可上传');processQueue();
      });};
      if(desktop)$('#loadCatalog').onclick=()=>act(loadCatalog);
      return;
    }
    if(tab==='queue') {
      const rows=(await queueStore('getAll')).filter(r=>r.account===accountKey());
      content.innerHTML=`<div class="actions"><button id="retryQueue">重试全部</button></div><p>任务和文件保存在当前设备，断网后可重试。上传时请保持云端页面打开；重新进入后会继续处理。失败文件从头重传，已上传文件只补归档。</p>${rows.map(r=>`<article class="card" style="margin-bottom:12px"><h3>${esc(r.payload.title)}</h3><p>${esc(r.state||'等待上传')} ${r.error?' · '+esc(r.error):''}</p><progress max="100" value="${r.progress||0}" id="progress-${r.id}"></progress><div class="actions"><button data-retry="${r.id}" ${r.id===activeQueue?'disabled':''}>重试</button><button data-remove="${r.id}" ${r.id===activeQueue?'disabled':''}>移除本地任务</button></div></article>`).join('') || '<div class="empty">队列为空</div>'}`;
      $('#retryQueue').onclick=()=>act(async()=>{for(const r of rows){r.error='';await queueStore('put',r);}processQueue();});
      content.querySelectorAll('[data-retry]').forEach(b=>b.onclick=()=>act(async()=>{const r=rows.find(r=>r.id===b.dataset.retry);r.error='';await queueStore('put',r);processQueue();}));
      content.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>act(async()=>{if(b.dataset.remove===activeQueue)return;await queueStore('delete',b.dataset.remove);await render();}));return;
    }
    const filters=`<div class="filters"><select id="productFilter"><option value="">全部商品</option>${options()}</select><input id="dayFilter" type="date" value="${esc(day)}"><button id="refresh">刷新</button></div>`;
    const params=new URLSearchParams({product_id:filter,offset:String(offset)});if(day)params.set('day',day);
    if(tab==='records') {
      const data=await api(`/edit-records?${params}`);
      content.innerHTML=filters+data.records.map(r=>`<article class="row"><b>${esc(r.title)}</b><p>${esc(r.product_name)} · ${esc(r.workflow)} · ${formatDate(r.completed_at)}</p><small>设备：${esc(r.device_id)}</small></article>`).join('')+`<div class="actions"><button id="previous">上一页</button><button id="next" ${data.records.length<100?'disabled':''}>下一页</button></div>`;
    } else {
      const data=await api(`/videos?${params}`);shown=new Map(data.videos.map(v=>[v.id,v]));
      content.innerHTML=filters+`<p>共 ${data.total} 条 · 日期筛选依据实际剪辑日期</p><div class="grid">${data.videos.map(v=>`<article class="card"><h3>${esc(v.title)}</h3><span class="tag">${esc(v.product_name)}</span><p>${bytes(v.size)} · 剪辑日期 ${esc(v.edited_on||'未知')}<br>上传 ${formatDate(v.created)}</p>${v.status==='ready'?`<div class="actions"><button data-play="${v.id}">播放</button><button data-download="${v.id}">下载</button></div>`:`<p>等待上传或归档</p><button data-complete="${v.id}">核验并补归档</button>`}<label>使用状态<select data-usage="${v.id}"><option value="unused">待使用</option><option value="used">已使用</option><option value="published">已发布</option></select></label><textarea data-note="${v.id}" maxlength="2000" placeholder="备注">${esc(v.note)}</textarea><div class="actions"><button data-save="${v.id}">保存记录</button><button data-log="${v.id}">下载记录</button></div></article>`).join('')}</div><div class="actions"><button id="previous">上一页</button><button id="next" ${offset+30>=data.total?'disabled':''}>下一页</button></div>`;
      data.videos.forEach(v=>content.querySelector(`[data-usage="${v.id}"]`).value=v.usage);
      content.querySelectorAll('[data-play]').forEach(b=>b.onclick=()=>act(async()=>{const data=await api(`/videos/${b.dataset.play}/access`);$('#preview video').src=data.url;$('#preview').showModal();}));
      content.querySelectorAll('[data-download]').forEach(b=>b.onclick=()=>act(()=>download(b.dataset.download)));
      content.querySelectorAll('[data-complete]').forEach(b=>b.onclick=()=>act(async()=>{await api(`/uploads/${b.dataset.complete}/complete`,{});await refresh();}));
      content.querySelectorAll('[data-save]').forEach(b=>b.onclick=()=>act(async()=>{const v=shown.get(b.dataset.save);await api(`/videos/${v.id}`,{version:v.version,note:content.querySelector(`[data-note="${v.id}"]`).value,usage:content.querySelector(`[data-usage="${v.id}"]`).value},'PATCH');await render();say('记录已保存');}));
      content.querySelectorAll('[data-log]').forEach(b=>b.onclick=()=>act(async()=>{const data=await api(`/videos/${b.dataset.log}/activities`);say(data.activities.map(a=>`${formatDate(a.created)} · ${{download_requested:'申请下载',download_completed:'客户端确认保存成功',download_failed:'保存失败'}[a.kind]}`).join('\n')||'暂无下载记录');}));
    }
    $('#productFilter').value=filter;
    $('#productFilter').onchange=e=>{filter=e.target.value;offset=0;act(render);};$('#dayFilter').onchange=e=>{day=e.target.value;offset=0;act(render);};$('#refresh').onclick=()=>act(refresh);
    $('#previous').disabled=offset===0;$('#previous').onclick=()=>{offset=Math.max(0,offset-(tab==='records'?100:30));act(render);};$('#next').onclick=()=>{offset+=tab==='records'?100:30;act(render);};
  }
  async function loadCatalog() {
    const response=await fetch('/api/cloud/catalog');if(!response.ok)throw new Error('本机成片读取失败');const data=await response.json();catalog=data.items;
    $('#catalog').innerHTML=`<label>关联商品<select id="catalogProduct">${options()}</select></label>${catalog.map((r,i)=>`<div class="row"><label><input type="checkbox" data-catalog="${i}"><span>${esc(r.title)} · ${esc(r.workflow)}<br><small>${esc(r.filename)} · ${bytes(r.size)} · ${formatDate(r.completed_at)}${r.time_source==='file'?'（文件修改时间）':''}</small></span></label></div>`).join('')}<div class="actions"><button id="syncRecords">登记选中剪辑</button><button id="uploadCatalog">上传选中成片</button></div>${data.errors.length?`<p>${esc(data.errors.map(e=>`${e.workflow}: ${e.error}`).join('; '))}</p>`:''}`;
    async function addSelected(uploadFiles) {
      const product=$('#catalogProduct').value;if(!product)throw new Error('先添加并选择商品');
      const selected=[...document.querySelectorAll('[data-catalog]:checked')].map(b=>catalog[Number(b.dataset.catalog)]);if(!selected.length)throw new Error('请选择成片');
      for(const r of selected) {
        const edit={product_id:product,device_id:device,workflow:r.workflow,local_id:r.local_id,title:r.title,completed_at:r.completed_at};
        if(uploadFiles){
          if(r.size>1024**3)throw new Error('单个视频最大 1 GB');
          const response=await fetch(r.content_url);if(!response.ok)throw new Error('成片已移动或读取失败');
          const blob=await response.blob();await enqueue(new File([blob],r.filename,{type:blob.type}),{product_id:product,title:r.title,edited_on:dateCN(r.completed_at)},edit);
        }else await queueStore('put',{id:crypto.randomUUID(),account:accountKey(),kind:'record',edit,payload:{title:r.title},state:'等待同步'});
      }
      tab='queue';await render();say('已加入队列；同一剪辑任务登记多次只计一次');processQueue();
    }
    $('#syncRecords').onclick=()=>act(()=>addSelected(false));$('#uploadCatalog').onclick=()=>act(()=>addSelected(true));
  }
  async function enqueue(file,payload,edit=null) {
    if(!file.size||file.size>1024**3)throw new Error('文件大小须为 1 B–1 GB');
    if(!/\.(mp4|mov|webm|mkv|avi|m4v)$/i.test(file.name))throw new Error('请选择支持的视频格式');
    const id=crypto.randomUUID();await queueStore('put',{id,account:accountKey(),file,edit,payload:{...payload,request_id:id,filename:file.name,size:file.size},state:'等待上传'});
  }
  function transfer(ticket,file,id) {
    return new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();xhr.open('POST',ticket.upload_url);xhr.timeout=30*60*1000;
      xhr.upload.onprogress=e=>{if(e.lengthComputable){const p=$(`#progress-${id}`);if(p)p.value=Math.round(e.loaded/e.total*100);}};
      xhr.onload=()=>xhr.status>=200&&xhr.status<300?resolve():reject(new Error(`七牛上传失败 ${xhr.status}，可重试`));
      xhr.onerror=()=>reject(new Error('上传连接中断，可重试'));xhr.ontimeout=()=>reject(new Error('上传超时，可重试'));
      const form=new FormData();form.append('token',ticket.token);form.append('key',ticket.key);form.append('file',file,file.name);xhr.send(form);
    });
  }
  async function processQueue() {
    if(running||!token||!navigator.onLine)return;running=true;
    try {
      const rows=(await queueStore('getAll')).filter(r=>r.account===accountKey()&&!r.error);
      for(const row of rows) {
        if(!token)break;activeQueue=row.id;
        try {
          row.state=row.kind==='record'?'同步剪辑记录':'上传中';await queueStore('put',row);if(tab==='queue')await render();
          if(row.edit&&!row.payload.edit_id){const edit=await api('/edit-records',row.edit);row.payload.edit_id=edit.id;await queueStore('put',row);}
          if(row.kind!=='record') {
            let ticket=await api('/uploads',row.payload);row.remote_id=ticket.id;await queueStore('put',row);
            if(ticket.status!=='ready') {
              let uploaded=false;try{await api(`/uploads/${ticket.id}/complete`,{});uploaded=true;}catch(e){if(e.status!==409)throw e;}
              if(!uploaded){await transfer(ticket,row.file,row.id);await api(`/uploads/${ticket.id}/complete`,{});}
            }
          }
          await queueStore('delete',row.id);say('上传 / 同步完成');
        }catch(e){row.state='等待重试';row.error=e.message;await queueStore('put',row);say(e.message);}
        finally{activeQueue='';}
      }
      if(rows.length&&token)await refresh();
    }catch(e){say(e.message);}finally{running=false;}
  }
  async function download(id) {
    const native=window.livecutNative?.isNative();
    const data=await api(`/videos/${id}/access?download=true`,{});
    if(native) {
      let savedPath;
      try {
        const name=data.filename.replace(/[\\/:*?"<>|]/g,'_');
        savedPath=await window.livecutNative.download(data.url,`livecut-${id}-${name}`);
      }catch(e){await api(`/videos/${id}/activities`,{kind:'download_failed',device_id:device}).catch(()=>{});throw e;}
      await api(`/videos/${id}/activities`,{kind:'download_completed',device_id:device}).catch(()=>{});
      await window.livecutNative.share(savedPath);say('视频已下载，选择保存位置或分享应用');
    }else {
      const a=document.createElement('a');a.href=data.url;a.target='_blank';a.rel='noopener';a.download=data.filename;document.body.append(a);a.click();a.remove();say('已请求下载，保存结果请在浏览器或系统下载列表中查看');
    }
  }
  async function act(fn){try{await fn();}catch(e){say(e.message);}}
  $('#loginForm').onsubmit=e=>{e.preventDefault();act(async()=>{
    if(running)throw new Error('请等待当前上传完成后再切换账号');
    const form=e.target;const url=new URL(form.server.value.trim());
    if(url.protocol!=='https:'&&!['localhost','127.0.0.1'].includes(url.hostname))throw new Error('服务器地址须使用 HTTPS；本机测试可用 HTTP');
    if(url.username||url.password||url.search||url.hash)throw new Error('请输入不包含账号、查询参数的服务器地址');
    server=url.href.replace(/\/$/,'');username=form.username.value;
    const data=await api('/auth/login',{username,password:form.password.value});token=data.token;form.password.value='';sessionStorage.livecutToken=token;
    localStorage.livecutServer=server;localStorage.livecutUsername=username;await refresh();say('已连接');processQueue();
  });};
  $('#settingsButton').onclick=()=>act(async()=>{if(running)throw new Error('请等待上传完成');if(token)await api('/auth/logout',{}).catch(()=>{});token='';sessionStorage.removeItem('livecutToken');showLogin();});
  $('#tabs').querySelectorAll('button').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;offset=0;act(render);});
  $('#closePreview').onclick=()=>$('#preview').close();$('#preview').addEventListener('close',()=>{$('#preview video').pause();$('#preview video').removeAttribute('src');});
  addEventListener('online',processQueue);
  showLogin();if(token&&server)act(async()=>{await refresh();processQueue();});
})();
