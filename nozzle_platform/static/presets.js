"use strict";
const presetState={items:[]};
function presetAvailability(){
 const selected=presetState.items.find(p=>p.preset_id===$("preset-select").value);
 for(const id of ["preset-select","preset-name","preset-import"])$(id).disabled=state.busy;
 for(const id of ["preset-load","preset-export"])$(id).disabled=state.busy||!selected?.available;
 for(const id of ["preset-save","preset-check"])$(id).disabled=state.busy||!state.model;
 for(const button of document.querySelectorAll(".preset-field-link"))button.disabled=state.busy;
}
async function refreshPresets(selected){
 const data=await pipelineGet("/api/presets");data.presets.sort((a,b)=>Number(b.physical_model==="air-isothermal-v1")-Number(a.physical_model==="air-isothermal-v1"));presetState.items=data.presets;
 const before=selected||$("preset-select").value;$("preset-select").replaceChildren();
 for(const p of data.presets){const option=document.createElement("option");option.value=p.preset_id;option.textContent=(p.builtin?"起步示例 · ":"")+p.name+(p.available?` · N=${p.N}`:" · 不可用");$("preset-select").append(option);}
 if(!data.presets.length){const option=document.createElement("option");option.value="";option.textContent="暂无预设，可保存当前配置";$("preset-select").append(option);}
 if(data.presets.some(p=>p.preset_id===before))$("preset-select").value=before;
 presetAvailability();
}
function presetValue(id,value){
 const input=$(id);if(input.tagName==="SELECT"&&![...input.options].some(o=>o.value===String(value))){const option=document.createElement("option");option.value=String(value);option.textContent=String(value)+"（预设值）";input.append(option);}
 if(input.type==="checkbox")input.checked=!!value;else input.value=value;
}
function applyPreset(request){
 presetValue("flow-physics",request.flow.physical_model||"h2-air");
 presetValue("flow-species-control",request.flow.species_solver_control||"standard");
 const maps={design:{N:"count",family:"family",region:"region",radius_mm:"radius",start_angle_deg:"start-angle",sweep_angle_deg:"sweep-angle",diameter_mm:"diameter",edge_gap_mm:"gap",wall_clearance_mm:"wall",spacing_mode:"spacing-mode",spacing_mm:"spacing",rotation_deg:"rotation"},
 domain:{boundary_template:"domain-template",chamber_radius_mm:"chamber-radius",chamber_length_mm:"chamber-length",chamber_start_z_mm:"chamber-start",passage_radius_mm:"passage-radius",upstream_z_mm:"upstream-z",fuel_z_mm:"fuel-z",fuel_half_band_mm:"fuel-band",oxidizer_ports_per_nozzle:"air-ports",fuel_ports_per_nozzle:"fuel-ports"},
 mesh:{mesh_method:"mesh-method",min_size_mm:"mesh-min",max_size_mm:"mesh-max",inlet_size_mm:"mesh-inlet",refine_distance_mm:"mesh-distance",algorithm:"mesh-algorithm",curvature_samples:"mesh-curvature",passage_size_mm:"mesh-passage",size_factor:"mesh-factor",cell_budget:"mesh-budget",layer_first_height_mm:"mesh-layer-first",layer_count:"mesh-layer-count",layer_thickness_mm:"mesh-layer-total",local_volume_refinement:"mesh-local-volume"},
 flow:{compute_device:"flow-device",transport_model:"flow-transport",fuel_velocity_m_s:"flow-fuel",oxidizer_velocity_m_s:"flow-air",temperature_K:"flow-temperature",oxidizer_o2_mass_fraction:"flow-oxygen",iterations:"flow-iterations",convergence_mode:"flow-mode",time_budget_seconds:"flow-time-budget"},
 section:{axis:"pipeline-axis",position_mm:"pipeline-position"}};
 for(const [group,fields] of Object.entries(maps))for(const [key,id] of Object.entries(fields))if(request[group][key]!==undefined)presetValue(id,request[group][key]);
 presetValue("pipeline-basis",request.comparison.basis);presetValue("pipeline-lhv",request.comparison.fuel_lhv_MJ_kg);
 if(request.comparison.fuel_heat_input_kW!==undefined)presetValue("pipeline-power",request.comparison.fuel_heat_input_kW);
 const cases=request.comparison.cases;$("pipeline-third").checked=cases.length>1;
 for(let i=0;i<cases.length;i++){presetValue("pipeline-n"+(i+2),cases[i].N);presetValue("pipeline-family"+(i+2),cases[i].family);}
 $("template-confirmed").checked=false;$("sector-fields").hidden=request.design.region!=="sector";$("manual-fields").hidden=request.design.spacing_mode!=="manual";
 $("spacing").parentElement.hidden=!["honeycomb","rectangular"].includes(request.design.family);
 $("preset-check-result").hidden=true;invalidate();updateMeshMethod();availability();
}
async function loadPreset(){
 if(state.busy)return;busy(true,"正在恢复预设模型与参数…");
 try{const data=await api("/api/preset-load",{preset_id:$("preset-select").value});invalidateModel();state.sources=data.models;sourceOptions(data.model.source_id);$("axis").value=data.model.axis;installModel(data.model);applyPreset(data.request);
  $("preset-name").value=data.preset.name;$("preset-note").textContent=`已载入 ${data.preset.name}。模型与参考坐标已恢复；请确认供气模板，检查参数后再运行。`+(data.request.flow.physical_model==="air-isothermal-v1"?"当前为单一空气定温冷流；两类通道都供空气，不计算燃烧。":data.request.flow.compute_device==="gpu"?"当前为界面明确显示的 GPU 兼容物性短测。":"当前为 CPU 有界短测，使用界面所选物性。");
  notice("预设已载入，尚未启动网格或求解。请核对供气模板与当前实际结构。","neutral");
 }catch(e){notice(e.message,"error");}finally{busy(false);}
}
const preflightFields={
 chamber_radius_mm:{id:"chamber-radius",label:"燃烧室半径"},
 chamber_length_mm:{id:"chamber-length",label:"燃烧室长度"},
 chamber_start_z_mm:{id:"chamber-start",label:"公共腔起点"},
 passage_radius_mm:{id:"passage-radius",label:"内部截取半径"},
 upstream_z_mm:{id:"upstream-z",label:"空气入口截面"},
 fuel_z_mm:{id:"fuel-z",label:"供氢带中心"},
 fuel_half_band_mm:{id:"fuel-band",label:"供氢带半宽"},
 oxidizer_ports_per_nozzle:{id:"air-ports",label:"空气口数量"},
 fuel_ports_per_nozzle:{id:"fuel-ports",label:"氢气口数量"},
 template_confirmed:{id:"template-confirmed",label:"供气模板确认"}
};
function focusPreflightField(field){
 if(state.busy)return;
 const descriptor=preflightFields[field];
 if(!descriptor)return;
 const target=$(descriptor.id);
 for(let parent=target.parentElement;parent;parent=parent.parentElement){
  if(parent.tagName==="DETAILS")parent.open=true;
 }
 for(const previous of document.querySelectorAll(".preflight-target"))previous.classList.remove("preflight-target");
 target.classList.add("preflight-target");
 target.scrollIntoView({block:"center"});
 target.focus({preventScroll:true});
}
function addPreflightFieldLinks(parent,fields){
 const links=document.createElement("div");
 links.className="preset-field-links";
 for(const field of fields){
  const descriptor=preflightFields[field];
  if(!descriptor)continue;
  const button=document.createElement("button");
  button.type="button";
  button.className="preset-field-link";
  button.textContent="修改"+descriptor.label;
  button.disabled=state.busy;
  button.addEventListener("click",()=>focusPreflightField(field));
  links.append(button);
 }
 if(links.childElementCount)parent.append(links);
}
function displayPreflight(result){
 const box=$("preset-check-result");box.hidden=false;box.replaceChildren();
 for(const row of result.cases){
  const item=document.createElement("div");
  item.className="preset-check-row"+(row.parameters_valid?" ready":"");
  const issues=Array.isArray(row.issues)?row.issues:[];
  item.textContent=`方案 ${row.index} · N=${row.N} · `+(issues.length?"请核对以下参数":row.message);
  for(const issue of issues){
   const detail=document.createElement("div");
   detail.className="preset-issue";
   const message=document.createElement("p");
   message.textContent=issue.message;
   detail.append(message);
   addPreflightFieldLinks(detail,Array.isArray(issue.fields)?issue.fields:[]);
   item.append(detail);
  }
  box.append(item);
 }
 const r=result.resources,summary=document.createElement("p");summary.textContent=`可用内存 ${fmt(r.available_memory_GiB,2)} GiB（需 ≥ ${r.minimum_memory_GiB}）；输出盘空闲 ${fmt(r.output_disk_free_GiB,2)} GiB（需 ≥ ${r.minimum_disk_GiB}）。${r.fluent_available?"已找到 Fluent 程序。":"未找到 Fluent 程序。"}${result.template_confirmed?"":"供气模板尚未确认。"}`;box.append(summary);
 if(!result.template_confirmed)addPreflightFieldLinks(box,["template_confirmed"]);
 const pending=document.createElement("p");pending.textContent="后续仍需核查："+result.pending_checks.join("、")+"。";box.append(pending);const note=document.createElement("p");note.className="hint";note.textContent=result.note;box.append(note);
}
function initializePresets(){
 $("preset-select").addEventListener("change",()=>{presetAvailability();const p=presetState.items.find(p=>p.preset_id===$("preset-select").value);if(p&&!p.available)$("preset-note").textContent=p.error;});
 $("preset-load").addEventListener("click",loadPreset);
 $("preset-save").addEventListener("click",async()=>{try{const request=pipelineRequest({useDisplayedGeometry:false}),name=$("preset-name").value;busy(true,"正在保存模型参考与整套参数…");const p=await api("/api/preset-save",{name,request});await refreshPresets(p.preset_id);notice("参数预设已保存，原排布坐标可从几何方案库恢复。载入预设后需重新确认供气模板。");}catch(e){notice(e.message,"error");}finally{busy(false);}});
 $("preset-export").addEventListener("click",()=>download("/preset-files/"+$("preset-select").value+".json","nozzle_preset.json"));
 $("preset-import").addEventListener("click",()=>$("preset-file").click());
 $("preset-file").addEventListener("change",async event=>{try{const file=event.target.files[0];if(!file)return;if(file.size>65536)throw Error("参数 JSON 超过 64 KiB。");const data=JSON.parse(await file.text());busy(true,"正在核对并导入参数预设…");const p=await api("/api/preset-import",data);await refreshPresets(p.preset_id);notice("预设已导入；选择「载入所选预设」后再检查运行条件。");}catch(e){notice(e.message,"error");}finally{busy(false);event.target.value="";}});
 $("preset-check").addEventListener("click",async()=>{try{const request=pipelineRequest();busy(true,"正在检查参数与当前资源…");displayPreflight(await api("/api/pipeline-check",request));}catch(e){notice(e.message,"error");}finally{busy(false);}});
 for(const form of ["design-form","pipeline-settings","domain-form","simulation-form"])$(form).addEventListener("input",()=>{$("preset-check-result").hidden=true;});
}
