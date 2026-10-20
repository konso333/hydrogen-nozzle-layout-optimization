/* Offline WebGL viewer. The preview uses actual CAD surface triangles. */
"use strict";
class NozzleViewer {
  constructor(canvas) {
    this.canvas=canvas;
    this.gl=canvas.getContext("webgl",{antialias:true,alpha:true,preserveDrawingBuffer:true});
    if(!this.gl) throw new Error("当前浏览器未启用 WebGL，请启用硬件加速后重试。");
    const gl=this.gl;
    const vertex=`attribute vec3 position; attribute vec3 normal; uniform mat3 rotation; uniform float scale; uniform float aspect; uniform float centerZ; varying vec3 vNormal; void main(){ vec3 p=rotation*(position-vec3(0.,0.,centerZ)); gl_Position=vec4(p.x*scale/aspect,p.y*scale,-p.z*scale*.2,1.); vNormal=rotation*normal; }`;
    const fragment=`precision mediump float; varying vec3 vNormal; uniform vec3 color; uniform bool lines; void main(){float light=lines?1.:.53+.47*abs(dot(normalize(vNormal),normalize(vec3(-.35,.7,1.)))); gl_FragColor=vec4(color*light,1.);}`;
    const shader=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(s));return s;};
    this.program=gl.createProgram();gl.attachShader(this.program,shader(gl.VERTEX_SHADER,vertex));gl.attachShader(this.program,shader(gl.FRAGMENT_SHADER,fragment));gl.linkProgram(this.program);
    if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw new Error("三维预览初始化失败。");
    this.locations={};for(const n of ["rotation","scale","aspect","centerZ","color","lines"])this.locations[n]=gl.getUniformLocation(this.program,n);
    this.position=gl.getAttribLocation(this.program,"position");this.normal=gl.getAttribLocation(this.program,"normal");
    this.meshBuffer=gl.createBuffer();this.lineBuffer=gl.createBuffer();this.count=0;this.lineCount=0;this.model=null;this.reset();
    new ResizeObserver(()=>this.draw()).observe(canvas);
    let drag=null;
    canvas.addEventListener("pointerdown",e=>{drag={x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId);});
    canvas.addEventListener("pointermove",e=>{if(!drag)return;this.yaw+=(e.clientX-drag.x)*.008;this.pitch=Math.max(.05,Math.min(3.08,this.pitch+(e.clientY-drag.y)*.008));drag={x:e.clientX,y:e.clientY};this.draw();});
    canvas.addEventListener("pointerup",()=>{drag=null;});canvas.addEventListener("pointercancel",()=>{drag=null;});
    canvas.addEventListener("wheel",e=>{e.preventDefault();this.zoom=Math.max(.25,Math.min(4,this.zoom*Math.exp(-e.deltaY*.001)));this.draw();},{passive:false});
  }
  reset(){this.yaw=-.35;this.pitch=.96;this.zoom=1;this.draw();}
  setModel(model,scheme=null){
    this.model=model;this.scheme=scheme;
    const coordinates=scheme?scheme.coordinates_mm:[[0,0]],gl=this.gl;
    const base=new Float32Array(model.triangles.length*18);let cursor=0;
    for(const face of model.triangles){
      const a=model.vertices[face[0]],b=model.vertices[face[1]],c=model.vertices[face[2]];
      const u=b.map((v,i)=>v-a[i]),v=c.map((v,i)=>v-a[i]);let n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]];
      const length=Math.hypot(...n)||1;n=n.map(x=>x/length);
      for(const p of [a,b,c]){base.set(p,cursor);base.set(n,cursor+3);cursor+=6;}
    }
    const vertices=new Float32Array(base.length*coordinates.length);
    coordinates.forEach(([x,y],index)=>{const offset=index*base.length;vertices.set(base,offset);for(let i=offset;i<offset+base.length;i+=6){vertices[i]+=x;vertices[i+1]+=y;}});
    this.count=vertices.length/6;gl.bindBuffer(gl.ARRAY_BUFFER,this.meshBuffer);gl.bufferData(gl.ARRAY_BUFFER,vertices,gl.STATIC_DRAW);
    const [low,high]=model.bounds_mm;this.centerZ=(low[2]+high[2])/2;
    const radius=scheme?scheme.request.radius_mm:Math.max(model.envelope_diameter_mm*.85,8);
    this.extent=Math.max(radius*1.25,(high[2]-low[2])*.72,10);
    const lines=[];const segment=(a,b)=>{lines.push(...a,0,0,1,...b,0,0,1);};
    const gridStep=radius>100?20:10,gridRadius=Math.ceil(radius*1.1/gridStep)*gridStep,z=low[2]-.8;
    for(let i=-gridRadius;i<=gridRadius;i+=gridStep){segment([i,-gridRadius,z],[i,gridRadius,z]);segment([-gridRadius,i,z],[gridRadius,i,z]);}
    if(scheme){
      const r=scheme.request,start=r.region==="circle"?0:r.start_angle_deg*Math.PI/180,sweep=r.region==="circle"?Math.PI*2:r.sweep_angle_deg*Math.PI/180;
      for(let i=0;i<160;i++){const a=start+sweep*i/160,b=start+sweep*(i+1)/160;segment([radius*Math.cos(a),radius*Math.sin(a),0],[radius*Math.cos(b),radius*Math.sin(b),0]);}
      if(r.region==="sector"){segment([0,0,0],[radius*Math.cos(start),radius*Math.sin(start),0]);segment([0,0,0],[radius*Math.cos(start+sweep),radius*Math.sin(start+sweep),0]);}
    }
    this.lineCount=lines.length/6;gl.bindBuffer(gl.ARRAY_BUFFER,this.lineBuffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(lines),gl.STATIC_DRAW);this.draw();
  }
  draw(){
    const gl=this.gl;if(!gl)return;
    const rect=this.canvas.getBoundingClientRect(),pixelRatio=Math.min(devicePixelRatio||1,2);
    const width=Math.max(1,Math.round(rect.width*pixelRatio)),height=Math.max(1,Math.round(rect.height*pixelRatio));
    if(this.canvas.width!==width||this.canvas.height!==height){this.canvas.width=width;this.canvas.height=height;}
    gl.viewport(0,0,width,height);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    if(!this.model)return;
    gl.enable(gl.DEPTH_TEST);gl.depthFunc(gl.LEQUAL);gl.useProgram(this.program);
    const c=Math.cos(this.yaw),s=Math.sin(this.yaw),cp=Math.cos(this.pitch),sp=Math.sin(this.pitch);
    gl.uniformMatrix3fv(this.locations.rotation,false,new Float32Array([c,cp*s,sp*s,-s,cp*c,sp*c,0,-sp,cp]));
    gl.uniform1f(this.locations.scale,this.zoom/(this.extent||50));gl.uniform1f(this.locations.aspect,width/height);gl.uniform1f(this.locations.centerZ,this.centerZ||0);
    const bind=buffer=>{gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.enableVertexAttribArray(this.position);gl.vertexAttribPointer(this.position,3,gl.FLOAT,false,24,0);gl.enableVertexAttribArray(this.normal);gl.vertexAttribPointer(this.normal,3,gl.FLOAT,false,24,12);};
    bind(this.lineBuffer);gl.uniform3f(this.locations.color,.80,.86,.87);gl.uniform1i(this.locations.lines,1);gl.drawArrays(gl.LINES,0,this.lineCount);
    bind(this.meshBuffer);gl.uniform3f(this.locations.color,.40,.69,.65);gl.uniform1i(this.locations.lines,0);gl.drawArrays(gl.TRIANGLES,0,this.count);
  }
}
window.NozzleViewer=NozzleViewer;
