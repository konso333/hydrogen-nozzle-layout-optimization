"use strict";
const pipelineState={task:null,watch:0,previewKey:null};
const pipelineStageNames={design:"阵列检查",assembly:"三维装配",domain:"流体域",mesh:"体网格",flow:"Fluent 冷流",maps:"仿真场图"};
const pipelineStatusNames={queued:"等待运行",running:"正在运行",completed:"完成",not_converged:"计算结束 · 未收敛",completed_with_map_error:"计算结束 · 场图未取得",failed:"失败",cancelled:"已停止",interrupted:"运行被中断",completed_with_failures:"部分方案失败"};
function pipelineAvailability(){
 const ready=!!state.model&&$("template-confirmed").checked&&state.fluentAvailable;
 const active=state.busy&&pipelineState.task&&["queued","running"].includes(pipelineState.task.status);
 $("run-pipeline").disabled=state.busy?!active:!ready;$("run-pipeline").textContent=active?"查看运行进度 ↗":"一键运行 ↗";
 $("pipeline-ready").textContent=state.busy?"任务正在执行，参数已锁定。可在下方查看进度或停止。":!state.model?"先导入并载入单喷嘴模型。":!$("template-confirmed").checked?"请在公共流体域设置中确认供气模板与实际模型一致。":!state.fluentAvailable?"未找到本机 Fluent，可继续设计及导出阵列。":"沿用左侧当前参数，自动生成阵列、装配、流体域、网格和冷流结果。";
 $("pipeline-refresh").disabled=false;
 $("pipeline-history").disabled=state.busy;
 if(state.busy&&state.job&&state.job!==pipelineState.task?.job_id)$("pipeline-refresh").disabled=true;
 $("pipeline-extra").hidden=$("pipeline-basis").value==="single";$("pipeline-heat").hidden=$("pipeline-basis").value!=="same-heat";
 $("pipeline-third-fields").hidden=!$("pipeline-third").checked;
}
function pipelineNode(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
async function pipelineGet(path){const r=await fetch(path,{cache:"no-store"}),v=await r.json();if(!r.ok)throw Error(v.error||"无法读取任务");return v;}
function pipelineRequest(){
 const domain=domainParameters("planned");delete domain.scheme_id;
 const basis=$("pipeline-basis").value,comparison={basis,cases:[],fuel_lhv_MJ_kg:numeric("pipeline-lhv")};
 if(basis!=="single")comparison.cases.push({N:numeric("pipeline-n2"),family:$("pipeline-family2").value});
 if(basis!=="single"&&$("pipeline-third").checked)comparison.cases.push({N:numeric("pipeline-n3"),family:$("pipeline-family3").value});
 if(basis==="same-heat")comparison.fuel_heat_input_kW=numeric("pipeline-power");
 return {design:parameters(),domain,mesh:meshValues(),flow:flowValues(),comparison,section:{axis:$("pipeline-axis").value,position_mm:numeric("pipeline-position")}};
}
function displayPipeline(task){
 if(pipelineState.task?.job_id!==task.job_id)$("pipeline-stop").disabled=false;
 pipelineState.task=task;$("pipeline-result").hidden=false;$("pipeline-message").textContent=task.message;
 const running=["queued","running"].includes(task.status);$("pipeline-stop").hidden=!running;$("pipeline-export").disabled=running;
 $("pipeline-stages").replaceChildren();$("pipeline-rows").replaceChildren();const previous=$("pipeline-map-case").value;$("pipeline-map-case").replaceChildren();
 for(const row of task.cases){
  const card=pipelineNode("div",undefined,"pipeline-case");card.append(pipelineNode("strong",`方案 ${row.index} · N=${row.N} · ${familyNames[row.family]}`));const list=pipelineNode("div",undefined,"pipeline-stage-list");
  for(const stage of row.stages){const box=pipelineNode("div",undefined,"pipeline-stage "+stage.status);box.append(pipelineNode("b",pipelineStageNames[stage.name]),pipelineNode("span",stage.message));list.append(box);}card.append(list);$("pipeline-stages").append(card);
  const tr=pipelineNode("tr");for(const v of [row.index,`${row.N} / ${familyNames[row.family]}`,fmt(row.installation_area_mm2,2),fmt(row.actual_fuel_heat_input_kW,6),fmt(row.actual_air_mass_flow_kg_s,8),fmt(row.pressure_drop_Pa?.fuel_inlet,5),fmt(row.pressure_drop_Pa?.oxidizer_inlet,5),pipelineStatusNames[row.status]||row.status])tr.append(pipelineNode("td",v));
  const view=pipelineNode("td"),button=pipelineNode("button","载入装配");button.type="button";button.disabled=!row.scheme_id||running;button.addEventListener("click",()=>showPipelineAssembly(row.index));view.append(button);tr.append(view);$("pipeline-rows").append(tr);
  const option=pipelineNode("option",`方案 ${row.index} · ${familyNames[row.family]}`);option.value=String(row.index);$("pipeline-map-case").append(option);
 }
 if([...$("pipeline-map-case").options].some(x=>x.value===previous))$("pipeline-map-case").value=previous;
 const selected=$("pipeline-map-case").value,firstMap=task.cases.find(row=>task.files.some(f=>f.name===`case${row.index}_speed.png`));
 if(firstMap&&!task.files.some(f=>f.name===`case${selected}_speed.png`))$("pipeline-map-case").value=String(firstMap.index);
 const basis=task.config.comparison.basis;$("pipeline-rule").textContent=(basis==="same-heat"?`相同燃料热输入：目标 ${task.config.comparison.fuel_heat_input_kW} kW；按 ${task.config.comparison.fuel_lhv_MJ_kg} MJ/kg 计算。`:basis==="same-area"?"所有方案使用相同圆形／扇形安装区域。":"当前单方案。")+(!running?` 比较条件${task.comparison_rule_verified===true?"已核对":basis==="single"?"不适用":"尚未全部核对"}；未收敛与接入短测数据不用于正式优选。`:"");
 $("pipeline-files").replaceChildren();for(const file of task.files){const link=pipelineNode("a",file.name);link.href=file.url;link.download=file.name;$("pipeline-files").append(link);}
 displayPipelineMap();
 const first=task.cases[0],previewKey=task.job_id+":"+first?.scheme_id;
 if(first?.scheme_id&&task.files.some(f=>f.name==="case1_scheme.json")&&task.config.design.model_id===state.model?.model_id&&pipelineState.previewKey!==previewKey){pipelineState.previewKey=previewKey;showPipelineAssembly(1);}
}
function displayPipelineMap(){
 const task=pipelineState.task;if(!task)return;const index=Number($("pipeline-map-case").value),field=$("pipeline-map-field").value,row=task.cases.find(x=>x.index===index),file=task.files.find(x=>x.name===`case${index}_${field}.png`);
 $("pipeline-map").hidden=!file;
 if(file){$("pipeline-map").src=file.url;$("pipeline-map").alt=`方案 ${index} 的实际${$("pipeline-map-field").selectedOptions[0].textContent}截面图`;$("pipeline-map-note").textContent=`${task.config.section.axis.toUpperCase()}=${task.config.section.position_mm} mm · 真实单元切面，共同色标 · ${row.numerical_criteria_met?"数值检查通过，物理适用性仍需验证":"保存的未收敛试算场"}。`;}
 else{$("pipeline-map").removeAttribute("src");$("pipeline-map-note").textContent=row?.map_error||row?.error||"该方案尚未取得场图，已生成文件仍可查看和导出。";}
}
async function showPipelineAssembly(index){
 const task=pipelineState.task,file=task.files.find(f=>f.name===`case${index}_scheme.json`);if(!file)return;
 try{
  if(task.config.design.model_id!==state.model?.model_id){
   const restored=await api("/api/pipeline-source",{job_id:task.job_id});state.sources=restored.models;sourceOptions(restored.source.source_id);setSourceReference();
   await loadModel();if(task.config.design.model_id!==state.model?.model_id)throw Error("模型参考坐标未能恢复为该任务快照，仍可查看场图及导出原始装配。");
  }
  const scheme=await pipelineGet(file.url);if(pipelineState.task?.job_id!==task.job_id)return;
  const ids={N:"count",family:"family",region:"region",radius_mm:"radius",start_angle_deg:"start-angle",sweep_angle_deg:"sweep-angle",diameter_mm:"diameter",edge_gap_mm:"gap",wall_clearance_mm:"wall",spacing_mode:"spacing-mode",spacing_mm:"spacing",rotation_deg:"rotation"};
  for(const [key,id] of Object.entries(ids))$(id).value=scheme.request[key];
  $("sector-fields").hidden=scheme.request.region!=="sector";$("manual-fields").hidden=scheme.request.spacing_mode!=="manual";
  invalidateDomain();display(scheme);switchView("3d");$("viewer-status").textContent=`正在查看运行记录中的方案 ${index}，阵列参数已载入。`;
 }catch(e){notice(e.message,"error");}
}
async function watchPipeline(key){
 const serial=++pipelineState.watch;
 try{for(;;){const task=await pipelineGet("/api/pipelines/"+key);if(serial!==pipelineState.watch)return;displayPipeline(task);
  if(!["queued","running"].includes(task.status)){state.busy=false;state.job=null;availability();notice(task.message,task.status==="completed"?"":"neutral");return;}
  state.busy=true;state.job=key;$("busy-overlay").hidden=true;availability();await new Promise(resolve=>setTimeout(resolve,1200));
 }}catch(e){if(serial===pipelineState.watch){state.busy=false;availability();notice("任务连接中断，可刷新运行记录重新连接。"+e.message,"error");}}
}
async function refreshPipelineHistory(preferred){
 const data=await pipelineGet("/api/pipelines"),previous=preferred||$("pipeline-history").value||new URLSearchParams(location.search).get("run");$("pipeline-history").replaceChildren();
 for(const task of data.tasks){const option=pipelineNode("option",`${task.job_id.slice(0,8)} · ${pipelineStatusNames[task.status]||task.status}`);option.value=task.job_id;$("pipeline-history").append(option);}
 if(!data.tasks.length){$("pipeline-history").append(pipelineNode("option","暂无运行记录"));return;}
 $("pipeline-history").value=data.tasks.some(t=>t.job_id===previous)?previous:data.tasks[0].job_id;
 await watchPipeline($("pipeline-history").value);
}
function initializePipeline(){
 $("pipeline-settings").addEventListener("submit",e=>e.preventDefault());
 for(const id of ["pipeline-basis","pipeline-third"])$(id).addEventListener("input",pipelineAvailability);
 $("run-pipeline").addEventListener("click",async()=>{if(state.busy){$("pipeline-panel").scrollIntoView({block:"start"});return;}if(!state.model)return;try{const request=pipelineRequest();state.busy=true;availability();const task=await api("/api/pipeline",request);state.job=task.job_id;$("busy-overlay").hidden=true;$("pipeline-panel").scrollIntoView({block:"start"});await refreshPipelineHistory(task.job_id);}catch(e){state.busy=false;availability();notice(e.message,"error");}});
 $("pipeline-stop").addEventListener("click",async()=>{try{await api("/api/stop-job",{job_id:pipelineState.task.job_id});$("pipeline-stop").disabled=true;}catch(e){notice(e.message,"error");}});
 $("pipeline-export").addEventListener("click",async()=>{const button=$("pipeline-export");button.disabled=true;try{const r=await api("/api/pipeline-export",{job_id:pipelineState.task.job_id});download(r.download_url,r.filename);}catch(e){notice(e.message,"error");}finally{button.disabled=false;}});
 $("pipeline-refresh").addEventListener("click",()=>refreshPipelineHistory().catch(e=>notice(e.message,"error")));
 $("pipeline-history").addEventListener("change",()=>watchPipeline($("pipeline-history").value));
 for(const id of ["pipeline-map-case","pipeline-map-field"])$(id).addEventListener("change",displayPipelineMap);
}
