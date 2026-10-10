(()=>{
  const open=document.getElementById('open-local'),status=document.getElementById('open-status');
  open.addEventListener('click',event=>{
    status.hidden=false;
    if(!/Windows/i.test(navigator.userAgent)){event.preventDefault();status.textContent='请在已安装组件的 Windows 电脑上打开，或使用自己的 Linux 运行端。';return;}
    status.textContent='已在新标签页打开本机地址。若无法连接或提示需要访问凭证，请从桌面或开始菜单打开“南科大校园面板”。';
  });
})();
