(()=>{
 const p=new URLSearchParams(location.hash.slice(1)),kind=p.get('kind')||'root',key=p.get('key');
 history.replaceState(null,'',location.pathname);
 const button=document.getElementById('local-file-open'),message=document.getElementById('local-file-message');
 button.textContent={file:'打开文件',folder:'打开所在文件夹',root:'打开下载根目录'}[kind]||'打开';
 button.onclick=async()=>{button.disabled=true;try{await campusPost('/api/materials/open',{kind,key});message.textContent='已请求本机打开。';}catch(e){message.textContent=e.message;}finally{button.disabled=false;}};
})();
