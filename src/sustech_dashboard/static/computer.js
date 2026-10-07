/* Browser session authorizes a one-use handoff; Windows starts the client if needed. */
(async()=>{
  const saved=sessionStorage.getItem('campus-desktop-handoff'),direct=sessionStorage.getItem('campus-desktop-grant');if(!saved&&!direct)return;
  const pending=saved?JSON.parse(saved):null,message=node('p','正在后台连接当前电脑…','notice');message.id='computer-auto-message';message.setAttribute('role','status');document.querySelector('main').prepend(message);
  try {
    const instance=await campusJson('/api/instance');
    if(!instance.configured){message.textContent='校园账号绑定完成后将自动连接当前电脑。';return;}
    const grant=direct?JSON.parse(direct):await campusPost('/api/devices/grant',{kind:'connect'});
    if(!direct){
      const response=await fetch('/app/desktop/authorize',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':pending.csrf},body:JSON.stringify({key:pending.key,uri:grant.uri})});
      if(!response.ok)throw Error('自动连接暂未完成，可在打印或下载区点击连接电脑重试。');
    }
    for(let i=0;i<90;i++){
      await new Promise(resolve=>setTimeout(resolve,2000));
      const result=await campusJson('/api/devices/grants/'+grant.id);
      if(result.state==='connected'){sessionStorage.removeItem('campus-desktop-handoff');sessionStorage.removeItem('campus-desktop-grant');message.textContent='当前电脑已自动连接，打印和下载组件正在后台运行。';window.dispatchEvent(new Event('campus-computer-connected'));return;}
      if(result.state==='expired')break;
    }
    message.textContent='未收到本机组件响应。请运行最新版客户端后，在打印或下载区点击连接电脑重试。';
  }catch(e){message.textContent=e.message;}
})();
async function campusComputer(button, message, kind='connect', key=null) {
  button.disabled=true;
  message.hidden=false;
  message.textContent='正在唤起本机组件，请在浏览器提示中允许打开校园面板…';
  try {
    const grant=await campusPost('/api/devices/grant',{kind,key});
    const launch=node('a','再次打开本机组件');launch.href=grant.uri;
    const download=node('a','下载 / 更新客户端');download.href=location.origin+'/campus-updates/targets/windows-x86_64.zip';
    message.append(document.createTextNode(' '),launch,document.createTextNode(' · '),download);
    launch.click();
    for(let i=0;i<90;i++) {
      await new Promise(resolve=>setTimeout(resolve,2000));
      const result=await campusJson('/api/devices/grants/'+grant.id);
      if(['connected','opened','open_failed','expired'].includes(result.state)) {
        message.textContent={connected:'当前电脑已连接，打印和下载状态将自动刷新。',opened:'已在当前电脑打开。',open_failed:'电脑已连接，但文件未能打开；请核对文件是否仍存在及默认打开应用。',expired:'尚未收到组件响应。请下载最新版并运行一次，再点击连接；无需重新输入面板账号密码。'}[result.state];
        window.dispatchEvent(new Event('campus-computer-connected'));
        return;
      }
      if(i===14)message.prepend(document.createTextNode('若没有打开提示，请确认已下载并运行一次最新版客户端。'));
    }
  } catch(e) { message.textContent=e.message; }
  finally {button.disabled=false;}
}
