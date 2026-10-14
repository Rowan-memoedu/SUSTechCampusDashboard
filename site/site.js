(()=>{
  const open=document.getElementById('open-local'),status=document.getElementById('open-status');
  open.addEventListener('click',event=>{
    status.hidden=false;
    if(!/Windows/i.test(navigator.userAgent)){event.preventDefault();status.textContent='请在已安装组件的 Windows 电脑上打开，或使用自己的 Linux 运行端。';return;}
    status.textContent='请在浏览器提示中允许打开“南科大校园面板”。组件准备就绪后会自动打开页面；若没有响应，可从桌面或开始菜单打开同名应用。';
  });
})();
