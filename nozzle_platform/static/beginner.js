"use strict";

const beginner = {limits: null, example: false};

function beginnerAvailability(){
  if(!state.beginner)return;
  $("beginner-import").disabled=state.busy||!beginner.limits;
  $("beginner-example").disabled=state.busy||!beginner.limits;
  $("beginner-generate").disabled=state.busy||!state.model;
  $("beginner-download").disabled=state.busy||!state.scheme;
  for(const button of $("beginner-suggestions").querySelectorAll("button"))button.disabled=state.busy;
}

function beginnerStatus(text="",kind=""){
  const status=$("beginner-status");
  status.textContent=text;
  status.dataset.kind=kind;
  status.hidden=!text;
}

function beginnerSuggestions(){
  const suggestions=$("beginner-suggestions");
  suggestions.replaceChildren();
  suggestions.hidden=true;
  return suggestions;
}

function addBeginnerSuggestion(label,action){
  const suggestions=$("beginner-suggestions");
  const button=document.createElement("button");
  button.type="button";
  button.className="button secondary";
  button.textContent=label;
  button.addEventListener("click",action);
  suggestions.append(button);
  suggestions.hidden=false;
}

function beginnerPreviewLimit(){
  return Math.min(beginner.limits.count,Math.floor(beginner.limits.preview_triangles/state.model.triangle_count));
}

function updateBeginnerModel(isExample){
  beginner.example=isExample;
  const model=state.model;
  $("beginner-example").hidden=isExample;
  $("beginner-model").textContent=model?(isExample?`示例喷嘴 · ${model.label}`:`你的模型 · ${model.label}（按默认方向和参考点试排）`):"请使用示例，或上传一个 STEP 模型。";
  const limit=model?beginnerPreviewLimit():beginner.limits.count;
  $("beginner-count").max=Math.max(1,limit);
  $("beginner-range").textContent=limit?`当前模型支持输入 1–${limit} 个。可先用 ${Math.min(11,limit)} 个试一遍。`:"这个模型太复杂，请使用简化版或示例模型。";
  beginnerAvailability();
}

function resetBeginnerResult(){
  if(state.scheme)invalidate();
  $("beginner-result").hidden=true;
  beginnerSuggestions();
  beginnerStatus();
}

async function loadBeginnerExample(){
  resetBeginnerResult();
  busy(true,"正在准备示例喷嘴…");
  beginnerStatus("正在准备示例喷嘴…");
  try{
    const reply=await api("/api/starter-model",{});
    invalidateModel();
    state.sources=reply.models;
    sourceOptions(reply.model.source_id);
    $("axis").value=reply.model.axis;
    installModel(reply.model);
    updateBeginnerModel(true);
    beginnerStatus();
  }catch(error){
    updateBeginnerModel(false);
    beginnerStatus(`示例喷嘴暂时无法载入。你可以重试，或上传自己的 STEP。${error.message}`,"error");
  }finally{busy(false);}
}

async function beginnerImport(file){
  resetBeginnerResult();
  beginnerStatus("正在读取你的模型…");
  const source=await importModel(file);
  const model=source?await loadModel():null;
  updateBeginnerModel(source?false:beginner.example);
  beginnerStatus(model?"文件已读入。输入数量即可试排。":`文件未能载入。请选择一个喷嘴的 STEP／STP 实体，或使用示例。${$("notice").textContent}`,model?"":"error");
}

function showBeginnerResult(scheme,automaticRadius){
  $("family").value=scheme.request.family;
  display(scheme);
  const request=scheme.request;
  $("beginner-result-title").textContent=`已排好 ${request.N} 个喷嘴`;
  const region=request.region==="circle"?"圆形":`${fmt(request.sweep_angle_deg)}° 扇形`;
  const size=`${region}，半径 ${fmt(request.radius_mm)} mm`;
  const scope=automaticRadius?"安装区大小由网站为本次试排选定。":"已按所示安装尺寸核对边缘和间隙。";
  const reference=beginner.example?"":"自带模型使用默认方向和参考点，工程使用前需核对。";
  $("beginner-result-details").textContent=`${familyNames[request.family]}排列 · ${size}。${scope}${reference}`;
  $("beginner-result").hidden=false;
  switchView("plan");
  $("viewer-status").textContent="数量、间隙和安装边缘检查通过";
  $("beginner-result-title").focus({preventScroll:true});
  $("beginner-result").scrollIntoView({block:"start"});
}

function setBeginnerFields(){
  const region=$("beginner-region").value;
  $("beginner-radius-field").hidden=region==="auto";
  $("beginner-angle-field").hidden=region!=="sector";
  $("beginner-scope").textContent=region==="auto"?"网站会选一个便于试排的安装尺寸。此处生成的是几何图和坐标。":"网站按你填写的安装尺寸排布，空间不足时会提供调整建议。";
}

function prepareBeginnerRequest(){
  const count=$("beginner-count");
  const N=count.valueAsNumber;
  const max=beginnerPreviewLimit();
  if(!Number.isInteger(N)||N<1){
    beginnerStatus("请输入一个大于 0 的整数，例如 11。","error");
    count.focus();
    return null;
  }
  if(N>max){
    beginnerStatus(max?`这个模型目前一次最多预览 ${max} 个喷嘴。你可以使用下面的数量，或上传更简单的模型。`:"这个模型太复杂，无法预览阵列。请用示例或简化模型。","error");
    if(max)addBeginnerSuggestion(`试排 ${max} 个`,()=>{count.value=max;$("beginner-form").requestSubmit();});
    count.focus();
    return null;
  }
  const region=$("beginner-region").value;
  const family=$("beginner-family").value;
  const diameter=state.model.envelope_diameter_mm;
  const gap=Number($("gap").value),wall=Number($("wall").value);
  const columns=Math.ceil(Math.sqrt(N));
  const trialRadius=Math.ceil(((columns-1)*(diameter+gap)/Math.SQRT2+diameter/2+wall)*1.05);
  const radius=$("beginner-radius"), angle=$("beginner-angle");
  if(region!=="auto"&&(!radius.value||!radius.validity.valid)){
    beginnerStatus("请填安装区半径，范围为 0.001–5000 mm；也可以让网站选一个试排尺寸。","error");
    radius.focus();
    return null;
  }
  if(region==="sector"&&(!angle.value||!angle.validity.valid)){
    beginnerStatus("请输入有效的扇形张角，例如 120° 或 180°。","error");
    angle.focus();
    return null;
  }
  if(region==="auto"&&trialRadius>5000){
    beginnerStatus("这个模型和数量需要很大的安装区。请减少数量，或填入实际安装尺寸再试。","error");
    return null;
  }
  const fields={count:N,region:region==="sector"?"sector":"circle",radius:region==="auto"?trialRadius:radius.value,
    "start-angle":0,"sweep-angle":region==="sector"?angle.value:360,family:family==="auto"?"rectangular":family,"spacing-mode":"auto"};
  for(const [id,value] of Object.entries(fields))$(id).value=value;
  return parameters();
}

async function adoptBeginnerSuggestion(result,suggestion){
  if(state.busy)return;
  busy(true,"正在复核调整后的排布…");
  beginnerStatus("正在复核调整后的排布…");
  try{
    const scheme=await api("/api/design-assist-adopt",{advice_id:result.advice_id,suggestion_id:suggestion.suggestion_id,current_design:parameters()});
    $("beginner-count").value=scheme.request.N;
    if($("beginner-family").value!=="auto")$("beginner-family").value=scheme.request.family;
    const automaticRadius=$("beginner-region").value==="auto"&&suggestion.kind!=="radius";
    if(!automaticRadius){$("beginner-region").value=scheme.request.region;$("beginner-radius").value=scheme.request.radius_mm;}
    setBeginnerFields();
    beginnerSuggestions();
    beginnerStatus();
    showBeginnerResult(scheme,automaticRadius);
  }catch(error){beginnerStatus(error.message,"error");}finally{busy(false);}
}

function showBeginnerSuggestions(result){
  const proven=result.feasibility==="proven_infeasible";
  const N=result.requested_design.N;
  const reason=proven?`这块安装区太小，放不下 ${N} 个。`:`这次在所选条件下没有找到 ${N} 个的位置。`;
  const next=result.suggestions.length?"可以试下面已经通过几何检查的方案。":"可以尝试减少数量、改用自动推荐，或调整安装尺寸。";
  beginnerStatus(`${reason}${next}`,"error");
  for(const suggestion of result.suggestions){
    const params=suggestion.request;
    const label=suggestion.kind==="radius"?`保留 ${N} 个，半径改为 ${fmt(params.radius_mm)} mm`:
      suggestion.kind==="quantity"?`保留区域大小，改排 ${params.N} 个`:`保留 ${N} 个，换成${familyNames[params.family]}`;
    addBeginnerSuggestion(label,()=>adoptBeginnerSuggestion(result,suggestion));
  }
}

async function generateBeginner(event){
  event.preventDefault();
  if(!state.model||state.busy)return;
  resetBeginnerResult();
  const design=prepareBeginnerRequest();
  if(!design)return;
  const automaticRadius=$("beginner-region").value==="auto";
  busy(true,"正在自动摆放喷嘴…");
  beginnerStatus("正在自动摆放喷嘴，并检查间隙和边缘…");
  try{
    const result=await api("/api/design-assist",{design,explore_families:$("beginner-family").value==="auto"});
    if(result.scheme){beginnerStatus();showBeginnerResult(result.scheme,automaticRadius);}
    else showBeginnerSuggestions(result);
  }catch(error){beginnerStatus(error.message,"error");}finally{busy(false);}
}

function initializeBeginner(){
  if(!state.beginner)return;
  document.body.classList.add("beginner-mode");
  $("beginner-panel").hidden=false;
  $("quick-mode").hidden=true;
  $("workspace-mode").href="/?mode=advanced";
  $("workspace-mode").textContent="专业工作台 ↗";
  $("view-plan").textContent="俯视图";
  $("save-image").textContent="保存图片 ↓";
  document.querySelector(".page-footer span").textContent="NOZZLE LAB / 喷嘴快速排布";
  $("beginner-form").addEventListener("submit",generateBeginner);
  $("beginner-import").addEventListener("click",()=>$("model-file").click());
  $("beginner-example").addEventListener("click",loadBeginnerExample);
  $("beginner-form").addEventListener("input",()=>{setBeginnerFields();resetBeginnerResult();});
  $("beginner-download").addEventListener("click",async()=>{
    if(state.busy||!state.scheme)return;
    await exportScheme(false);
    const noticeNode=$("notice");
    beginnerStatus(noticeNode.textContent,noticeNode.classList.contains("error")?"error":"");
  });
}

async function startBeginner(limits){
  beginner.limits=limits;
  await loadBeginnerExample();
}
