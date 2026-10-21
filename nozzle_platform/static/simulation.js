"use strict";
const meshFields=["mesh-min","mesh-max","mesh-inlet","mesh-distance","mesh-algorithm","mesh-curvature","mesh-passage"];
const flowFields=["flow-fuel","flow-air","flow-temperature","flow-oxygen","flow-iterations"];
function simulationAvailability(){
  $("mesh-fieldset").disabled=state.busy||!state.domain;
  $("flow-fieldset").disabled=state.busy||!state.mesh||!state.fluentAvailable;
  $("build-mesh").disabled=state.busy||!state.domain;
  $("run-flow").disabled=state.busy||!state.mesh||!state.fluentAvailable;
  $("view-mesh").disabled=state.busy||!state.mesh;
  $("export-mesh").disabled=state.busy||!state.mesh;$("export-flow").disabled=state.busy||!state.flow;
  $("stop-job").disabled=!state.job;
  $("mesh-state").textContent=state.mesh?"网格已检查":state.domain?"可生成网格":"先构建流体域";
}
function invalidateFlow(){state.flow=null;$("flow-results").hidden=true;}
function invalidateMesh(){
  invalidateFlow();state.mesh=null;$("mesh-results").hidden=true;
  if(state.view==="mesh")switchView("3d");
}
function numeric(id){const value=$(id).value.trim();if(!value||!Number.isFinite(Number(value)))throw new Error("请完整填写有限数值参数。");return Number(value);}
function parentIds(){return {domain_id:state.domain.domain_id,scheme_id:state.scheme.scheme_id};}
function cards(target,items){$(target).replaceChildren();for(const [label,value,unit] of items){const card=document.createElement("div"),name=document.createElement("span"),number=document.createElement("strong"),small=document.createElement("small");name.textContent=label;number.textContent=value;small.textContent=unit;card.append(name,number,small);$(target).append(card);}}
function displayMesh(mesh){
  state.mesh=mesh;$("mesh-results").hidden=false;
  $("mesh-summary").textContent=`${fmt(mesh.cells,0)} 个四面体 · ${fmt(mesh.nodes,0)} 个节点 · ${mesh.connected_fluid_components} 个连通流体域`;
  cards("mesh-cards",[["体单元数量",fmt(mesh.cells,0),"一阶四面体"],["最小 minSICN",fmt(mesh.minimum_gmsh_minSICN,4),"Gmsh 指标"],["CAD 体积差",fmt(mesh.relative_CAD_volume_error*100,3)+"%","小于 1%"],["边界检查","通过","四类边界完整，无未分类外表面"]]);
  switchView("mesh");availability();
}
function displayFlow(flow){
  state.flow=flow;$("flow-results").hidden=false;const last=flow.history.at(-1);
  $("flow-summary").textContent=`已计算 ${flow.iterations_completed} 步 · ${flow.numerical_criteria_met?"达到设定数值检查标准":"达到步数预算，尚未收敛"} · ${flow.polyhedral_conversion_performed?"已转换多面体":"四面体"}`;
  $("flow-summary").className=flow.numerical_criteria_met?"passed":"pending";
  cards("flow-cards",[["实际计算步数",flow.iterations_completed,"Fluent 保存文件核实"],["最小正交质量",fmt(flow.minimum_fluent_orthogonal_quality,4),"本模板筛选值 ≥ 0.1"],["最大长宽比",fmt(flow.maximum_fluent_aspect_ratio,2),"本模板筛选值 ≤ 100"],["连续通过批次",`${flow.convergence_assessment.consecutive_passing_blocks} / ${flow.convergence_assessment.required_consecutive_blocks}`,"每批次完整检查 25 步"]]);
  $("flow-table").replaceChildren();for(const [role,[name]] of Object.entries(domainRoleInfo)){const row=document.createElement("tr");for(const text of [name,last.mass_flow_kg_s[role].toExponential(6),last.area_weighted_total_pressure_drop_Pa[role]===undefined?"—":fmt(last.area_weighted_total_pressure_drop_Pa[role],5)]){const cell=document.createElement("td");cell.textContent=text;row.append(cell);}$("flow-table").append(row);}
  displayConvergence(flow.convergence_assessment);displayQualityHistory(flow);drawResiduals(flow.history);availability();
}
function displayConvergence(report){
  const labels={relative_mass_imbalance:"质量相对不平衡",h2_relative_convective_imbalance:"氢气对流不平衡",relative_pressure_drop_change:"相邻批次压降变化"};
  $("convergence-note").textContent=`至少 ${report.minimum_iterations} 步：${report.minimum_iterations_met?"已满足":"未满足"}；连续 ${report.required_consecutive_blocks} 个完整批次通过：${report.sustained_checks_met?"已满足":"未满足"}。残差检查使用批次内全部 25 步的最大值。`;
  $("convergence-checks").replaceChildren();
  for(const check of report.checks){
    const residual=check.name.startsWith("residual:"),label=residual?check.name.slice(9)+" 缩放残差":labels[check.name];
    const value=check.value===null?"等待下一批":residual?check.value.toExponential(3):fmt(check.value*100,5)+"%";
    const limit=(check.inclusive?"≤ ":"< ")+(residual?check.limit.toExponential(0):fmt(check.limit*100,2)+"%");
    const row=document.createElement("tr");for(const text of [label,value,limit,check.passed?"通过":"未通过"]){const cell=document.createElement("td");cell.textContent=text;row.append(cell);}row.lastChild.className=check.passed?"check-passed":"check-pending";$("convergence-checks").append(row);
  }
}
function displayQualityHistory(flow){
  $("quality-checks").replaceChildren();
  const names={imported_tetra:"导入四面体",direct_polyhedra:"直接转换多面体",restored_best:"恢复最佳网格"};
  for(const stage of flow.mesh_quality_history){const row=document.createElement("tr");for(const text of [names[stage.stage]||`内部改善 ${stage.improvement_passes}`,fmt(stage.minimum_orthogonal_quality,5),fmt(stage.maximum_aspect_ratio,2),stage.retained_as_best?"保留检查点":"未替换最佳网格"]){const cell=document.createElement("td");cell.textContent=text;row.append(cell);}$("quality-checks").append(row);}
}
function drawResiduals(history){
  const names=["continuity","x-velocity","y-velocity","z-velocity","energy","h2","o2","h2o"],colors=["#237b61","#3691c9","#c27c25","#9562b7","#be5462","#535fc0","#70872a","#4c9893"];
  const logs=history.flatMap(point=>names.map(name=>point.residuals[name]).filter(v=>v>0).map(Math.log10));
  if(!logs.length){$("residual-chart").textContent="各批次末步缩放残差均为 0。";return;}
  const high=Math.ceil(Math.max(...logs)),low=Math.min(high-1,Math.floor(Math.min(...logs))),maxIter=Math.max(1,history.at(-1).iteration);
  const x=i=>55+i/maxIter*650,y=v=>24+(high-Math.log10(Math.max(v,1e-20)))/(high-low)*180;
  let svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 740 245" role="img" aria-label="每批次末步的缩放残差，纵轴为对数刻度">';
  for(let tick=low;tick<=high;tick++){const py=y(10**tick);svg+=`<line x1="55" y1="${py}" x2="705" y2="${py}" stroke="#e0e8e4"/><text x="5" y="${py+4}" font-size="11" fill="#64786d">10^${tick}</text>`;}
  names.forEach((name,i)=>{const positive=history.filter(p=>p.residuals[name]>0);if(!positive.length)return;svg+=`<polyline fill="none" stroke="${colors[i]}" stroke-width="2" points="${positive.map(p=>`${x(p.iteration)},${y(p.residuals[name])}`).join(" ")}"/>`;for(const p of positive)svg+=`<circle cx="${x(p.iteration)}" cy="${y(p.residuals[name])}" r="2" fill="${colors[i]}"/>`;});
  svg+=`<text x="55" y="230" font-size="12" fill="#64786d">批次末步缩放残差 / 对数刻度（0 不绘入）</text><text x="630" y="230" font-size="12" fill="#64786d">${maxIter} 步</text></svg>`;
  $("residual-chart").innerHTML='<div class="residual-legend">'+names.map((n,i)=>`<span><i class="series-${i}"></i>${n}${history.every(p=>p.residuals[n]===0)?" = 0":""}</span>`).join("")+'</div>'+svg;
}
async function startSimulation(kind,request){
  busy(true,"正在创建本机任务…");
  $("job-diagnostics").hidden=true;$("job-diagnostics").textContent="";
  try{
    const job=await api(kind==="mesh"?"/api/mesh":"/api/coldflow",request);state.job=job.job_id;
    $("busy-overlay").hidden=true;$("job-panel").hidden=false;availability();
    for(;;){
      const response=await fetch("/api/jobs/"+state.job);const task=await response.json();if(!response.ok)throw new Error(task.error);
      $("job-message").textContent=task.message;
      if(task.status==="completed"){if(kind==="mesh")displayMesh(task.result);else displayFlow(task.result);notice(kind==="mesh"?"体网格已生成并通过几何、边界和质量检查。可导出网格或运行 Fluent 冷流。":"Fluent 冷流试算已结束，请查看实际数据与收敛状态。");break;}
      if(["failed","cancelled"].includes(task.status)){
        const diagnostics=task.diagnostics||{},parts=[task.message];
        if(diagnostics.minimum_fluent_orthogonal_quality!==undefined)parts.push(`保留网格：最低正交质量 ${fmt(diagnostics.minimum_fluent_orthogonal_quality,5)}，最大长宽比 ${fmt(diagnostics.maximum_fluent_aspect_ratio,2)}。`);
        if(diagnostics.iterations_completed)parts.push(`已保存至 ${diagnostics.iterations_completed} 步，未生成完整成功结果。`);
        $("job-diagnostics").textContent=parts.join(" ");$("job-diagnostics").hidden=false;
        throw new Error(task.message);
      }
      await new Promise(resolve=>setTimeout(resolve,1200));
    }
  }catch(error){notice(error.message,"error");$("job-message").textContent=error.message;}
  finally{state.job=null;busy(false);$("job-panel").hidden=true;}
}
async function exportSimulation(kind){
  const result=kind==="mesh"?state.mesh:state.flow;if(!result)return;
  busy(true,"正在整理结果包…");try{const exported=await api("/api/export-result",{...parentIds(),result_id:result.result_id});download(exported.download_url,exported.filename);notice("结果包已导出，包含来源、输入参数、检查记录和实际计算文件。");}catch(error){notice(error.message,"error");}finally{busy(false);}
}
function initializeSimulation(){
  for(const id of meshFields)$(id).addEventListener("input",()=>{invalidateMesh();availability();});
  for(const id of flowFields)$(id).addEventListener("input",()=>{invalidateFlow();availability();});
  $("simulation-form").addEventListener("submit",event=>{event.preventDefault();if(state.busy||!state.domain)return;try{const request={...parentIds(),min_size_mm:numeric("mesh-min"),max_size_mm:numeric("mesh-max"),inlet_size_mm:numeric("mesh-inlet"),refine_distance_mm:numeric("mesh-distance"),algorithm:$("mesh-algorithm").value,curvature_samples:numeric("mesh-curvature"),passage_size_mm:numeric("mesh-passage")};invalidateMesh();startSimulation("mesh",request);}catch(error){notice(error.message,"error");}});
  $("run-flow").addEventListener("click",()=>{if(state.busy||!state.mesh)return;try{const request={...parentIds(),mesh_id:state.mesh.result_id,fuel_velocity_m_s:numeric("flow-fuel"),oxidizer_velocity_m_s:numeric("flow-air"),temperature_K:numeric("flow-temperature"),oxidizer_o2_mass_fraction:numeric("flow-oxygen"),iterations:numeric("flow-iterations")};invalidateFlow();startSimulation("flow",request);}catch(error){notice(error.message,"error");}});
  $("stop-job").addEventListener("click",async()=>{if(!state.job)return;try{await api("/api/stop-job",{job_id:state.job});$("stop-job").disabled=true;$("job-message").textContent="正在停止并清理本次任务…";}catch(error){notice(error.message,"error");}});
  $("view-mesh").addEventListener("click",()=>switchView("mesh"));
  $("export-mesh").addEventListener("click",()=>exportSimulation("mesh"));$("export-flow").addEventListener("click",()=>exportSimulation("flow"));
}
