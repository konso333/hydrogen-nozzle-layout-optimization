"use strict";
const designAdviceState={result:null};
function clearDesignAdvice(){designAdviceState.result=null;$("design-assistance").hidden=true;$("design-assist-options").replaceChildren();}
function showDesignAdvice(result){
 designAdviceState.result=result;$("design-assistance").hidden=false;$("design-assist-message").textContent=result.message;
 $("design-assist-reasons").replaceChildren();for(const item of result.diagnostics){const li=document.createElement("li");li.textContent=item.message;$("design-assist-reasons").append(li);}
 $("design-assist-options").replaceChildren();for(const suggestion of result.suggestions){
  const button=document.createElement("button");button.type="button";button.className="button secondary";button.textContent="采用建议 · "+suggestion.label;
  button.addEventListener("click",()=>adoptDesignSuggestion(result,suggestion));$("design-assist-options").append(button);
 }
 $("design-assist-scope").textContent=result.scope+" 数量和安装半径仅在点击相应建议后改变；这些不是 CFD 性能或最大容量结论。";
}
async function adoptDesignSuggestion(result,suggestion){
 if(state.busy||designAdviceState.result!==result)return;
 try{
  if(JSON.stringify(parameters())!==JSON.stringify(result.requested_design))throw Error("参数已改变，请重新智能生成，旧建议不再适用。");
  busy(true,"正在重新核验建议方案…");
  const scheme=await api("/api/design-assist-adopt",{advice_id:result.advice_id,suggestion_id:suggestion.suggestion_id,current_design:parameters()});if(!scheme.feasible)throw Error("建议复核未通过，请重新搜索；当前输入保持原值。");
  const labels={N:"喷嘴数量",family:"排列方式",radius_mm:"安装半径 / mm"};const changes=[];for(const key of Object.keys(suggestion.request))if(suggestion.request[key]!==result.requested_design[key])changes.push(`${labels[key]||key}: ${key==="family"?familyNames[result.requested_design[key]]:result.requested_design[key]} → ${key==="family"?familyNames[suggestion.request[key]]:suggestion.request[key]}`);
  invalidate();const fields={N:"count",family:"family",radius_mm:"radius"};for(const [key,id] of Object.entries(fields))$(id).value=suggestion.request[key];
  display(scheme);showDesignAdvice({...result,requested_design:{...suggestion.request},scheme,diagnostics:[],suggestions:[],message:"已采用并重新核验："+changes.join("；")+"。"+(suggestion.kind==="radius"?"安装半径已改变，后续流体域的腔体尺寸需重新核对。":"")+"未启动 Fluent。"});
  $("viewer-status").textContent+=" · 已采用建议";
 }catch(error){notice(error.message,"error");}finally{busy(false);}
}
async function generateAssistedDesign(request){
 const result=await api("/api/design-assist",{design:request,explore_families:$("explore-families").checked});
 if(result.scheme){$("family").value=result.scheme.request.family;display(result.scheme);showDesignAdvice(result);notice(result.message+" 可检查二维排布和三维装配；未启动 Fluent。");}
 else{showDesignAdvice(result);notice(result.message,"neutral");$("result-note").textContent="请查看几何设计助手中的原因与已核验建议。";$("viewer-status").textContent="当前目标未取得，未改变数量或约束";$("design-assistance").scrollIntoView({block:"center"});}
}
