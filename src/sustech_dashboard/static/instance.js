(()=>{
  const form=document.getElementById('owner-login');
  async function post(path,data){const r=await fetch(apiBase+path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':roomCsrf},body:JSON.stringify(data||{})});const value=await r.json();if(!r.ok)throw Error(value.error||'请求失败');return value;}
  if(form)form.addEventListener('submit',async event=>{event.preventDefault();const button=form.querySelector('button'),message=document.getElementById('login-message');button.disabled=true;message.textContent='正在连接学校验证账号…';try{await post('/api/instance/login',{sid:form.elements.sid.value,password:form.elements.password.value,remember:form.elements.remember.checked,consent:form.elements.consent?.checked===true});form.elements.password.value='';location.replace(apiBase+'/');}catch(e){message.textContent=e.message;form.elements.password.value='';button.disabled=false;}});
  const disconnect=document.getElementById('disconnect-school');if(disconnect)disconnect.onclick=async()=>{disconnect.disabled=true;try{await post('/api/instance/disconnect');document.getElementById('disconnect-message').textContent='已断开学校账号，后台同步已停止，保存的 CAS 凭据已清除。';}catch(e){document.getElementById('disconnect-message').textContent=e.message;}finally{disconnect.disabled=false;}};
  const info=document.getElementById('instance-info');if(!info)return;
  const passwordForm=document.getElementById('panel-password');if(passwordForm)passwordForm.onsubmit=async event=>{event.preventDefault();const button=passwordForm.querySelector('button');button.disabled=true;try{const result=await post('/api/instance/password',{previous:passwordForm.elements.previous.value,password:passwordForm.elements.password.value});passwordForm.reset();location.replace(result.login_url);}catch(e){passwordForm.reset();document.getElementById('password-message').textContent=e.message;}finally{button.disabled=false;}};
  let installing=false,oldVersion=null;
  async function refresh(){try{const r=await fetch(apiBase+'/api/instance');if(!r.ok)throw Error();const d=await r.json();
    if(installing&&oldVersion&&d.version!==oldVersion){location.reload();return;}oldVersion=d.version;
    info.replaceChildren();for(const text of [`版本：${d.version} · ${d.mode}`,`账号：${d.configured?'已配置':'尚未登录'}`,`数据位置：${d.data_root}`,`附件：${d.download_mode==='paired'?'保存到已配对电脑':d.download_root}`]){const p=document.createElement('p');p.textContent=text;info.append(p);}
    const u=d.update;document.getElementById('update-state').textContent=d.execution_mode==='hosted'?'当前版本由维护者统一更新':u.message+(u.latest?' · 最新版本 '+u.latest:'')+(d.last_update.state==='rolled_back'?' · 上次更新启动失败，已恢复原版本':'');
    document.getElementById('update-install').disabled=!u.available||!d.managed||['downloading','ready'].includes(u.state);
  }catch{document.getElementById('update-state').textContent=installing?'服务正在重启，等待连接恢复…':'暂时无法连接本机服务';}}
  document.getElementById('update-check').onclick=async()=>{try{await post('/api/instance/updates/check');setTimeout(refresh,1000);}catch(e){document.getElementById('update-state').textContent=e.message;}};
  document.getElementById('update-install').onclick=async()=>{try{await post('/api/instance/updates/install');installing=true;document.getElementById('update-install').disabled=true;await refresh();}catch(e){document.getElementById('update-state').textContent=e.message;}};
  refresh();setInterval(refresh,3000);
})();
