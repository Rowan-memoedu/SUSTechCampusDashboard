(()=>{
  const open=document.getElementById('open-local'),status=document.getElementById('open-status');
  open.addEventListener('click',event=>{
    status.hidden=false;
    if(!/Windows/i.test(navigator.userAgent)){event.preventDefault();status.textContent='请在已安装组件的 Windows 电脑上打开，或使用自己的 Linux 运行端。';return;}
    status.textContent='请在浏览器提示中允许打开校园面板。若没有反应，可从开始菜单打开，或先下载安装。';
  });
})();
