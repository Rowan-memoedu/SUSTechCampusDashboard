(()=>{
  const panel=document.getElementById('update-panel');if(!panel)return;
  const state=document.getElementById('update-state'),install=document.getElementById('update-install'),check=document.getElementById('update-check'),notes=document.getElementById('update-notes');
  const storageKey='campus-update:'+apiBase;let data=null,busy=false;
  function readPending(){try{return JSON.parse(sessionStorage.getItem(storageKey)||'null');}catch{return null;}}
  let pending=readPending(),completed=sessionStorage.getItem(storageKey+':result');
  sessionStorage.removeItem(storageKey+':result');
  async function post(path){const r=await fetch(apiBase+path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':roomCsrf},body:'{}'});const d=await r.json();if(!r.ok)throw Object.assign(Error(d.error||'请求失败，请重试'),{rejected:true});return d;}
  function message(text,kind){state.textContent=text;panel.dataset.state=kind;panel.hidden=false;}
  async function refresh(){
    try{
      const r=await fetch(apiBase+'/api/instance',{cache:'no-store'});if(!r.ok)throw Error();data=await r.json();
      if(data.execution_mode==='hosted'){panel.hidden=true;return;}
      const u=data.update,last=data.last_update||{};
      if(pending&&data.version===pending.target){
        sessionStorage.setItem(storageKey+':result',data.version);sessionStorage.removeItem(storageKey);pending=null;location.reload();return;
      }
      if(pending&&last.state==='rolled_back'&&last.failed_version===pending.target){pending=null;sessionStorage.removeItem(storageKey);}
      if(pending&&u.state==='error'){pending=null;sessionStorage.removeItem(storageKey);}
      const rollingBack=last.state==='rolled_back'&&last.failed_version!==data.version&&(!u.latest||last.failed_version===u.latest);
      let text=u.message+(u.latest?' · '+u.latest:'');
      if(rollingBack)text='新版本启动失败，已恢复 '+data.version+'。个人数据保留，可重试更新。';
      else if(pending&&['idle','checking','current'].includes(u.state))text='正在恢复连接并确认新版本，请稍候…';
      else if(last.state==='installed'&&last.version===data.version&&u.state!=='available')text='当前版本 '+data.version+' 已启动。'+u.message;
      panel.hidden=Boolean(panel.dataset.compact&&!u.available&&!pending&&!rollingBack&&completed!==data.version&&u.state!=='error');
      state.textContent=text;panel.dataset.state=rollingBack||u.state==='error'?'error':pending?'loading':u.state==='current'?'success':'default';
      notes.textContent=u.notes||'';notes.hidden=!u.notes;
      const working=['downloading','ready','waiting','restarting'].includes(u.state);
      install.disabled=busy||Boolean(pending)||working||!u.available||!data.managed;
      check.disabled=busy||Boolean(pending)||working||u.state==='checking';
      install.textContent=pending||working?'正在安装…':'安装更新';
      panel.setAttribute('aria-busy',String(Boolean(pending)||working));
    }catch{
      if(pending){const long=Date.now()-pending.started>180000;message(long?'仍未连接到本机服务。页面会继续重试；也可从快捷方式重新打开面板。':'服务正在重启，等待重新连接…','loading');}
      else message('暂时无法连接本机服务。请从快捷方式重新打开面板。','error');
      install.disabled=true;
    }
  }
  check.onclick=async()=>{busy=true;check.disabled=true;try{await post('/api/instance/updates/check');message('正在校验版本签名…','loading');}catch(e){message(e.message,'error');}finally{busy=false;setTimeout(refresh,1000);}};
  install.onclick=async()=>{
    if(!data?.update.available||pending)return;busy=true;install.disabled=true;
    try{pending={target:data.update.latest,started:Date.now()};sessionStorage.setItem(storageKey,JSON.stringify(pending));await post('/api/instance/updates/install');message('正在下载并校验更新…','loading');}
    catch(e){if(e.rejected){pending=null;sessionStorage.removeItem(storageKey);message(e.message,'error');}else message('安装请求的回执尚未确认，正在等待本机服务恢复连接…','loading');}
    finally{busy=false;setTimeout(refresh,1000);}
  };
  refresh();setInterval(refresh,3000);
})();
