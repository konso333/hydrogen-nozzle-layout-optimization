"use strict";
const domainFields=["domain-template","chamber-radius","chamber-length","chamber-start","passage-radius","upstream-z","fuel-z","fuel-band","air-ports","fuel-ports","template-confirmed"];
const domainRoleInfo={oxidizer_inlet:["空气入口","air"],fuel_inlet:["氢气入口","fuel"],outlet:["公共出口","outlet"],wall:["壁面","wall"]};
function domainAvailability(){
  $("domain-fieldset").disabled=state.busy||!state.model;
  $("build-domain").disabled=state.busy||!state.scheme||!$("template-confirmed").checked;
  $("export-domain").disabled=state.busy||!state.domain;
  $("view-domain").disabled=state.busy||!state.domain;
  $("domain-state").textContent=state.domain?"已构建":state.scheme?"可构建":"先生成阵列";
  $("domain-state").className=state.domain?"tag domain-state-ready":"tag";
  for(const id of ["cutaway","boundary-filter"])$(id).disabled=state.busy||!state.domain;
}
function invalidateDomain(){
  invalidateMesh();
  state.domain=null;$("domain-results").hidden=true;$("boundary-cards").replaceChildren();
  if(state.view==="domain")switchView("3d");
}
function domainParameters(schemeId=state.scheme?.scheme_id){
  const n=id=>{const text=$(id).value.trim(),value=Number(text);if(!text||!Number.isFinite(value))throw new Error("请完整填写流体域参数，数值必须有限。");return value;};
  return {scheme_id:schemeId,chamber_radius_mm:n("chamber-radius"),chamber_length_mm:n("chamber-length"),chamber_start_z_mm:n("chamber-start"),passage_radius_mm:n("passage-radius"),upstream_z_mm:n("upstream-z"),fuel_z_mm:n("fuel-z"),fuel_half_band_mm:n("fuel-band"),oxidizer_ports_per_nozzle:n("air-ports"),fuel_ports_per_nozzle:n("fuel-ports"),boundary_template:$("domain-template").value,template_confirmed:$("template-confirmed").checked};
}
function displayDomain(domain){
  state.domain=domain;$("domain-results").hidden=false;$("boundary-cards").replaceChildren();
  $("domain-summary").textContent=`${domain.connected_fluid_components} 个连通流体域 · ${fmt(domain.volume_mm3/1000,2)} cm³ · 公共腔 Z=${fmt(domain.config.chamber_start_z_mm)}～${fmt(domain.config.outlet_z_mm)} mm`;
  for(const [role,[name,css]] of Object.entries(domainRoleInfo)){
    const card=document.createElement("div");card.className="boundary-card "+css;
    const title=document.createElement("h4"),dot=document.createElement("i");title.append(dot,document.createTextNode(name));
    const count=document.createElement("strong");count.textContent=domain.boundary_patch_counts[role]+(role==="wall"?" 组连通面":" 个开口");
    const details=document.createElement("small");details.textContent=`${fmt(domain.boundary_area_mm2[role],3)} mm² · ${domain.physical_surfaces[role].length} 个 CAD 面`;
    card.append(title,count,details);$("boundary-cards").append(card);
  }
  $("boundary-filter").value="all";$("cutaway").checked=true;switchView("domain");state.viewer?.reset();
  $("viewer-status").textContent="流体连通、入口数量、出口面积和导出边界检查通过";
  notice(`公共流体域已构建：${domain.boundary_patch_counts.oxidizer_inlet} 个空气入口、${domain.boundary_patch_counts.fuel_inlet} 个氢气入口及 1 个公共出口。尚未划分体网格或求解。`);
  availability();
}
function initializeDomains(){
  for(const id of domainFields)$(id).addEventListener("input",()=>{invalidateDomain();availability();if(state.scheme)notice("流体域参数已变更，请重新构建。","neutral");});
  $("domain-form").addEventListener("submit",async event=>{
    event.preventDefault();if(!state.scheme||state.busy)return;
    let request;try{request=domainParameters();}catch(error){notice(error.message,"error");return;}
    invalidateDomain();busy(true,"正在连接喷嘴通道与公共腔体…");$("viewport").scrollIntoView({block:"center",behavior:"auto"});
    try{displayDomain(await api("/api/domain",request));}catch(error){notice(error.message,"error");$("viewer-status").textContent="流体域构建未通过，请核对参数";}finally{busy(false);}
  });
  $("view-domain").addEventListener("click",()=>switchView("domain"));
  $("cutaway").addEventListener("input",()=>{if(state.viewer){state.viewer.cutaway=$("cutaway").checked;state.viewer.draw();}});
  $("boundary-filter").addEventListener("change",()=>{if(state.domain)switchView("domain");});
  $("export-domain").addEventListener("click",async()=>{
    if(!state.domain||!state.scheme)return;busy(true,"正在整理流体域与命名边界…");
    try{const result=await api("/api/export-domain",{domain_id:state.domain.domain_id,scheme_id:state.scheme.scheme_id});download(result.download_url,result.filename);notice("流体域包已导出：包含 STEP、BREP、命名边界加载文件和完整检查记录。");}catch(error){notice(error.message,"error");}finally{busy(false);}
  });
}
