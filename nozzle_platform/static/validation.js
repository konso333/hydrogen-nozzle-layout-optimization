"use strict";
const $=id=>document.getElementById(id);
let current=null,requestNumber=0;
function node(tag,text,className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
async function json(url){const r=await fetch(url,{cache:"no-store"});const v=await r.json();if(!r.ok)throw new Error(v.error||"无法读取报告");return v;}
function notice(text,error=false){$("notice").textContent=text;$("notice").className=error?"error":"";}
function value(v){if(v===null||v===undefined)return "未取得";if(typeof v==="number")return Number.isFinite(v)?String(v):"未取得";return String(v);}
async function load(id){
 const serial=++requestNumber;current=null;$("report").hidden=true;$("bundle").hidden=true;notice("正在核对报告完整性…");
 try{
  const r=await json("/api/verification-reports/"+encodeURIComponent(id));if(serial!==requestNumber)return;current=r;
  $("title").textContent=r.title;$("summary").textContent=r.summary;$("statuses").replaceChildren();
  for(const [key,label] of [["saved_fields","保存数据"],["convergence","数值收敛"],["physical_scope","物理适用范围"]]){const s=r.statuses[key],card=node("div",undefined,"status-card state-"+s.state);card.append(node("small",label),node("h3",s.label),node("p",s.detail));$("statuses").append(card);}
  $("metrics").replaceChildren();for(const m of r.metrics){const card=node("div");card.append(node("small",m.label),node("strong",value(m.value)),node("span",m.unit||""));$("metrics").append(card);}
  $("method").textContent=r.method;$("missing").replaceChildren();for(const text of r.missing_evidence)$("missing").append(node("li",text));
  $("table-head").replaceChildren();const head=node("tr");for(const c of r.table.columns)head.append(node("th",c));$("table-head").append(head);$("table-body").replaceChildren();
  for(const cells of r.table.rows){const row=node("tr");for(const c of cells)row.append(node("td",value(c)));$("table-body").append(row);}
  $("metric-scope").textContent=r.metric_scope;$("conclusion").textContent=r.conclusion;$("next").textContent=r.next_step;
  const files=new Map(r.files.map(f=>[f.name,f.url]));$("records").replaceChildren();$("charts").replaceChildren();
  for(const f of r.files){const a=node("a",f.name);a.href=f.url;if(f.name.endsWith(".zip")){ $("bundle").href=f.url;$("bundle").hidden=false;}else if(!f.name.endsWith(".png"))$("records").append(a);}
  for(const chart of r.charts||[]){if(!files.has(chart.file))continue;const img=node("img");img.src=files.get(chart.file);img.alt=chart.label;img.loading="lazy";$("charts").append(img);}
  $("chart-panel").hidden=!$("charts").children.length;$("raw").hidden=true;$("raw").textContent="";$("report").hidden=false;notice("报告文件与来源哈希已核对。各项状态独立显示，不代表已获准用于正式方案比较。");
 }catch(e){if(serial===requestNumber)notice(e.message,true);}
}
async function refresh(){
 $("refresh").disabled=true;try{const catalog=await json("/api/verification-reports"),previous=$("report-select").value;$("report-select").replaceChildren();
 for(const r of catalog.reports){const option=node("option",r.title+(r.available?"":" · 校验失败"));option.value=r.report_id;$("report-select").append(option);}
 if(!catalog.reports.length){++requestNumber;current=null;$("report").hidden=true;$("bundle").hidden=true;notice("暂无已审核验证记录。生成并审核后，可在这里查看；浏览本页不会启动仿真。");return;}
 if(catalog.reports.some(r=>r.report_id===previous))$("report-select").value=previous;await load($("report-select").value);
 }catch(e){notice(e.message,true);}finally{$("refresh").disabled=false;}
}
$("report-select").addEventListener("change",()=>load($("report-select").value));$("refresh").addEventListener("click",refresh);
$("show-raw").addEventListener("click",async()=>{if(!current)return;const selected=current,serial=requestNumber;try{const name=selected.raw_excerpt_file,file=selected.files.find(f=>f.name===name);if(!file)throw new Error("没有原始报告摘录，可下载审核 JSON 查看。");const response=await fetch(file.url,{cache:"no-store"});if(!response.ok)throw new Error("原始报告校验未通过。");const text=await response.text();if(serial!==requestNumber)return;$("raw").textContent=text;$("raw").hidden=false;}catch(e){notice(e.message,true);}});
refresh();
