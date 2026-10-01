(async()=>{
  const params=new URLSearchParams(location.hash.slice(1)),token=params.get('access');
  history.replaceState(null,'',location.pathname);
  if(!token)return;
  const message=document.getElementById('unlock-message');
  try{
    const r=await fetch('/auth/unlock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
    if(!r.ok)throw Error();
    location.replace('/');
  }catch{message.textContent='本机访问凭证已失效，请重新打开客户端。';}
})();
