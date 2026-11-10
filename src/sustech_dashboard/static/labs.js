/* Hallmark · pre-emit critique: P4 H4 E4 S5 R5 V3 */
(()=>{
 const panel=document.getElementById('labs-panel');if(!panel)return;
 const toggle=document.getElementById('labs-auto'),info=document.getElementById('labs-info'),root=document.getElementById('labs-jobs');let busy=false;
 const names={discover:'下载并核对来源',generate:'生成题面与代码',review:'独立审核',verify:'编译与本地测试',publish:'写入学习工程',connect:'打开并接入 IDEA',done:'IDEA 已接入'};
 const states={queued:'等待处理',running:'正在处理',retry_wait:'等待自动恢复',needs_attention:'需要核对',failed:'暂未完成',complete:'已完成'};
 const node=(tag,value,cls)=>{const e=document.createElement(tag);e.textContent=value;if(cls)e.className=cls;return e;};
 const message=text=>{info.textContent=text;info.setAttribute('role','status');};
 async function refresh(){if(busy)return;busy=true;try{
  const response=await fetch(`${apiBase}/api/labs`);if(!response.ok)throw Error('Lab 状态暂时无法读取');const data=await response.json();
  toggle.checked=data.enabled;toggle.disabled=!data.configured;
  const pending=data.jobs.filter(j=>j.state!=='complete').length,attention=data.jobs.filter(j=>['needs_attention','failed'].includes(j.state)).length;document.getElementById('labs-summary').textContent=!data.enabled?'未启用':data.discovery_error?'获取暂未完成':attention?`${attention} 项需要核对`:pending?`${pending} 项处理中`:'已完成同步';
  message(!data.configured?'尚未配置 CS109 学习工程与 IDEA。':!data.enabled?'自动处理已关闭，现有文件和任务保留。':data.discovery_error||`从 Week ${data.min_week} 开始持续获取；检查通过后自动打开 IDEA。`);
  root.replaceChildren();if(!data.jobs.length)root.append(node('p',data.enabled?'等待发现新的 Lab。':'开启后将自动获取后续 Lab。','muted'));
  for(const job of data.jobs){const row=node('div','','row'),main=node('div','','row-main');
   main.append(node('strong',`${job.name} · ${states[job.state]||job.state}`));
   main.append(node('p',job.connected?'已确认课程节点、模块及 Check 入口。':job.written?'已写入工程，等待 IDEA 确认加载。':names[job.stage]||job.stage,'muted'));
   if(job.state==='retry_wait'){const when=job.next_retry_at?new Date(job.next_retry_at).toLocaleString('zh-CN'): '稍后';main.append(node('p',`${when} 自动重试，已完成阶段保留。`,'muted'));}
   if(job.preserved_count)main.append(node('p',`已保留 ${job.preserved_count} 个现有文件，不覆盖你的答案。`,'muted'));
   if(job.error){const detail=document.createElement('details');detail.append(node('summary','未完成原因'),node('p',job.error));main.append(detail);}
   row.append(main);if(['needs_attention','failed'].includes(job.state)){const button=node('button','重试当前阶段','secondary');button.type='button';button.onclick=async()=>{button.disabled=true;try{await campusPost(`/api/labs/${job.id}/retry`,{revision:job.revision});await refresh();}catch(e){message(e.message);}finally{button.disabled=false;}};row.append(button);}root.append(row);
  }
 }catch(e){message(e.message);}finally{busy=false;}}
 toggle.addEventListener('change',async()=>{toggle.disabled=true;try{await campusPost('/api/labs/settings',{enabled:toggle.checked});}catch(e){message(e.message);}finally{await refresh();}});
 refresh();setInterval(refresh,8000);
})();
