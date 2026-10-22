"use strict";
const levelNames={coarse:"粗网格",medium:"中网格",fine:"细网格"};
function invalidateStudy(){state.study=null;$("study-results").hidden=true;}
function meshValues(){const result={min_size_mm:numeric("mesh-min"),max_size_mm:numeric("mesh-max"),inlet_size_mm:numeric("mesh-inlet"),refine_distance_mm:numeric("mesh-distance"),algorithm:$("mesh-algorithm").value,curvature_samples:numeric("mesh-curvature"),passage_size_mm:numeric("mesh-passage"),size_factor:numeric("mesh-factor"),cell_budget:numeric("mesh-budget"),mesh_method:$("mesh-method").value};if(result.mesh_method==="layered-polyhex")Object.assign(result,{layer_first_height_mm:numeric("mesh-layer-first"),layer_count:numeric("mesh-layer-count"),layer_thickness_mm:numeric("mesh-layer-total"),local_volume_refinement:$("mesh-local-volume").checked});return result;}
function flowValues(){return {fuel_velocity_m_s:numeric("flow-fuel"),oxidizer_velocity_m_s:numeric("flow-air"),temperature_K:numeric("flow-temperature"),oxidizer_o2_mass_fraction:numeric("flow-oxygen"),iterations:numeric("flow-iterations"),convergence_mode:$("flow-mode").value,time_budget_seconds:numeric("flow-time-budget"),compute_device:$("flow-device").value,transport_model:$("flow-transport").value};}
async function loadStudyLevel(row){
  busy(true,"正在读取所选档位的实际结果…");
  try{
    state.flow=null;$("flow-results").hidden=true;
    if(row.mesh_id){const response=await fetch("/api/jobs/"+row.mesh_id),task=await response.json();if(!response.ok||!task.result)throw Error(task.error||"该档位没有可用网格结果。");displayMesh(task.result);$("mesh-summary").textContent=levelNames[row.level]+" · "+$("mesh-summary").textContent;}
    if(row.flow_id){const response=await fetch("/api/jobs/"+row.flow_id),task=await response.json();if(!response.ok||!task.result)throw Error(task.error||"该档位没有完整冷流结果。");displayFlow(task.result);}
    notice("正在查看"+levelNames[row.level]+(row.flow_id?"的实际网格与冷流数据。":"的实际网格；该档求解任务未完成。"));
  }catch(error){notice(error.message,"error");}finally{busy(false);}
}
function displayStudy(study){
  state.study=study;$("study-results").hidden=false;
  const c=study.comparison,uniform=study.config?.refinement_mode==="uniform",passed=uniform?c.scoped_grid_convergence_passed:c.pressure_sensitivity_passed;
  $("study-summary").textContent=(passed?(uniform?"本工况所检压降与采样场的网格收敛检查通过":"本次两路压降敏感性检查通过"):"网格收敛证据尚不充分")+" · "+(c.reasons.length?c.reasons.join(" "):uniform?"建议保留细网格；范围限于本次冷流工况及所检指标。":"在所测三档范围内，可选用中网格继续工程试算。")+" 全部内部流场及燃烧工况仍需另行验证。";
  $("study-summary").className=passed?"check-passed":"check-pending";
  cards("study-cards",[["达到数值标准",`${study.rows.filter(r=>r.numerical_criteria_met).length} / 3`,"每档单独检查收敛"],["实际相邻细化比",c.effective_refinement_ratios.length?c.effective_refinement_ratios.map(r=>fmt(r,3)).join(" / "):"—","按 Fluent 单元数和体积；均需 ≥ 1.1"],["指标变化容许值",fmt(c.tolerance_percent,2)+"%","粗→中、中→细均检查"],["建议后续试算网格",c.recommended_level?levelNames[c.recommended_level]:"待定","限本工况所检指标"]]);
  $("study-rows").replaceChildren();
  for(const row of study.rows){
    const status=row.status!=="completed"?(row.status==="mesh_failed"?"网格未通过":row.status==="mesh_ready"?"网格完成，未求解":"求解任务未完成"):row.numerical_criteria_met?"数值达标":"尚未收敛";
    const values=[levelNames[row.level],row.input_mesh_cells?fmt(row.input_mesh_cells,0):"—",row.solver_cells?fmt(row.solver_cells,0):"—",row.iterations??"—",fmt(row.minimum_orthogonal_quality,4),fmt(row.maximum_aspect_ratio,2),fmt(row.pressure_drop_Pa?.fuel_inlet,6),fmt(row.pressure_drop_Pa?.oxidizer_inlet,6),fmt((row.mesh_seconds||0)+(row.flow_seconds||0),1),status];
    const tr=document.createElement("tr");for(const value of values){const td=document.createElement("td");td.textContent=value;tr.append(td);}
    const action=document.createElement("td");if(row.mesh_id){const button=document.createElement("button");button.type="button";button.textContent="查看档位";button.className="study-level-button";button.addEventListener("click",async()=>{if(state.busy)return;const saved=state.study;await loadStudyLevel(row);state.study=saved;availability();});action.append(button);}tr.append(action);$("study-rows").append(tr);
  }
  $("study-metrics").replaceChildren();
  if(c.mesh_preflight){const p=document.createElement("p");p.textContent="原生网格预检细化比："+(c.mesh_preflight.effective_refinement_ratios.length?c.mesh_preflight.effective_refinement_ratios.map(r=>fmt(r,4)).join(" / "):"—")+"；"+(c.mesh_preflight.passed?"通过，仍需核对最终求解结果。":c.mesh_preflight.reasons.join(" "));$("study-metrics").append(p);}
  for(const row of study.rows){if(row.status!=="completed"&&row.message){const p=document.createElement("p");p.textContent=levelNames[row.level]+"："+row.message;$("study-metrics").append(p);}}
  for(const [role,label] of [["fuel_inlet","氢气入口"],["oxidizer_inlet","空气入口"]]){
    const metric=c.metrics[role];if(!metric)continue;
    const p=document.createElement("p");p.textContent=`${label}压降：粗→中变化 ${fmt(metric.adjacent_changes_percent[0],4)}%，中→细变化 ${fmt(metric.adjacent_changes_percent[1],4)}%；末两批次最大压降漂移 ${fmt(metric.iteration_drift_percent,5)}%（需 ≤ ${fmt(metric.iteration_drift_limit_percent,3)}%）。`;
    $("study-metrics").append(p);
  }
  if(c.outlet_fields?.metrics){for(const [name,metric] of Object.entries(c.outlet_fields.metrics)){const p=document.createElement("p");p.textContent=(name==="velocity"?"出口速度矢量":"出口氢气质量分数")+"共同采样相邻 L2 变化："+metric.adjacent_relative_l2_percent.map(v=>fmt(v,4)+"%").join(" / ");$("study-metrics").append(p);}}
  if(c.interior_fields?.sections){for(const [z,section] of Object.entries(c.interior_fields.sections)){for(const [name,metric] of Object.entries(section.metrics||{})){const p=document.createElement("p");p.textContent="内部 Z="+fmt(Number(z)*1000,2)+" mm "+(name==="velocity"?"速度矢量":"氢气质量分数")+"相邻 L2 变化："+metric.adjacent_relative_l2_percent.map(v=>fmt(v,4)+"%").join(" / ");$("study-metrics").append(p);}}}
  if(c.gci){for(const [role,g] of Object.entries(c.gci)){const p=document.createElement("p");p.textContent=(role==="fuel_inlet"?"氢气":"空气")+"压降 GCI："+(g.available?fmt(g.fine_gci_percent,4)+"%，观测阶 "+fmt(g.observed_order,3)+"；迭代噪声分离"+(g.iteration_noise_separated?"通过":"未通过"):g.reason);$("study-metrics").append(p);}}
  drawStudy(study.rows);availability();
}
function drawStudy(rows){
  $("study-chart").replaceChildren();
  const usable=rows.filter(r=>r.status==="completed"&&r.solver_cells&&r.pressure_drop_Pa);
  if(!usable.length)return;
  for(const [role,label,color] of [["fuel_inlet","氢气入口总压差 / Pa","#9865b5"],["oxidizer_inlet","空气入口总压差 / Pa","#237b61"]]){
    const values=usable.map(r=>r.pressure_drop_Pa[role]),lo=Math.min(...values),hi=Math.max(...values),pad=Math.max((hi-lo)*.2,Math.abs(hi)*1e-5,1e-6);
    const maxCells=Math.max(...usable.map(r=>r.solver_cells));const x=n=>60+n/maxCells*370,y=v=>36+(hi+pad-v)/(hi-lo+2*pad)*175;
    const div=document.createElement("div");
    let svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 275" role="img" aria-label="${label}与实际求解单元数"><text x="60" y="18" font-size="12" fill="#526e63">${label}</text>`;
    for(let i=0;i<=3;i++){const v=lo-pad+(hi-lo+2*pad)*i/3;svg+=`<line x1="60" y1="${y(v)}" x2="445" y2="${y(v)}" stroke="#e0e8e4"/><text x="3" y="${y(v)+4}" font-size="10" fill="#64786d">${v.toPrecision(5)}</text>`;}
    svg+=`<polyline fill="none" stroke="${color}" stroke-width="2" points="${usable.map(r=>`${x(r.solver_cells)},${y(r.pressure_drop_Pa[role])}`).join(" ")}"/>`;
    for(const r of usable)svg+=`<circle cx="${x(r.solver_cells)}" cy="${y(r.pressure_drop_Pa[role])}" r="4" fill="${color}"/><text x="${x(r.solver_cells)-12}" y="${y(r.pressure_drop_Pa[role])+(r.level==="medium"?20:-12)}" font-size="11" fill="${color}">${levelNames[r.level]}</text>`;
    svg+=`<text x="60" y="245" font-size="11" fill="#64786d">横轴：实际求解单元数（0 至 ${fmt(maxCells,0)}）</text><text x="60" y="263" font-size="10" fill="#64786d">纵轴局部放大，观察网格变化趋势</text></svg>`;
    div.innerHTML=svg;$("study-chart").append(div);
  }
}
function initializeStudy(){
  for(const id of ["study-ratio","study-tolerance","study-mode"])$(id).addEventListener("input",()=>{invalidateStudy();availability();});
  $("run-study").addEventListener("click",()=>{if(state.busy||!state.domain)return;try{if($("study-mode").value==="uniform")$("flow-mode").value="grid-verification";const request={...parentIds(),mesh:meshValues(),flow:flowValues(),refinement_ratio:numeric("study-ratio"),tolerance_percent:numeric("study-tolerance"),refinement_mode:$("study-mode").value};invalidateMesh();startSimulation("study",request);}catch(error){notice(error.message,"error");}});
  $("export-study").addEventListener("click",()=>exportSimulation("study"));
}
