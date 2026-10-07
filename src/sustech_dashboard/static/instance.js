(()=>{
  async function post(path,data){const r=await fetch(apiBase+path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':roomCsrf},body:JSON.stringify(data||{})});const value=await r.json();if(!r.ok)throw Error(value.error||'请求失败');return value;}
  const form=document.getElementById('owner-login');
  if(form)form.addEventListener('submit',async event=>{
    event.preventDefault();const button=form.querySelector('button'),message=document.getElementById('login-message');button.disabled=true;message.textContent='正在连接学校验证账号…';
    try{await post('/api/instance/login',{sid:form.elements.sid.value,password:form.elements.password.value,remember:form.elements.remember.checked,consent:form.elements.consent?.checked===true});form.elements.password.value='';location.replace(apiBase+'/');}
    catch(e){message.textContent=e.message;form.elements.password.value='';button.disabled=false;}
  });
  const disconnect=document.getElementById('disconnect-school');if(disconnect)disconnect.onclick=async()=>{disconnect.disabled=true;try{await post('/api/instance/disconnect');document.getElementById('disconnect-message').textContent='已断开学校账号，后台同步已停止。';}catch(e){document.getElementById('disconnect-message').textContent=e.message;}finally{disconnect.disabled=false;}};
  const info=document.getElementById('instance-info'),preferences=document.getElementById('preferences');
  if(info)fetch(apiBase+'/api/instance').then(r=>r.json()).then(d=>{
    info.replaceChildren();for(const text of ['版本：'+d.version+' · '+d.mode,'账号：'+(d.configured?'已配置':'尚未登录'),'数据位置：'+d.data_root,'附件目录：'+d.download_root]){const p=document.createElement('p');p.textContent=text;info.append(p);}
    if(preferences){preferences.elements.download_root.value=d.preferences.download_root;preferences.elements.autostart.checked=d.preferences.autostart;preferences.elements.autostart.disabled=!d.preferences.autostart_supported;}
    if(d.execution_mode!=='local'&&document.getElementById('stop-instance'))document.getElementById('stop-instance').hidden=true;
  }).catch(()=>{info.textContent='无法读取实例信息，请重新打开面板。';});
  if(preferences)preferences.onsubmit=async e=>{
    e.preventDefault();const button=preferences.querySelector('button'),message=document.getElementById('preferences-message');button.disabled=true;
    try{const d=await post('/api/instance/preferences',{download_root:preferences.elements.download_root.value,autostart:preferences.elements.autostart.disabled?null:preferences.elements.autostart.checked});message.textContent=d.message;}catch(error){message.textContent=error.message;}finally{button.disabled=false;}
  };
  const stop=document.getElementById('stop-instance');if(stop)stop.onclick=async()=>{stop.disabled=true;try{await post('/api/instance/stop');document.getElementById('stop-message').textContent='正在等待当前操作结束后退出；可关闭此页面。';}catch(e){document.getElementById('stop-message').textContent=e.message;stop.disabled=false;}};
})();
