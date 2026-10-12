/* On-demand panels: loading one page never queries every campus service. */
(() => {
  const announcementCache=new Map(), announcementPending=new Map(), announcementOpen=new Set();
  function safeLink(label,url,cls=''){
    const parsed=new URL(url,location.href);
    if(!['https:','http:'].includes(parsed.protocol))return node('span',label);
    const a=link(label,parsed.href,cls);a.target='_blank';a.rel='noopener noreferrer';return a;
  }
  function announcementRender(slot,data){
    slot.replaceChildren();if(!data.items.length)return;
    slot.append(node('h3','课程公告'));
    for(const a of data.items){
      const d=node('details','','fold'),summary=node('summary'),main=node('div','','row-main');
      main.append(node('strong',a.title),node('div',`${timestamp(a.created)}${a.attachments.length?' · '+a.attachments.length+' 个附件':''}`,'row-sub'));
      summary.append(main);const body=node('div','','fold-body rich');body.innerHTML=a.body_html;
      // Body links retain their original context; do not duplicate them below.
      d.open=announcementOpen.has(a.id);d.ontoggle=()=>d.open?announcementOpen.add(a.id):announcementOpen.delete(a.id);
      d.append(summary,body);slot.append(d);
    }
  }
  window.addEventListener('campus-course-open',async e=>{
    const {id,slot}=e.detail,cached=announcementCache.get(id);
    if(cached&&Date.now()-cached.at<300000){announcementRender(slot,cached.data);return;}
    slot.replaceChildren(node('p','正在读取课程公告…','muted'));
    try{
      if(!announcementPending.has(id))announcementPending.set(id,campusJson('/api/announcements/'+encodeURIComponent(id)).finally(()=>announcementPending.delete(id)));
      const data=await announcementPending.get(id);announcementCache.set(id,{at:Date.now(),data});announcementRender(slot,data);
    }catch(error){slot.replaceChildren(node('p','公告暂不可用：'+error.message,'muted'),button('重试公告',()=>window.dispatchEvent(new CustomEvent('campus-course-open',{detail:{id,slot}})),'secondary'));}
  });

  document.addEventListener('DOMContentLoaded',()=>{
    const bbFold=$('bb-grade-fold');
    if(bbFold){
      let loaded=false,busy=false;
      async function loadGrades(){if(busy)return;busy=true;const el=$('bb-grade-results');notice(el,'正在读取本人成绩簿…');
        try{const data=await campusJson('/api/blackboard/grades');loaded=true;el.replaceChildren();$('bb-grade-note').textContent=data.note;
          for(const warning of data.warnings||[])el.append(node('p',warning,'notice'));
          for(const r of data.rows){const d=node('details','','fold'),s=node('summary'),m=node('div','','row-main');m.append(node('strong',r.name),node('div',r.course,'row-sub'));
            const score=r.exempt?'已免交':r.display_grade??r.score??'未公布';s.append(m,node('span',String(score),'pill '+(r.exempt?'':'unknown')));
            const body=node('div','','fold-body');body.append(node('p',`满分：${r.possible??'未标'} · 来源：本人成绩簿列成绩`,'muted'));
            if(r.feedback_html){const feedback=node('div','','rich');feedback.innerHTML=r.feedback_html;body.append(feedback);}d.append(s,body);el.append(d);
          }if(!data.rows.length)empty(el,data.warnings?.length?'部分来源未读取成功，请重试。':'Blackboard 暂无可见成绩项');
        }catch(e){notice(el,e.message);}finally{busy=false;}}
      bbFold.ontoggle=()=>{if(bbFold.open&&!loaded)loadGrades();};$('bb-grade-refresh').onclick=loadGrades;
    }
    const extra=document.querySelector('[data-extra]');if(!extra)return;
    const kind=extra.dataset.extra,form=$('extra-form'),out=$('extra-results'),message=$('extra-message');
    let loading=false,query={},review=null,operation=null;
    const inputs={};
    function input(name,label,type='text',options=null){
      const wrap=node('label',label),control=node(options?'select':'input');control.name=name;
      if(options){for(const [value,text] of options)control.add(new Option(text,value));}else control.type=type;
      inputs[name]=control;wrap.append(control);form.append(wrap);return control;
    }
    function options(name,values){const el=inputs[name],old=el.value;el.replaceChildren();for(const [v,t] of values)el.add(new Option(t,v));if(values.some(x=>x[0]===old))el.value=old;}
    const search=()=>input('q','关键词');
    if(kind==='transit'){
      input('op','查询内容','text',[['lines','校巴线路'],['live','实时车辆'],['stops','沿线站点'],['places','建筑与校门'],['route','路线估算']]);
      input('day','班次类型','text',[['workday','工作日'],['holiday','节假日']]);
      input('line','校巴站点线路','text',[['XYBS1','1 路'],['XYBS2','2 路']]);input('direction','站点方向','text',[['0','顺时针'],['1','逆时针']]);search();
      input('from','出发地点','text',[['','先查询建筑与校门']]);input('to','到达地点','text',[['','先查询建筑与校门']]);
      input('mode','路线方式','text',[['transit','步行与校巴'],['bus','校巴'],['walk','步行连接估算']]);
    }else if(kind==='nces'){search();input('teacher','教师姓名');}
    else if(kind==='faculty'){input('department','院系','text',[['','加载院系目录…']]);search();}
    else if(kind==='programs'){search();}
    else if(kind==='mirror'){input('kind','资料类型','text',[['syllabus','课程教学大纲'],['program','培养方案镜像'],['handbook','手册镜像'],['map','校园地图']]);input('q','课程代码');input('text','大纲正文','text',[['0','PDF / HTML 入口'],['1','提取 PDF 文本']]);}
    else if(kind==='library'){search();input('field','检索字段','text',[['any','全部字段'],['title','标题'],['creator','作者'],['subject','主题']]);input('kind','资源类型','text',[['','全部'],['Book','图书'],['Article','文章'],['Journal','期刊'],['Dissertation','学位论文']]);input('fulltext','全文','text',[['0','全部'],['1','在线全文']]);input('peer','同行评审','text',[['0','不限'],['1','仅同行评审']]);}
    else if(kind==='content'){search();input('course','课程','text',[['','全部当前学期课程']]);input('kind','内容类型','text',[['','全部类型']]);}
    else if(kind==='cle'){input('op','查询内容','text',[['slots','可预约时段'],['walkin','今日 walk-in'],['mine','我的预约'],['catalog','指导类型与老师']]);input('date','指定日期','date');input('service','指导类型');input('teacher','教师姓名');}
    else if(kind==='borrow'){
      input('name','教室名称');input('date','使用日期','date').value=localDate(1);input('start','开始节次','number').value=1;input('end','结束节次','number').value=2;
      input('headcount','使用人数','number').value=1;input('applicant','申请人 / 使用人');input('phone','联系电话','tel');input('purpose','借用用途');
      for(const el of Object.values(inputs))el.required=true;
      for(const n of ['start','end']){inputs[n].min=1;inputs[n].max=12;}inputs.headcount.min=1;
      const initial=new URLSearchParams(location.search);for(const n of ['name','date','start','end'])if(initial.has(n))inputs[n].value=initial.get(n);
    }
    const submit=node('button',kind==='borrow'?'预览借用申请':'查询');submit.type='submit';form.append(submit);
    function conditionFields(){
      if(kind!=='transit')return;
      const op=inputs.op.value;
      for(const n of ['day','line','direction','q','from','to','mode'])inputs[n].parentElement.hidden=!({day:['lines'],line:['stops'],direction:['stops'],q:['places'],from:['route'],to:['route'],mode:['route']}[n].includes(op));
    }
    if(kind==='transit'){inputs.op.onchange=conditionFields;conditionFields();}
    function params(){return Object.fromEntries(new FormData(form));}
    function info(parent,values){const dl=node('dl','','extra-summary');for(const [label,value] of Object.entries(values)){if(value==null||value==='')continue;dl.append(node('dt',label),node('dd',typeof value==='object'?JSON.stringify(value):String(value)));}parent.append(dl);}
    function detailPanel(title){$('extra-detail-panel').hidden=false;$('extra-detail-title').textContent=title;const el=clear('extra-detail');return el;}
    async function details(args,title){const el=detailPanel(title);notice(el,'正在读取详情…');try{const data=await campusJson('/api/extras/'+kind+'?'+new URLSearchParams(args));el.replaceChildren();renderItems(data,el,true,args);}catch(e){notice(el,e.message);}}
    async function preview(payload){notice(message,'正在核对学校当前状态…');$('extra-review').hidden=true;review=null;
      try{const r=await campusPost('/api/extras/'+kind+'/preview',payload);review=r;operation=crypto.randomUUID();const el=clear('extra-review-body');info(el,r.summary);$('extra-review').hidden=false;$('extra-confirm').disabled=false;notice(message,'请核对下方本次操作内容；确认后才会发送。');$('extra-review').scrollIntoView({block:'start'});}catch(e){notice(message,e.message);}}
    $('extra-confirm').onclick=async()=>{if(!review)return;const b=$('extra-confirm');b.disabled=true;try{const r=await campusPost('/api/extras/'+kind+'/commit',{review_id:review.review_id,operation_id:operation,confirmed:true});review=null;$('extra-review').hidden=true;notice(message,r.message,r.state==='confirmed');if(r.official_url)message.append(safeLink('查看学校记录',r.official_url));if(r.receipt_number)message.append(node('p','申请编号：'+r.receipt_number));}catch(e){notice(message,e.message);b.disabled=false;}};
    $('extra-cancel').onclick=()=>{review=null;$('extra-review').hidden=true;};$('extra-close').onclick=()=>{$('extra-detail-panel').hidden=true;};
    async function load(args=params()){
      if(loading)return;loading=true;submit.disabled=true;query={...args};notice(message,'正在查询…');out.setAttribute('aria-busy','true');
      try{const data=await campusJson('/api/extras/'+kind+'?'+new URLSearchParams(args));out.replaceChildren();clear('extra-pages');notice(message,(data.warnings||[]).join('；'));$('extra-note').textContent=data.note||'';$('service-updated').textContent='最近查询：'+timestamp(data.updated_at);
        if(data.departments)options('department',[['','请选择院系'],...data.departments.map(x=>[x,x])]);
        if(data.courses)options('course',[['','全部当前学期课程'],...data.courses.map(x=>[x.id,x.name])]);
        if(data.counts)options('kind',[['','全部类型'],...Object.entries(data.counts).map(([k,v])=>[k,`${typeName(k)} (${v})`])]);
        if(kind==='transit'&&args.op==='places'){const places=[['','请选择地点'],...data.items.map(x=>[x.facility_id,x.name])];options('from',places);options('to',places);}
        if(data.quota)out.append(node('p',`本学期剩余额度：${data.quota.remaining??'待核对'} / ${data.quota.limit??'待核对'}`,'muted'));
        renderItems(data,out,false,args);
        if(data.source)out.append(safeLink('打开来源页面',data.source));
        const pager=$('extra-pages'),page=Number(args.page||1);if(data.total>20){const prev=button('上一页',()=>load({...args,page:page-1}),'secondary'),next=button('下一页',()=>load({...args,page:page+1}),'secondary');prev.disabled=page<=1;next.disabled=page*20>=data.total;pager.append(prev,node('span',`第 ${page} 页 · ${data.total} 项`,'muted'),next);}
        if(kind==='library'&&args.op!=='detail'){const offset=Number(args.offset||0),prev=button('上一页',()=>load({...args,offset:Math.max(0,offset-10)}),'secondary'),next=button('下一页',()=>load({...args,offset:offset+10}),'secondary');prev.disabled=offset===0;next.disabled=data.items.length<10;pager.append(prev,next);}
      }catch(e){notice(message,e.message);out.replaceChildren();empty(out,'未取得查询结果；请重试或核对学校入口。');}finally{loading=false;submit.disabled=false;out.removeAttribute('aria-busy');}
    }
    function typeName(k){return {document:'文本与资料',file:'文件',externallink:'外部链接',video:'视频',folder:'文件夹',assignment:'作业',assessment:'测验',modulepage:'模块页面'}[k]||k;}
    function renderItems(data,el,isDetail,args){
      for(const item of data.items||[]){
        if(kind==='transit'){
          if(item.routes&&item.title){const r=row(el,item.title,'');for(let i=0;i<item.routes.length;i++){const route=item.routes[i];r.append(button(route.name||'查看班次',()=>details({op:'schedule',line:item.id,route:i,day:inputs.day.value},item.title),'secondary'));}}
          else if(item.times){info(el,{'线路':item.title,'方向':item.sub_route_name,'班次':item.times.join(' · '),'说明':item.sub_route_desc});}
          else if(item.steps){info(el,{'出发':item.origin,'到达':item.destination,'预计时间':`${Math.round(item.total_minutes)} 分钟`,'距离':`${Math.round(item.total_meters)} 米`});for(const s of item.steps)row(el,s.from_name+' → '+s.to_name,`${s.mode==='walk'?'步行连接估算':'校巴'} · ${s.details||''}`,`${Math.round(s.duration_min)} 分`);}
          else if(item.facility_id){row(el,item.name,`${item.name_en||''} · 坐标 ${item.lat}, ${item.lng}`);}
          else {info(el,{'车辆':item.bus_id||'运行车辆','线路':item.route_code,'下一站':item.next_station,'纬度':item.lat,'经度':item.lng,'速度':item.speed_kmh==null?'':item.speed_kmh+' km/h','更新时间':item.timestamp?timestamp(new Date(item.timestamp*1000).toISOString()):''});}
        }else if(kind==='nces'){
          const name=item.name||item.code||'课程',id=item.nces_id||item.id;
          const r=row(el,name,`${item.teacher||item.teacher_names||''} · ${item.semester||item.course_code||''}`,`${item.rating??item.rate_average??'—'} / 10`);
          if(!isDetail&&id)r.append(button('评分与评论',()=>details({op:'detail',id},name),'secondary'));
          if(isDetail){info(el,{'课程代码':item.code,'教师':item.teacher,'评论数':item.review_count,'学期':(item.semesters||[]).join('、')});for(const review of item.reviews||[])row(el,review.semester||review.created_at||'社区评论',review.content||review.text||review.comment||'');if(item.detail_url)el.append(safeLink('查看社区原文',item.detail_url));}
        }else if(kind==='faculty'){
          const r=row(el,item.name,[item.title,item.department].filter(Boolean).join(' · '));
          if(isDetail){el.append(node('div',item.profile_text||item.research_interests||'','extra-detail-text'));if(item.profile_url)el.append(safeLink('教师公开主页',item.profile_url));}
          else r.append(button('研究方向与资料',()=>details({op:'detail',id:item.slug},item.name),'secondary'));
        }else if(kind==='programs'){
          if(isDetail){for(const [title,fields] of Object.entries(item.sections||{})){el.append(node('h3',title));info(el,fields);}for(const table of item.tables||[])el.append(node('div',table.map(r=>r.join(' · ')).join('\n'),'extra-body'));}
          else {const title=item.ProjectName||item.Name||item.Title||item.ProjectNameCN||'交流项目',r=row(el,title,[item.CountryName,item.ApplyBeginDate,item.ApplyEndDate].filter(Boolean).join(' · '));r.append(button('查看要求与详情',()=>details({op:'detail',id:item.ID},title),'secondary'));}
        }else if(kind==='mirror'){
          const r=row(el,item.title,item.size?`${Math.ceil(item.size/1024)} KB`:'');
          if(item.directory)r.append(button('展开目录',()=>load({...args,path:item.path}),'secondary'));else if(item.url)r.append(safeLink('打开 / 下载',item.url));
          if(item.html_url)r.append(safeLink('HTML 版',item.html_url));if(item.text)el.append(node('div',item.text,'extra-body'));
        }else if(kind==='library'){
          const title=item.title||'文献',r=row(el,title,item.body||item.format||'');
          if(!isDetail&&item.id)r.append(button('文献详情',()=>details({op:'detail',id:item.id},title),'secondary'));
          if(isDetail){info(el,{'作者':(item.authors||[]).join('、'),'出版':item.publisher,'年份':item.year,'语言':item.language,'主题':(item.subjects||[]).join('、'),'ISBN':item.isbn,'摘要':item.abstract,'全文':item.full_text_availability});if(item.online_url)el.append(safeLink('全文入口',item.online_url));}
          if(item.url||item.detail_url)r.append(safeLink('图书馆原页面',item.url||item.detail_url));
        }else if(kind==='content'){
          const r=row(el,item.title,`${item.course} · ${typeName(item.kind)} · ${(item.folders||[]).join(' / ')}`);r.append(safeLink('打开课程内容',item.url));if(item.excerpt)r.append(node('p',item.excerpt,'extra-body'));
        }else if(kind==='cle'){
          if(args.op==='catalog'){row(el,item.name,`${item.teachers??'—'} 位老师`);continue;}
          const r=row(el,item.service||'语言指导',[item.when,item.teacher,item.room].filter(Boolean).join(' · '),item.status||'');
          if(args.op==='slots'&&item.status==='free')r.append(button('预约',()=>{const detail=detailPanel('预约说明'),label=node('label','本次希望获得什么指导？'),note=node('textarea');note.maxLength=500;label.append(note);detail.append(label,button('预览预约',()=>preview({action:'book',id:item.slot_id,note:note.value})));},'secondary'));
          if(args.op==='mine'&&item.active)r.append(button('取消预约',()=>preview({action:'cancel',id:item.id}),'secondary'));
        }else if(kind==='evaluations'){
          if(args.op==='form')continue;
          const state={'0':'待评价','1':'已放弃','2':'已评价','3':'已保存未提交','4':'未结课','5':'已评价'}[item.status]||'状态未知';
          const r=row(el,item.title,item.task_type,state);
          if(['0','3'].includes(item.status))r.append(button('读取问卷',async()=>{const panel=detailPanel(item.title);notice(panel,'正在读取官方问卷…');try{const d=await campusJson('/api/extras/evaluations?'+new URLSearchParams({op:'form',id:item.id}));panel.replaceChildren();panel.append(node('p',d.note,'muted'));const answers={};for(const q of d.items){const label=node('label',q.text),control=node(q.type==='rating'?'select':'textarea');if(q.type==='rating'){control.add(new Option('请选择',''));for(const opt of q.options)control.add(new Option(opt,opt));}else if(q.type!=='text')control.disabled=true;control.dataset.qid=q.id;answers[q.id]=control;label.append(control);label.className='extra-fields';panel.append(label);}panel.append(button('预览保存草稿',()=>preview({action:'save',id:item.id,answers:Object.fromEntries(Object.entries(answers).map(([key,control])=>[key,control.value]))})));}catch(e){notice(panel,e.message);}},'secondary'));
          if(item.status==='3')r.append(button('提交已保存问卷',()=>preview({action:'submit',id:item.id}),'secondary'));
        }
      }
      if(kind==='cle'&&data.teachers){el.append(node('h3','指导老师'));for(const t of data.teachers)row(el,t.JSXM||t.JSGH||'老师',[t.FWLX_DISPLAY,t.FWZY_DISPLAY].filter(Boolean).join(' · '));}
      if(!(data.items||[]).length)empty(el,kind==='faculty'&&!args.department?'选择院系后查询教师及研究方向。':'当前查询没有返回条目。');
      if(isDetail&&data.note)el.append(node('p',data.note,'muted'));
    }
    form.onsubmit=e=>{e.preventDefault();kind==='borrow'?preview(params()):load();};
    if(kind==='borrow'){$('extra-note').textContent='先从空闲教室中选择场地。提交前重新核对权限、课程占用和临时借用；发送的是审批申请。';}
    else if(['faculty','evaluations','transit'].includes(kind))load();
    else empty(out,'设置查询条件后点击查询。');
  });
})();
