let latestCampus={};
function metric(el,value,label){const box=node('div','','metric');box.append(node('strong',value),node('span',label));el.append(box);}
function renderWeek(tis){
 const root=clear('week-schedule'),week=tis.week_schedule;if(!week?.days?.length){empty(root,'本周课表尚未成功同步');return;}
 $('week-range').textContent=`${week.start} 至 ${week.end} · 教学第 ${tis.week} 周`;
 const grid=node('div','','week-grid');root.append(grid);grid.append(node('div','北京时间','week-cell week-head'));
 const labels=['周一','周二','周三','周四','周五','周六','周日'];
 week.days.forEach((day,index)=>{const h=node('div','','week-cell week-head'+(day.date===localDate()?' today':''));h.append(node('span',labels[index]),node('strong',Number(day.date.slice(-2))));if(['holiday','makeup'].includes(day.calendar.kind))h.append(node('div',day.calendar.label.replace('，今日无课',''),'week-day-note'));grid.append(h);});
 const slots=[['1–2 节','08:00–09:50',[1,2]],['3–4 节','10:20–12:10',[3,4]],['5–6 节','14:00–15:50',[5,6]],['7–8 节','16:20–18:10',[7,8]],['9–10 节','19:00–20:50',[9,10]]];if(week.days.some(d=>d.classes?.some(c=>c.periods?.some(p=>p>10))))slots.push(['11–12 节','时段以官方通知为准',[11,12]]);
 const colors=['#147d72','#315fb0','#7855a3','#ab6c28','#256e82','#aa4e7d'];function color(title){return colors[[...title].reduce((n,c)=>n+c.charCodeAt(0),0)%colors.length];}
 slots.forEach(([title,time,periods])=>{const t=node('div','','week-cell week-time');t.append(node('strong',title),node('span',time));grid.append(t);week.days.forEach(day=>{const cell=node('div','','week-cell');(day.classes||[]).filter(c=>(c.periods||[]).some(p=>periods.includes(p))).forEach(c=>{const event=node('div','','course-event');event.style.setProperty('--event',color(c.KCMC||''));event.append(node('strong',c.KCMC),node('span',c.room||'未标教室'),node('span',`第 ${(c.periods||[]).join('、')} 节 · ${time}`));event.title=`${c.KCMC} · ${c.room||''} · ${c.teacher||''}`;cell.append(event);});grid.append(cell);});});
}
function renderDashboard(data){
 latestCampus=data;$('updated').textContent=data.updated_at?`最近同步：${timestamp(data.updated_at)} · 页面每分钟更新倒计时`:'尚未完成首次同步';
 const issues=[...Object.entries(data.errors||{}).map(([k,v])=>`${k}：${v}`),...(data.warnings||[])];$('problems').hidden=!issues.length;const problem=clear('problem-list');issues.forEach(v=>problem.append(node('div',v,'notice')));
 const tis=data.tis||{},tasks=data.assignments||[],pending=tasks.filter(a=>['not_submitted','draft'].includes(a.status)),m=clear('metrics');metric(m,tis.semester||'—','当前学期');metric(m,tis.week??'—','教学周');metric(m,pending.length,'未提交或草稿作业');metric(m,(tis.exams||[]).length,'后续考试');if(data.weather?.condition)metric(m,`${data.weather.temperature??'—'}°C`,data.weather.condition);
 const cl=clear('classes'),cal=tis.calendar||{};cl.append(node('h3','今日课程'));
 if(tis.date!==localDate())empty(cl,'等待今日课表同步，未显示其他日期的课程');else{if(cal.label)cl.append(node('div',cal.label,cal.kind==='unknown'?'notice':'ok'));if(cal.kind==='holiday'&&cal.start)cl.append(node('div',`假期：${cal.start} 至 ${cal.end}`,'muted'));if(cal.kind==='makeup'&&cal.source_date)cl.append(node('div',`按 ${cal.source_date}（第 ${cal.source_week} 教学周）的课表补课`,'muted'));(tis.today_classes||[]).forEach(c=>row(cl,c.KCMC||'课程',c.SKSJ||''));if(!(tis.today_classes||[]).length&&!['holiday','break','vacation','final','unknown'].includes(cal.kind))empty(cl,'今日没有已排课程');if(cal.next_makeup)cl.append(node('div',`下次调休补课：${cal.next_makeup.date} · ${cal.next_makeup.label}`,'muted'));if(cal.data_checked_at)cl.append(node('div',`校历核验：${timestamp(cal.data_checked_at)}${cal.cached_data?' · 缓存数据':''}`,'muted'));}
 renderWeek(tis);if(window.renderAssignments)window.renderAssignments(tasks);
 const ex=clear('exams');if(!(tis.exams||[]).length)empty(ex,'暂无已发布的后续考试');else tis.exams.forEach(e=>row(ex,e.KCMC||'考试',`${e.KSRQ||''} ${e.KSJTSJ||''} · ${e.JXLMC||''} ${e.JXCDMC||''}`));const ev=clear('evals');if(!(tis.pending_evals||[]).length)empty(ev,'暂无待评教');else tis.pending_evals.forEach(e=>row(ev,e.course,e.status));
}
async function refresh(){try{renderDashboard(await campusJson('/api/status'));}catch(e){$('updated').textContent=`读取校园数据失败：${e.message}`;}}
async function loadVenues(){
 const b=$('venues-refresh');b.disabled=true;
 try{const data=await campusJson('/api/venues');notice($('venues-message'),(data.errors||[]).join('\n'));const l=clear('library-venues'),eh=clear('ehall-venues');if(!data.library?.length)empty(l,'场地列表未读取成功');(data.library||[]).forEach(r=>{const outer=row(l,r.name,r.total!=null?`${r.idle??'—'}/${r.total} 当前空闲`:'查看具体开放和预约情况');outer.append(link('查看与预约',`${apiBase}/venues/library/${encodeURIComponent(r.id)}`,'button'));});if(!data.ehall?.length)empty(eh,'暂无可查询的场地');(data.ehall||[]).forEach(r=>{const outer=row(eh,r.name,`${r.location||''} · ${r.kind_name||''}${r.needs_approval?' · 需审批':''}`);outer.append(link(r.bookable?'预约':'查看',`${apiBase}/venues/ehall/${encodeURIComponent(r.id)}`,`button${r.bookable?'':' secondary'}`));});
 for(const[system,id]of[['library','library-mine'],['ehall','ehall-mine']]){const target=clear(id);try{const d=await campusJson(`/api/venues/mine?system=${system}&start=${localDate()}&end=${localDate(30)}`);if(!d.records.length)empty(target,'暂无当前或后续预约');d.records.forEach(r=>row(target,r.room_name||r.testName||r.title||'预约',`${timestamp(r.begin)} — ${clock(r.end)}`));}catch(e){empty(target,e.message);}}
 }catch(e){notice($('venues-message'),e.message);}finally{b.disabled=false;}
}
$('venue-catalog-search').oninput=()=>{const q=$('venue-catalog-search').value.trim().toLowerCase();document.querySelectorAll('.venue-catalog .row').forEach(r=>r.style.display=r.textContent.toLowerCase().includes(q)?'':'none');};
$('venues-refresh').onclick=loadVenues;refresh();loadVenues();setInterval(refresh,60000);
