"use strict";
const $=id=>document.getElementById(id);
const state={sources:[],model:null,scheme:null,busy:false,view:"3d",viewer:null};
const familyNames={rectangular:"方阵",honeycomb:"蜂巢",sunflower:"向日葵",ring:"环形"};
const fmt=(v,d=3)=>v===null||v===undefined?"—":Number(v).toLocaleString("zh-CN",{maximumFractionDigits:d});
function notice(message,type=""){ $("notice").textContent=message;$("notice").className="notice "+type; }
function availability(){
  for(const element of document.querySelectorAll("form input,form select,form button"))element.disabled=state.busy;
  $("generate").disabled=state.busy||!state.model;
  for(const id of ["export-scheme","export-cad","save-image"])$(id).disabled=state.busy||!state.scheme;
}
function busy(on,text="处理中…"){state.busy=on;$("busy-overlay").hidden=!on;$("busy-text").textContent=text;availability();}
async function api(path,body,headers={}){
  const response=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json",...headers},body:typeof body==="string"||body instanceof ArrayBuffer?body:JSON.stringify(body)});
  const data=await response.json();if(!response.ok)throw new Error(data.error||"本次操作未完成。");return data;
}
function sourceOptions(selected){
  const select=$("model-select");select.replaceChildren();
  for(const source of state.sources){const option=document.createElement("option");option.value=source.source_id;option.textContent=source.label;select.append(option);}
  if(selected)select.value=selected;
}
function setSourceReference(){
  const source=state.sources.find(s=>s.source_id===$("model-select").value);
  $("axis").value=source?.default_axis||"y";
  ["origin-x","origin-y","origin-z"].forEach((id,i)=>{$(id).value=source?.default_origin_mm?.[i]??"";});
}
function invalidate(message=false){
  state.scheme=null;$("coordinate-rows").innerHTML='<tr><td colspan="5" class="empty-table">参数已更新，生成后显示坐标</td></tr>';
  for(const id of ["metric-count","metric-spacing","metric-gap","metric-wall"])$(id).textContent="—";
  $("plan-view").replaceChildren();$("view-name").textContent="单喷嘴预览";
  $("result-note").textContent="生成后可检查坐标，导出参数与真实实体阵列。";
  $("viewer-status").textContent=state.model?"模型已载入 · 等待生成":"等待模型";
  if(state.model&&state.viewer)state.viewer.setModel(state.model);
  if(message)notice("参数已变更，请重新生成阵列。","neutral");availability();
}
function invalidateModel(){
  invalidate();state.model=null;$("model-state").textContent="待载入";$("model-state").className="tag";
  $("empty-state").hidden=false;$("model-info").textContent="模型或参考坐标已更改，请点击「载入模型」确认。";
  if(state.viewer){state.viewer.model=null;state.viewer.draw();}
  $("viewer-status").textContent="等待载入模型";availability();notice("请载入所选模型，再生成阵列。","neutral");
}
async function loadModel(){
  if(!$("model-select").value){notice("请先导入一个 STEP 模型。","error");return;}
  invalidateModel();busy(true,"正在读取真实 CAD 曲面…");
  try{
    const fields=["origin-x","origin-y","origin-z"].map(id=>$(id).value.trim());
    if(fields.some(Boolean)&&fields.some(v=>!v))throw new Error("请完整填写 X、Y、Z，或全部留空使用自动参考点。");
    const origin=fields.every(v=>v!=="")?fields.map(Number):null;
    if(origin&&origin.some(v=>!Number.isFinite(v)))throw new Error("安装原点必须为有限数值。");
    const model=await api("/api/load-model",{source_id:$("model-select").value,axis:$("axis").value,origin});
    state.model=model;["origin-x","origin-y","origin-z"].forEach((id,i)=>{$(id).value=model.origin_mm[i];});
    $("diameter").value=model.envelope_diameter_mm;$("diameter").min=model.envelope_diameter_mm;
    $("spacing").value=model.envelope_diameter_mm+Number($("gap").value);
    $("model-state").textContent="已载入";$("model-state").className="tag ready";
    $("model-info").textContent=`${model.label} · ${model.solid_count} 个实体 · 整体包络 Ø${fmt(model.envelope_diameter_mm)} mm · 原单位 ${model.source_unit}`;
    $("empty-state").hidden=true;state.viewer?.setModel(model);$("viewer-status").textContent="真实模型已载入 · 等待生成";
    $("view-subtitle").textContent=`真实 CAD 曲面 · 原轴线 ${model.axis.toUpperCase()} → 阵列 +Z · mm`;
    notice(origin?"模型已载入。请核对安装尺寸和约束，点击「生成阵列」。":"模型已载入。安装原点按包围盒估计，请先在「轴线与安装参考点」中核对。",origin?"":"neutral");
  }catch(error){notice(error.message,"error");}finally{busy(false);}
}
function parameters(){
  const val=id=>{const text=$(id).value.trim();if(text==="")throw new Error("请填写所有必需的设计参数。");const value=Number(text);if(!Number.isFinite(value))throw new Error("设计参数必须为有限数值。");return value;};
  return {model_id:state.model.model_id,N:val("count"),family:$("family").value,region:$("region").value,
    radius_mm:val("radius"),start_angle_deg:$("region").value==="circle"?0:val("start-angle"),sweep_angle_deg:$("region").value==="circle"?360:val("sweep-angle"),
    diameter_mm:val("diameter"),edge_gap_mm:val("gap"),wall_clearance_mm:val("wall"),spacing_mode:$("spacing-mode").value,
    spacing_mm:val("spacing"),rotation_deg:val("rotation")};
}
function planSvg(scheme){
  const r=scheme.request,R=r.radius_mm,size=R*1.15;let boundary;
  if(r.region==="circle")boundary=`<circle cx="0" cy="0" r="${R}"/>`;
  else{const a=r.start_angle_deg*Math.PI/180,b=a+r.sweep_angle_deg*Math.PI/180;boundary=`<path d="M0 0 L${R*Math.cos(a)} ${-R*Math.sin(a)} A${R} ${R} 0 ${r.sweep_angle_deg>180?1:0} 0 ${R*Math.cos(b)} ${-R*Math.sin(b)} Z"/>`;}
  const circles=scheme.coordinates_mm.map(([x,y],i)=>`<circle cx="${x}" cy="${-y}" r="${r.diameter_mm/2}" fill="#ddf3ed" stroke="#3b8e7f"/><text x="${x}" y="${-y}" text-anchor="middle" dominant-baseline="central" font-size="${R/15}" fill="#237564" stroke="none">${i+1}</text>`).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${-size} ${-size} ${size*2} ${size*2}"><g fill="none" stroke="#7b9a9e" stroke-width="${R/200}">${boundary}</g><g stroke-width="${R/220}">${circles}</g></svg>`;
}
function display(scheme){
  state.scheme=scheme;const summary=scheme.summary;
  $("metric-count").textContent=summary.nozzle_count;$("metric-spacing").textContent=fmt(summary.minimum_center_distance_mm);
  $("metric-gap").textContent=fmt(summary.minimum_edge_gap_mm);$("metric-wall").textContent=fmt(summary.minimum_wall_clearance_mm);
  $("coordinate-rows").replaceChildren();scheme.coordinates_mm.forEach(([x,y],i)=>{const tr=document.createElement("tr");for(const value of [String(i+1).padStart(2,"0"),fmt(x,5),fmt(y,5),"0","✓ 包络检查通过"]){const td=document.createElement("td");td.textContent=value;tr.append(td);}$("coordinate-rows").append(tr);});
  $("plan-view").innerHTML=planSvg(scheme);state.viewer?.setModel(state.model,scheme);state.viewer?.reset();
  $("view-name").textContent=`${familyNames[scheme.request.family]} · ${summary.nozzle_count} 喷嘴`;
  $("workspace-caption").textContent=`${state.model.label} / ${scheme.request.region==="circle"?"圆形":"扇形"}安装区域 / R ${fmt(scheme.request.radius_mm)} mm`;
  $("viewer-status").textContent=`数量、间距、边界包络检查通过 · ${summary.nozzle_count} / ${scheme.request.N}`;
  $("result-note").textContent=`方案 ${scheme.scheme_id.slice(0,10)} · ${scheme.attempts} 组参数中找到 ${scheme.feasible_candidates} 个候选`;
  notice(`阵列已生成：${summary.nozzle_count} 个喷嘴。坐标与真实模型已同步，可查看二维排布或导出方案。`);availability();
}
function switchView(view){
  state.view=view;$("viewer").hidden=view!=="3d";$("plan-view").hidden=view!=="plan";
  $("view-3d").classList.toggle("selected",view==="3d");$("view-plan").classList.toggle("selected",view==="plan");
  $("view-guide").textContent=view==="3d"?"拖动旋转 · 滚轮缩放":"完整模型包络 · 编号与坐标表对应";
  state.viewer?.draw();
}
async function exportScheme(cad){
  if(!state.scheme)return;busy(true,cad?"正在复制实体并核对 STEP…":"正在保存方案…");
  try{const result=await api("/api/export",{scheme_id:state.scheme.scheme_id,cad});const a=document.createElement("a");a.href=result.download_url;a.download=result.filename;document.body.append(a);a.click();a.remove();notice(cad?"真实实体阵列已导出，已重新读取核对实体数量与总体积。下载包包含 STEP、坐标、排布图和参数。":"方案已导出，下载包包含坐标、二维排布图和完整参数。");}catch(error){notice(error.message,"error");}finally{busy(false);}
}
function download(url,name){const a=document.createElement("a");a.href=url;a.download=name;a.click();}
$("load-model").addEventListener("click",loadModel);
$("model-select").addEventListener("change",()=>{setSourceReference();invalidateModel();});
for(const id of ["axis","origin-x","origin-y","origin-z"])$(id).addEventListener("change",invalidateModel);
$("import-button").addEventListener("click",()=>$("model-file").click());
$("model-file").addEventListener("change",async event=>{
  const file=event.target.files[0];if(!file)return;
  if(file.size>20*1024*1024){notice("请选择不超过 20 MB 的 STEP 模型。","error");return;}
  busy(true,"正在导入模型…");try{const source=await api("/api/import",await file.arrayBuffer(),{"Content-Type":"application/octet-stream","X-Filename":encodeURIComponent(file.name)});state.sources=state.sources.filter(s=>s.source_id!==source.source_id);state.sources.push(source);sourceOptions(source.source_id);setSourceReference();invalidateModel();notice("文件已导入。核对轴线与参考点后，点击「载入模型」。","neutral");}catch(error){notice(error.message,"error");}finally{busy(false);event.target.value="";}
});
for(const id of ["count","region","radius","start-angle","sweep-angle","family","diameter","gap","wall","spacing-mode","spacing","rotation"]){$(id).addEventListener("input",()=>{
  $("sector-fields").hidden=$("region").value!=="sector";$("manual-fields").hidden=$("spacing-mode").value!=="manual";
  $("spacing").parentElement.hidden=!["honeycomb","rectangular"].includes($("family").value);
  $("spacing-hint").textContent=$("family").value==="sunflower"||$("family").value==="ring"?"向日葵／环形的中心距由几何分布决定；指定模式仅设置旋转角。":"自动模式检查有限组旋转与平移，优先保留边缘余量。";
  invalidate(true);
});}
$("design-form").addEventListener("submit",async event=>{
  event.preventDefault();if(!state.model||state.busy)return;
  let request;try{request=parameters();}catch(error){notice(error.message,"error");return;}
  invalidate();busy(true,"正在生成并检查阵列…");try{const scheme=await api("/api/generate",request);if(!scheme.feasible){notice(scheme.message,"error");$("result-note").textContent=scheme.search_scope;$("viewer-status").textContent="未找到满足当前约束的排布";}else display(scheme);}catch(error){notice(error.message,"error");}finally{busy(false);}
});
$("view-3d").addEventListener("click",()=>switchView("3d"));$("view-plan").addEventListener("click",()=>switchView("plan"));
$("reset-view").addEventListener("click",()=>state.viewer?.reset());
$("export-scheme").addEventListener("click",()=>exportScheme(false));$("export-cad").addEventListener("click",()=>exportScheme(true));
$("save-image").addEventListener("click",()=>{
  if(!state.scheme)return;
  if(state.view==="plan"){const blob=new Blob([planSvg(state.scheme)],{type:"image/svg+xml"});const url=URL.createObjectURL(blob);download(url,"nozzle_layout.svg");setTimeout(()=>URL.revokeObjectURL(url),1000);}
  else{state.viewer?.draw();download($("viewer").toDataURL("image/png"),"nozzle_assembly.png");}
});
async function init(){
  try{state.viewer=new NozzleViewer($("viewer"));}catch(error){notice(error.message,"error");return;}
  try{const response=await fetch("/api/bootstrap"),data=await response.json();state.sources=data.models;sourceOptions();setSourceReference();if(state.sources.length)await loadModel();else notice("导入单喷嘴 STEP，即可开始设计。","neutral");}catch(error){notice(error.message,"error");}
}
init();
