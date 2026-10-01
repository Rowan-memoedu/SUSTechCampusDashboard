const campusFetch=(path,options)=>fetch(apiBase+path,options);
const $=id=>document.getElementById(id);
function node(tag,text='',cls=''){const e=document.createElement(tag);e.textContent=String(text??'');if(cls)e.className=cls;return e;}
function clear(id){const e=$(id);e.replaceChildren();return e;}
function empty(el,text='暂无数据'){el.append(node('div',text,'empty'));}
function row(el,title,subtitle='',right=''){const r=node('div','','row'),m=node('div','','row-main');m.append(node('div',title,'row-title'));if(subtitle)m.append(node('div',subtitle,'row-sub'));r.append(m);if(right)r.append(node('div',right,'countdown'));el.append(r);return r;}
function localDate(offset=0){return new Date(Date.now()+8*3600000+offset*86400000).toISOString().slice(0,10);}
function clock(iso){return iso?new Date(iso).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit'}):'—';}
function timestamp(iso){return iso?new Date(iso).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'未标时间';}
function countdown(iso){if(!iso)return '未标截止时间';const ms=new Date(iso).getTime()-Date.now();if(!Number.isFinite(ms))return '时间不明';if(ms<0)return '已截止';const m=Math.ceil(ms/60000),d=Math.floor(m/1440),h=Math.floor(m%1440/60);return d?`${d} 天 ${h} 小时`:h?`${h} 小时 ${m%60} 分`:`${m} 分钟`;}
const statusText={submitted:'已提交',not_submitted:'未提交',draft:'草稿未提交',unknown:'状态未知'};
async function campusJson(path,options={}){const r=await campusFetch(path,{cache:'no-store',...options});let d;try{d=await r.json();}catch{throw Error(r.status===413?'文件总大小超过 256 MB':`读取失败（${r.status}），请稍后重试`);}if(!r.ok)throw Object.assign(Error(d.error||d.message||`请求失败（${r.status}）`),{result:d});return d;}
function campusPost(path,data){return campusJson(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':roomCsrf},body:JSON.stringify(data)});}
function button(text,handler,cls=''){const e=node('button',text,cls);e.type='button';e.onclick=handler;return e;}
function link(text,url,cls=''){const e=node('a',text,cls);e.href=url;return e;}
function notice(el,text,success=false){el.replaceChildren();if(text)el.append(node('div',text,`notice${success?' success':''}`));}
function rawDetails(title,data){const d=node('details','','fold'),s=node('summary',title),p=node('pre',typeof data==='string'?data:JSON.stringify(data,null,2),'raw-info fold-body');d.append(s,p);return d;}
