(async()=>{
  const originalHash=location.hash,params=new URLSearchParams(originalHash.slice(1)),token=params.get('access');
  history.replaceState(null,'',location.pathname);
  // Cross-site navigation omits Strict cookies. Once on this local page,
  // a same-origin request can prove an existing authenticated session.
  if(!token){if(['/','/connect','/files'].includes(location.pathname)){try{const r=await fetch('/api/instance',{cache:'no-store'});if(r.ok)location.replace(location.pathname+originalHash);}catch{}}return;}
  const message=document.getElementById('unlock-message');
  try{
    const r=await fetch('/auth/unlock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
    if(!r.ok)throw Error();
    location.replace('/');
  }catch{message.textContent='本机访问凭证已失效，请重新打开客户端。';}
})();
