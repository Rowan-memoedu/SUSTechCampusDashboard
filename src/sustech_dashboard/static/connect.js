(()=>{
 const form=document.getElementById('computer-connect'),message=document.getElementById('connect-message');
 const url=new URLSearchParams(location.hash.slice(1)).get('server');
 if(url)form.elements.url.value=url;
 history.replaceState(null,'',location.pathname);
 form.onsubmit=async event=>{event.preventDefault();const button=form.querySelector('button');button.disabled=true;message.textContent='正在验证面板账号和所属空间…';try{const result=await campusPost('/api/instance/pair',{url:form.elements.url.value,username:form.elements.username.value,password:form.elements.password.value});message.textContent=result.message;}catch(e){message.textContent=e.message;}finally{form.elements.password.value='';button.disabled=false;}};
})();
