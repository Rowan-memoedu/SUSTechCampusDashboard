(() => {
  let data=null, fetching=false;
  const openCourses=new Set();
  const saved=s=>['saved','existing'].includes(s);
  const stateText={not_downloaded:'未下载',queued:'等待本机下载',running:'下载中',saved:'已保存',existing:'已保存',failed:'下载失败',missing:'本机文件已移除'};
  const jobText={queued:'待下载',running:'进行中',completed:'已完成',partial:'部分失败',failed:'失败',cancelled:'已暂停'};
  function size(n){if(!n)return '大小待核验';return n>=1048576?`${(n/1048576).toFixed(1)} MB`:`${Math.ceil(n/1024)} KB`;}
  function message(text){$('material-message').textContent=text;$('material-message').hidden=!text;}
  async function post(path,payload){
    const response=await campusFetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':roomCsrf},body:JSON.stringify(payload)});
    const result=await response.json();if(!response.ok)throw Error(result.error||`请求失败 (${response.status})`);return result;
  }
  async function requestDownload(payload,button){
    button.disabled=true;message('');
    try {await post('/api/materials/jobs',payload);message(data?.agent?.online?'下载请求已提交，正在等待保存；请查看下方进度。':'下载请求已保留。下载客户端恢复运行后会继续。');await refresh();}
    catch(e){message(e.message);}finally{button.disabled=false;}
  }
  function render(){
    if(!data)return;
    const agent=data.agent||{}, badge=$('material-agent');badge.textContent=agent.online?'本机下载代理在线':'本机下载代理离线';badge.className=`pill ${agent.online?'':'unknown'}`;
    $('material-auto').checked=!!data.auto_enabled;
    $('material-download-all').disabled=!(data.items||[]).length;
    $('material-refresh').disabled=!!data.scan?.running;
    $('material-refresh').textContent=data.scan?.running?'正在扫描…':'刷新附件清单';
    const total=(data.items||[]).length, done=(data.items||[]).filter(i=>saved(i.local_status)).length;
    $('material-summary').textContent=`${(data.courses||[]).length} 门课程 · ${total} 个可见附件 · ${done} 个已保存到本机${data.updated_at?' · 清单更新：'+new Date(data.updated_at).toLocaleString('zh-CN'):''}${agent.at?' · 代理最近连接：'+new Date(agent.at).toLocaleString('zh-CN'):''}`;
    const jobs=clear('material-jobs');
    if(agent.current_file)jobs.append(node('div',`当前下载：${agent.current_file}`,'muted'));
    if(data.scan?.error)jobs.append(node('div',`附件刷新失败：${data.scan.error}；下面保留上次可见清单。`,'notice'));
    (data.warnings||[]).forEach(w=>jobs.append(node('div',w,'notice')));
    if(!data.baseline_ready)jobs.append(node('div','等待本机确认原有附件基线，暂不自动下载旧资料。','muted'));
    const visibleJobs=(data.jobs||[]).filter(j=>['queued','running','partial','failed'].includes(j.state));
    if(!visibleJobs.length&&(data.jobs||[]).length)visibleJobs.push(data.jobs[0]);
    visibleJobs.slice(0,6).forEach(j=>{
      const box=node('div','','material-job'), head=node('div','','material-job-head');
      head.append(node('strong',j.label),node('span',`${jobText[j.state]||j.state} · ${j.done}/${j.total} 已保存${j.failed?' · '+j.failed+' 失败':''}`));box.append(head);
      const progress=document.createElement('progress');progress.max=Math.max(1,j.total);progress.value=j.done+j.failed;box.append(progress);
      if(j.failed){box.append(node('div',(j.errors||[]).join('；'),'muted'));const retry=node('button','重试未完成附件');retry.onclick=()=>requestDownload({scope:'retry',job_id:j.id},retry);box.append(retry);}
      jobs.append(box);
    });
    const search=$('material-search').value.trim().toLowerCase(), filter=$('material-filter').value;
    const courses=clear('material-courses');let matches=0;
    (data.courses||[]).forEach(course=>{
      const all=(data.items||[]).filter(i=>i.course_id===course.id);
      const items=all.filter(i=>[course.name,i.file_name,i.title,...(i.folders||[])].join(' ').toLowerCase().includes(search)&&
        (filter==='all'||filter==='saved'&&saved(i.local_status)||filter==='pending'&&!saved(i.local_status)||filter==='failed'&&i.local_status==='failed'));
      if((search||filter!=='all')&&!items.length)return;
      matches+=items.length;
      const details=node('details','','material-course');details.open=search?true:openCourses.has(course.id);
      details.addEventListener('toggle',()=>{if(details.open)openCourses.add(course.id);else openCourses.delete(course.id);});
      const summary=node('summary'), count=all.filter(i=>saved(i.local_status)).length;
      summary.append(node('strong',course.name),node('span',`${all.length} 个附件 · ${count} 个已保存`,'muted'));
      const bulk=node('button','课程全部下载','secondary');bulk.disabled=!all.length;bulk.onclick=e=>{e.preventDefault();e.stopPropagation();requestDownload({scope:'course',course_id:course.id},bulk);};summary.append(bulk);details.append(summary);
      const list=node('div','','material-files');
      if(!items.length)empty(list,'该课程暂无当前可访问的附件');
      items.forEach(i=>{
        const entry=node('div','','material-file'), main=node('div','','material-file-main');
        main.append(node('div',i.file_name,'material-file-title'),node('div',`${[...(i.folders||[]),i.title].filter(Boolean).join(' / ')||'课程资料'} · ${size(i.size)}`,'material-file-sub'));
        if(i.local_path)main.append(node('div',`${data.destination} / ${i.local_path}`,'material-file-sub'));
        if(i.error)main.append(node('div',i.error,'material-file-sub'));
        const actions=node('div','','material-file-actions');actions.append(node('span',stateText[i.local_status]||i.local_status,`pill ${i.local_status==='failed'?'overdue':saved(i.local_status)?'':'unknown'}`));
        const download=node('button',saved(i.local_status)?'核验并下载':i.local_status==='failed'?'重试下载':'下载');
        download.disabled=['queued','running'].includes(i.local_status);download.onclick=()=>requestDownload({scope:'file',key:i.key},download);actions.append(download);
        const browser=node('a','浏览器另存');browser.href=apiBase+'/api/attachment?key='+encodeURIComponent(i.key);browser.setAttribute('download',i.file_name);actions.append(browser);
        entry.append(main,actions);list.append(entry);
      });details.append(list);courses.append(details);
    });
    if(!matches&&(search||filter!=='all'))empty(courses,'没有符合条件的附件');
    if(!(data.courses||[]).length)empty(courses,'附件尚未完成首次扫描，可点击“刷新附件清单”。');
  }
  async function refresh(){
    if(fetching)return;fetching=true;
    try{const r=await campusFetch('/api/materials',{cache:'no-store'});if(!r.ok)throw Error(`附件读取失败 (${r.status})`);const next=await r.json();if(!data&&next.courses?.length)openCourses.add(next.courses[0].id);data=next;render();}
    catch(e){message(e.message);}finally{fetching=false;}
  }
  $('material-search').oninput=render;$('material-filter').onchange=render;
  $('material-download-all').onclick=()=>requestDownload({scope:'all'},$('material-download-all'));
  $('material-refresh').onclick=async()=>{const button=$('material-refresh');button.disabled=true;try{await post('/api/materials/refresh',{});message('正在重新扫描附件；发现新资料后，自动模式会提交下载。');await refresh();}catch(e){message(e.message);button.disabled=false;}};
  $('material-auto').onchange=async()=>{const box=$('material-auto'),enabled=box.checked;box.disabled=true;try{await post('/api/materials/auto',{enabled});message(enabled?'已启用自动下载新附件；已有资料不会因开关变化而全部下载。':'已暂停自动下载。正在传输的文件可能完成，手动下载请求仍会执行。');await refresh();}catch(e){message(e.message);box.checked=!enabled;}finally{box.disabled=false;}};
  refresh();setInterval(refresh,5000);
})();
