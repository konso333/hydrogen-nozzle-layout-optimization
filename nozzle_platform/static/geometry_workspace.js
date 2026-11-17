"use strict";
const geometryWorkspace={comparison:null,loadingLibrary:false,selectedEntries:new Set(),savedComparison:null};
function clearGeometryComparison(){geometryWorkspace.comparison=null;$("geometry-comparison").hidden=true;$("geometry-candidates").replaceChildren();}
function geometryAvailability(){
 $("compare-designs").disabled=state.busy||!state.model;$("geometry-library-save").disabled=state.busy||!state.scheme;$("geometry-library-refresh").disabled=state.busy;
 $("geometry-compare-export").disabled=state.busy||!geometryWorkspace.comparison;
 $("geometry-to-simulation").disabled=state.busy||!state.scheme;
 $("geometry-library-import").disabled=state.busy;
 $("geometry-library-compare").disabled=state.busy||geometryWorkspace.selectedEntries.size<2;
 $("geometry-saved-export").disabled=state.busy||!geometryWorkspace.savedComparison;
 for(const b of document.querySelectorAll("#geometry-candidates button,#geometry-library-rows button,#geometry-saved-cards button"))b.disabled=state.busy||b.dataset.unavailable==="true";
 for(const box of document.querySelectorAll("#geometry-library-rows input[type=checkbox]"))box.disabled=state.busy||box.dataset.unavailable==="true"||(!box.checked&&geometryWorkspace.selectedEntries.size>=4);
 if(state.geometryOnly)for(const id of ["build-domain","build-mesh","run-flow","run-study","run-pipeline","view-domain","view-mesh"])if($(id))$(id).disabled=true;
}
function geometryNode(tag,text){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;}
function setGeometryEntryAddress(key){
 if(state.beginner)return;
 const address=new URL(location.href);address.searchParams.set("entry",key);history.replaceState(null,"",address);
}
function displayGeometryComparison(result){
 geometryWorkspace.comparison=result;$("geometry-comparison").hidden=false;$("geometry-candidates").replaceChildren();
 $("geometry-compare-summary").textContent=`目标 N=${result.requested_design.N} · ${result.requested_design.region==="circle"?"圆形":"扇形"}半径 ${fmt(result.requested_design.radius_mm)} mm · 已找到 ${result.rows.filter(x=>x.feasible).length} 种可行排列，${result.unique_coordinate_sets} 组不同坐标。${result.scope}`;
 for(const row of result.rows){
  const card=geometryNode("article");card.className="geometry-candidate";card.append(geometryNode("h4",familyNames[row.family]+(row.feasible?" · 几何可行":" · 本次未找到")));
  if(row.feasible){const preview=geometryNode("div");preview.innerHTML=planSvg(row.scheme);card.append(preview);
   card.append(geometryNode("p",`实际 ${row.N} 个 · 最小中心距 ${fmt(row.summary.minimum_center_distance_mm)} mm · 净间隙 ${fmt(row.summary.minimum_edge_gap_mm)} mm · 最小边界余量 ${fmt(row.summary.minimum_wall_clearance_mm)} mm`));
   if(row.same_coordinates_as)card.append(geometryNode("p",`坐标集合与${familyNames[row.same_coordinates_as]}相同，编号顺序可能不同。`));
   const suggestion=result.suggestions.find(s=>s.request.family===row.family),button=geometryNode("button","采用此排布并查看三维");button.type="button";button.className="button secondary";button.dataset.family=row.family;button.addEventListener("click",()=>adoptComparedGeometry(result,suggestion));card.append(button);
  }else card.append(geometryNode("p",row.reason+" 有限构造未找到不等于全局无解。"));
  $("geometry-candidates").append(card);
 }
}
async function adoptComparedGeometry(result,suggestion){
 if(state.busy||geometryWorkspace.comparison!==result)return;
 try{
  if(JSON.stringify(parameters())!==JSON.stringify(result.requested_design))throw Error("参数已改变，请重新比较。");
  busy(true,"正在复核所选几何排布…");const scheme=await api("/api/design-assist-adopt",{advice_id:result.advice_id,suggestion_id:suggestion.suggestion_id,current_design:parameters()});
  invalidate();$("family").value=scheme.request.family;display(scheme);switchView("3d");notice(`已采用${familyNames[scheme.request.family]}，N=${scheme.request.N} 和全部几何约束保持不变。可保存到几何方案库；未启动 Fluent。`);
 }catch(e){notice(e.message,"error");}finally{busy(false);}
}
async function refreshGeometryLibrary(){
 const response=await fetch("/api/geometry-library",{cache:"no-store"}),data=await response.json();if(!response.ok)throw Error(data.error||"方案库读取失败");
 clearSavedGeometryComparison();
 const available=new Set(data.entries.filter(entry=>entry.available).map(entry=>entry.entry_id));
 for(const key of geometryWorkspace.selectedEntries)if(!available.has(key))geometryWorkspace.selectedEntries.delete(key);
 $("geometry-library-rows").replaceChildren();const seen=new Map();
 $("geometry-library-note").textContent=data.total_records?`共 ${data.total_records} 条记录，最多显示最近 ${data.display_limit} 条。完整几何保存在本机，源 STEP 缺失或文件核验失败时禁止载入。`:"还没有保存的几何方案。生成或采用排布后可保存，供队员回放。";
 for(const entry of data.entries){
  const row=geometryNode("article");row.className="geometry-library-row";const description=geometryNode("div");description.append(geometryNode("h4",entry.name));
  if(entry.available){description.append(geometryNode("p",`${entry.N} 个 · ${familyNames[entry.family]} · ${entry.region==="circle"?"圆形":"扇形"} R=${fmt(entry.radius_mm)} mm · ${entry.model_name}`));if(seen.has(entry.geometry_key))description.append(geometryNode("p","与另一记录坐标集合相同，各自名称及调整记录仍保留。"));else seen.set(entry.geometry_key,entry.entry_id);}
  else{const error=geometryNode("p",entry.error);error.className="geometry-library-error";description.append(error);}
  const actions=geometryNode("div");actions.className="geometry-library-actions";
  for(const [label,action] of [["载入几何",()=>loadGeometryEntry(entry.entry_id)],["导出记录包",()=>exportGeometryEntry(entry.entry_id)]]){const button=geometryNode("button",label);button.type="button";button.className="button secondary";button.dataset.entryId=entry.entry_id;button.dataset.unavailable=String(!entry.available);button.disabled=!entry.available||state.busy;button.addEventListener("click",action);actions.append(button);}
  const selection=geometryNode("label");selection.className="geometry-library-select";
  const checkbox=geometryNode("input");checkbox.type="checkbox";checkbox.dataset.entryId=entry.entry_id;checkbox.dataset.unavailable=String(!entry.available);checkbox.checked=geometryWorkspace.selectedEntries.has(entry.entry_id);checkbox.setAttribute("aria-label",`比较方案：${entry.name}`);
  checkbox.addEventListener("change",()=>{
   if(checkbox.checked&&geometryWorkspace.selectedEntries.size>=4){checkbox.checked=false;return;}
   if(checkbox.checked)geometryWorkspace.selectedEntries.add(entry.entry_id);else geometryWorkspace.selectedEntries.delete(entry.entry_id);
   clearSavedGeometryComparison();geometryAvailability();
  });
  selection.append(checkbox,geometryNode("span","加入比较"));description.append(selection);
  row.append(description,actions);$("geometry-library-rows").append(row);
 }
 geometryAvailability();
}
function clearSavedGeometryComparison(){
 geometryWorkspace.savedComparison=null;$("geometry-saved-comparison").hidden=true;
 $("geometry-saved-cards").replaceChildren();$("geometry-saved-table").replaceChildren();
}
function displaySavedGeometryComparison(result){
 geometryWorkspace.savedComparison=result;$("geometry-saved-comparison").hidden=false;
 $("geometry-saved-note").textContent=(result.warnings.length?`比较条件有差异：${result.warnings.join("；")}。`:"模型、实际数量、安装边界和包络／间隙约束一致。")+"相同燃料热输入尚未核对。"+result.scope;
 const table=$("geometry-saved-table"),head=geometryNode("thead"),titles=geometryNode("tr");
 titles.append(geometryNode("th","比较项目"));
 for(const row of result.rows)titles.append(geometryNode("th",row.name));
 head.append(titles);table.append(head);const body=geometryNode("tbody");
 const fields=[
  ["模型",r=>r.model_name],["原目标数量（记录）",r=>String(r.original_N)],["实际喷嘴数量",r=>String(r.N)],
  ["排列",r=>familyNames[r.family]],["安装区域",r=>r.region==="circle"?"圆形":`${fmt(r.sweep_angle_deg)}° 扇形，起始 ${fmt(r.start_angle_deg)}°`],
  ["原目标半径 / mm",r=>fmt(r.original_radius_mm)],["实际半径 / mm",r=>fmt(r.radius_mm)],
  ["安装面积 / mm²",r=>fmt(r.boundary_area_mm2)],["包络直径 / mm",r=>fmt(r.diameter_mm)],
  ["要求净间隙 / mm",r=>fmt(r.edge_gap_mm)],["要求边界余量 / mm",r=>fmt(r.wall_clearance_mm)],
  ["实际最小中心距 / mm",r=>fmt(r.minimum_center_distance_mm)],["实际最小净间隙 / mm",r=>fmt(r.minimum_edge_gap_mm)],
  ["实际最小边界余量 / mm",r=>fmt(r.minimum_wall_clearance_mm)],
  ["相同坐标记录",r=>r.same_coordinates_as?(result.rows.find(x=>x.entry_id===r.same_coordinates_as)||{}).name:"—"]
 ];
 for(const [label,value] of fields){const tr=geometryNode("tr");tr.append(geometryNode("th",label));for(const row of result.rows)tr.append(geometryNode("td",value(row)));body.append(tr);}
 table.append(body);
 for(const record of result.records){
  const card=geometryNode("article");card.className="geometry-candidate";card.append(geometryNode("h4",record.entry.name));
  const image=geometryNode("div");image.innerHTML=planSvg(record.scheme);card.append(image);
  card.append(geometryNode("p",`${record.scheme.request.N} 个 · ${familyNames[record.scheme.request.family]} · 坐标沿用保存记录`));
  const load=geometryNode("button","载入此方案");load.type="button";load.className="button secondary";load.addEventListener("click",()=>loadGeometryEntry(record.entry.entry_id));card.append(load);$("geometry-saved-cards").append(card);
 }
 geometryAvailability();$("geometry-saved-comparison").scrollIntoView({block:"start"});
}
async function loadGeometryEntry(key){
 if(state.busy)return;busy(true,"正在恢复保存的模型参考坐标与几何排布…");
 try{
  const data=await api("/api/geometry-library-load",{entry_id:key});invalidateModel();state.sources=data.models;sourceOptions(data.model.source_id);$("axis").value=data.model.axis;installModel(data.model);
  applyDesignFields(data.scheme.request);
  display(data.scheme);switchView(state.viewer?"3d":"plan");$("geometry-save-name").value=data.entry.name;
  state.selectedGeometry=data.scheme.scheme_id;
  setGeometryEntryAddress(key);
  notice(state.geometryOnly?`已载入“${data.entry.name}”，可以继续修改、比较或导出。`:`已载入“${data.entry.name}”。方案 1 将使用保存的坐标，请设置并核对燃烧室、供气与工况。`);
  return data;
 }catch(e){notice(e.message,"error");}finally{busy(false);}
}
async function exportGeometryEntry(key){
 if(state.busy)return;busy(true,"正在核对并打包几何记录…");try{const data=await api("/api/geometry-library-export",{entry_id:key});download(data.download_url,data.filename);notice("几何记录包已导出，包含原单喷嘴 STEP、参考坐标与排布；不含网格或 CFD。");}catch(e){notice(e.message,"error");}finally{busy(false);}
}
function initializeGeometryWorkspace(){
 if(state.geometryOnly){document.body.classList.add("geometry-only");if(!state.beginner){$("geometry-intro").hidden=false;$("workspace-mode").href="/?mode=advanced";$("workspace-mode").textContent="切换到仿真工作流 ↗";document.querySelector(".workspace-heading h2").textContent="多喷嘴几何设计与方案比较";document.querySelector(".sidebar-title>p").textContent="先完成排布、装配与方案整理，不运行 Fluent。";document.querySelector(".workspace-heading .version").textContent="仅几何设计";}}
 $("compare-designs").addEventListener("click",async()=>{if(state.busy||!state.model)return;try{const request=parameters();invalidate();busy(true,"正在比较四种排列并核验坐标…");const result=await api("/api/design-compare",{design:request});displayGeometryComparison(result);$("geometry-comparison").scrollIntoView({block:"start"});notice(result.rows.some(x=>x.feasible)?"比较完成。请选择一项几何排布查看三维；没有运行 Fluent。":"四种排列均未取得目标数量，可点击智能生成查看原因与调整建议；没有运行 Fluent。");}catch(e){notice(e.message,"error");}finally{busy(false);}});
 $("geometry-compare-export").addEventListener("click",async()=>{const result=geometryWorkspace.comparison;if(state.busy||!result)return;try{if(JSON.stringify(parameters())!==JSON.stringify(result.requested_design))throw Error("参数已改变，请重新比较。");busy(true,"正在打包几何比较表与排布…");const reply=await api("/api/design-compare-export",{advice_id:result.advice_id,current_design:parameters()});download(reply.download_url,reply.filename);notice("几何比较包已导出，包含同条件CSV、坐标与二维图，不含CFD结果。");}catch(e){notice(e.message,"error");}finally{busy(false);}});
 $("geometry-library-refresh").addEventListener("click",()=>refreshGeometryLibrary().catch(e=>notice(e.message,"error")));
 $("geometry-library-compare").addEventListener("click",async()=>{
  if(state.busy||geometryWorkspace.selectedEntries.size<2)return;
  clearSavedGeometryComparison();busy(true,"正在核对已保存的坐标与比较条件…");
  try{const result=await api("/api/geometry-library-compare",{entry_ids:[...geometryWorkspace.selectedEntries]});displaySavedGeometryComparison(result);notice("已保存方案比较完成。当前设计没有改变，未运行 Fluent。");}
  catch(e){notice(e.message,"error");}finally{busy(false);}
 });
 $("geometry-saved-export").addEventListener("click",async()=>{
  const result=geometryWorkspace.savedComparison;if(state.busy||!result)return;
  busy(true,"正在核对并导出已保存方案比较包…");
  try{const data=await api("/api/geometry-library-compare-export",{entry_ids:result.entry_ids,comparison_id:result.comparison_id});download(data.download_url,data.filename);notice("比较包已导出，包含条件差异、几何指标、原坐标和二维图。");}
  catch(e){notice(e.message,"error");}finally{busy(false);}
 });
 for(const id of ["geometry-library-import","beginner-open-record"])$(id).addEventListener("click",()=>$("geometry-library-file").click());
 $("geometry-library-file").addEventListener("change",async event=>{
  const file=event.target.files[0];event.target.value="";
  if(!file||state.busy)return;
  const report=(message,error=false)=>state.beginner?beginnerStatus(message,error?"error":""):notice(message,error?"error":"");
  if(file.size>20*1024*1024){report("请选择不超过 20 MB 的几何记录 ZIP。",true);return;}
  busy(true,"正在核对并导入几何记录包…");
  report("正在核对并导入几何记录包…");
  let imported=null;
  try{
   const data=await api("/api/geometry-library-import",await file.arrayBuffer(),{"Content-Type":"application/zip"});
   await refreshGeometryLibrary();
   imported=data;
  }catch(error){report(error.message,true);}finally{busy(false);}
  if(!imported)return;
  if(state.beginner){location.assign("/?mode=geometry&entry="+encodeURIComponent(imported.entry.entry_id));return;}
  const loaded=await loadGeometryEntry(imported.entry.entry_id);
  if(loaded)notice((imported.already_saved?"方案已存在，已载入。":"记录包已导入，已载入方案。")+"原坐标、模型参考点和调整记录已恢复。");
 });
 $("geometry-to-simulation").addEventListener("click",async()=>{
  if(state.busy||!state.scheme)return;
  busy(true,"正在保存排布并准备仿真配置…");
  try{
   const name=$("geometry-save-name").value.trim()||`排布 · ${state.scheme.request.N} 个 · ${familyNames[state.scheme.request.family]}`;
   const saved=await api("/api/geometry-library-save",{scheme_id:state.scheme.scheme_id,name});
   location.assign("/?mode=advanced&entry="+encodeURIComponent(saved.entry.entry_id));
  }catch(e){notice(e.message,"error");}finally{busy(false);}
 });
 $("geometry-library-save").addEventListener("click",async()=>{
  if(state.busy||!state.scheme)return;
  busy(true,"正在保存并核验几何方案…");
  try{
   const data=await api("/api/geometry-library-save",{scheme_id:state.scheme.scheme_id,name:$("geometry-save-name").value});
   state.selectedGeometry=state.scheme.scheme_id;
   setGeometryEntryAddress(data.entry.entry_id);
   await refreshGeometryLibrary();
   const saved=data.already_saved?"同名同内容记录已保存。":"几何方案已保存。";
   notice(saved+(state.geometryOnly?"刷新此页面可恢复这份排布，也可从方案库载入。":"可从方案库恢复这份排布。"));
  }catch(e){notice(e.message,"error");}finally{busy(false);}
 });
}
