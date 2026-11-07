"use strict";
const geometryWorkspace={comparison:null,loadingLibrary:false};
function clearGeometryComparison(){geometryWorkspace.comparison=null;$("geometry-comparison").hidden=true;$("geometry-candidates").replaceChildren();}
function geometryAvailability(){
 $("compare-designs").disabled=state.busy||!state.model;$("geometry-library-save").disabled=state.busy||!state.scheme;$("geometry-library-refresh").disabled=state.busy;
 $("geometry-compare-export").disabled=state.busy||!geometryWorkspace.comparison;
 for(const b of document.querySelectorAll("#geometry-candidates button,#geometry-library-rows button"))b.disabled=state.busy||b.dataset.unavailable==="true";
 if(state.geometryOnly)for(const id of ["build-domain","build-mesh","run-flow","run-study","run-pipeline","view-domain","view-mesh"])if($(id))$(id).disabled=true;
}
function geometryNode(tag,text){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;}
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
 $("geometry-library-rows").replaceChildren();const seen=new Map();
 $("geometry-library-note").textContent=data.total_records?`共 ${data.total_records} 条记录，最多显示最近 ${data.display_limit} 条。完整几何保存在本机，源 STEP 缺失或文件核验失败时禁止载入。`:"还没有保存的几何方案。生成或采用排布后可保存，供队员回放。";
 for(const entry of data.entries){
  const row=geometryNode("article");row.className="geometry-library-row";const description=geometryNode("div");description.append(geometryNode("h4",entry.name));
  if(entry.available){description.append(geometryNode("p",`${entry.N} 个 · ${familyNames[entry.family]} · ${entry.region==="circle"?"圆形":"扇形"} R=${fmt(entry.radius_mm)} mm · ${entry.model_name}`));if(seen.has(entry.geometry_key))description.append(geometryNode("p","与另一记录坐标集合相同，各自名称及调整记录仍保留。"));else seen.set(entry.geometry_key,entry.entry_id);}
  else{const error=geometryNode("p",entry.error);error.className="geometry-library-error";description.append(error);}
  const actions=geometryNode("div");actions.className="geometry-library-actions";
  for(const [label,action] of [["载入几何",()=>loadGeometryEntry(entry.entry_id)],["导出记录包",()=>exportGeometryEntry(entry.entry_id)]]){const button=geometryNode("button",label);button.type="button";button.className="button secondary";button.dataset.entryId=entry.entry_id;button.dataset.unavailable=String(!entry.available);button.disabled=!entry.available||state.busy;button.addEventListener("click",action);actions.append(button);}
  row.append(description,actions);$("geometry-library-rows").append(row);
 }
 geometryAvailability();
}
async function loadGeometryEntry(key){
 if(state.busy)return;busy(true,"正在恢复保存的模型参考坐标与几何排布…");
 try{
  const data=await api("/api/geometry-library-load",{entry_id:key});invalidateModel();state.sources=data.models;sourceOptions(data.model.source_id);$("axis").value=data.model.axis;installModel(data.model);
  const fields={N:"count",family:"family",region:"region",radius_mm:"radius",start_angle_deg:"start-angle",sweep_angle_deg:"sweep-angle",diameter_mm:"diameter",edge_gap_mm:"gap",wall_clearance_mm:"wall",spacing_mode:"spacing-mode",spacing_mm:"spacing",rotation_deg:"rotation"};
  for(const [name,id] of Object.entries(fields))$(id).value=data.scheme.request[name];$("sector-fields").hidden=data.scheme.request.region!=="sector";$("manual-fields").hidden=data.scheme.request.spacing_mode!=="manual";
  display(data.scheme);switchView("3d");$("geometry-save-name").value=data.entry.name;
  notice(`已载入“${data.entry.name}”，保存坐标保持原样，未重新优化或启动 Fluent。后续新仿真将按当前算法重新生成，请另行核对。`);
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
 $("geometry-library-save").addEventListener("click",async()=>{if(state.busy||!state.scheme)return;busy(true,"正在保存并核验几何方案…");try{const data=await api("/api/geometry-library-save",{scheme_id:state.scheme.scheme_id,name:$("geometry-save-name").value});await refreshGeometryLibrary();notice(data.already_saved?"同名同内容记录已保存，未重复写入。":"几何方案已保存，平台重启后可从方案库恢复。");}catch(e){notice(e.message,"error");}finally{busy(false);}});
}
