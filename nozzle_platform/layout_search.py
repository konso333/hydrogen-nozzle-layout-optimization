"""Bounded deterministic point pools for whole-disc mounting constraints.

These are construction searches, not proofs of maximum packing or global optima.
The legacy shape-search engine remains available for manual requests.
"""
import hashlib,json,math
import numpy as np
from shape_boundary import MountingBoundary,validate_mounting_layout

ENGINE='mounting-pools-1'
MAX_POOL=1600

def distances(points,boundary):
    """Vectorized exact arc/finite-edge signed distance, including reflex sectors."""
    p=np.asarray(points,dtype=float).reshape(-1,2);r=np.linalg.norm(p,axis=1)
    if boundary.kind=='circle':return boundary.radius_mm-r
    start=math.radians(boundary.start_angle_deg);sweep=math.radians(boundary.sweep_angle_deg)
    angular=((np.arctan2(p[:,1],p[:,0])-start)%(2*math.pi)<=sweep)|(r==0)
    edges=[];ends=[]
    for angle in (start,start+sweep):
        u=np.array([math.cos(angle),math.sin(angle)]);t=np.clip(p@u,0,boundary.radius_mm)
        edges.append(np.linalg.norm(p-t[:,None]*u,axis=1));ends.append(np.linalg.norm(p-boundary.radius_mm*u,axis=1))
    arc=np.where(angular,np.abs(boundary.radius_mm-r),np.minimum(*ends))
    value=np.minimum(np.minimum(*edges),arc)
    return np.where(angular&(r<=boundary.radius_mm),value,-value)

def boundary_from(request):
    return MountingBoundary(request['region'],request['radius_mm'],request['start_angle_deg'],request['sweep_angle_deg'])

def pool_requests(request):
    n=request['N'];R=request['radius_mm'];pitch=request['diameter_mm']+request['edge_gap_mm'];family=request['family']
    usable=R-request['diameter_mm']/2-request['wall_clearance_mm']
    if usable<0:return
    if family in ('rectangular','honeycomb'):
        half=min(18,max(2,math.ceil(min(R/pitch+1,math.sqrt(n)*1.25+2))))
        j,i=np.meshgrid(np.arange(-half,half+1),np.arange(-half,half+1));x=j.astype(float);y=i.astype(float)
        if family=='honeycomb':x+=.5*(i%2);y*=math.sqrt(3)/2
        raw=np.column_stack([x.ravel(),y.ravel()])*pitch
        start=request['start_angle_deg'];bisector=start+request['sweep_angle_deg']/2
        anchors=[(0.,0.)]
        if request['region']=='sector':
            a=math.radians(bisector)
            anchors += [(f*R*math.cos(a),f*R*math.sin(a)) for f in (.35,.65)]
        angles=sorted(set([0.,15.,30.,45.,60.,75.]+([start%180,bisector%180] if request['region']=='sector' else [])))
        for angle in angles:
            a=math.radians(angle);c,s=math.cos(a),math.sin(a);rotation=np.array([[c,s],[-s,c]])
            for sx,sy in [(0,0),(.5,0),(0,.5),(.5,.5),(.25,.25),(.375,0),(0,.375),(.75,.25)]:
                for ax,ay in anchors:
                    points=(raw+np.array([sx,sy])*pitch)@rotation+np.array([ax,ay])
                    yield points,dict(rotation_deg=angle,phase_pitch=[sx,sy],anchor_mm=[ax,ay],spacing_mm=pitch,pool_half_width=half)
    elif family=='ring':
        if n==1:
            angle=math.radians(request['start_angle_deg']+request['sweep_angle_deg']/2)
            r=0 if request['region']=='circle' else R/(1+math.sin(math.radians(min(request['sweep_angle_deg'],180)/2)))
            yield np.array([[r*math.cos(angle),r*math.sin(angle)]]),dict(radius_mm=r)
            return
        if request['region']=='circle':
            radii=[pitch/(2*math.sin(math.pi/n)),usable]
            for radius in radii:
                theta=np.arange(n)*2*math.pi/n
                yield radius*np.column_stack([np.cos(theta),np.sin(theta)]),dict(radius_mm=radius,kind='closed-ring')
        else:
            for radius in [usable*f for f in (1,.9,.75,.5)]:
                if radius<=0:continue
                inset=request['diameter_mm']/2+request['wall_clearance_mm']
                if inset>radius:continue
                padding=math.asin(inset/radius);a=math.radians(request['start_angle_deg'])+padding;b=math.radians(request['start_angle_deg']+request['sweep_angle_deg'])-padding
                if b<a:continue
                theta=np.linspace(a,b,n)
                yield radius*np.column_stack([np.cos(theta),np.sin(theta)]),dict(radius_mm=radius,kind='sector-arc')
    else:
        multiplier=360/request['sweep_angle_deg'];base=max(n,math.ceil(n*multiplier))
        counts=sorted(set(min(MAX_POOL,max(1,math.ceil(base*f))) for f in (1,1.5,2,3)))
        golden=math.pi*(3-math.sqrt(5))
        for count in counts:
            indices=np.arange(count);radius=usable*np.sqrt(indices/max(count-1,1))
            for angle in (0,15,30,45,60,75,request['start_angle_deg']):
                theta=indices*golden+math.radians(angle)
                yield np.column_stack([radius*np.cos(theta),radius*np.sin(theta)]),dict(pool_size=count,rotation_deg=angle,angular_increment_rad=golden)

def search(request):
    boundary=boundary_from(request);n=request['N'];inset=request['diameter_mm']/2+request['wall_clearance_mm'];pitch=request['diameter_mm']+request['edge_gap_mm']
    best=None;best_score=None;capacity=None;attempts=0;feasible_count=0;seeds=[]
    for raw,parameters in pool_requests(request):
        attempts+=1;clear=distances(raw,boundary)-inset;indices=np.flatnonzero(clear>=-1e-9)
        # Tie breaks are explicit; no random search or platform-dependent RNG.
        indices=sorted(indices,key=lambda i:(-round(float(clear[i]),10),round(float(raw[i,0]),10),round(float(raw[i,1]),10)))
        selected=[]
        for i in indices:
            v=raw[i]
            if request['family'] in ('sunflower','ring') and any(float(np.dot(v-w,v-w))<(pitch-1e-9)**2 for w in selected):continue
            selected.append(v)
            if len(selected)>=128:break
        points=[list(map(float,p)) for p in selected]
        if capacity is None or len(points)>len(capacity['coordinates_mm']):capacity=dict(coordinates_mm=points,parameters=parameters)
        if len(points)<n:continue
        chosen=points[:n];score=(min(boundary.signed_distance_mm(p)-inset for p in chosen),-sum(math.hypot(*p) for p in chosen))
        feasible_count+=1
        if best_score is None or score>best_score:best_score=score;best=dict(coordinates_mm=chosen,parameters=parameters)
    if best:
        validation=validate_mounting_layout(best['coordinates_mm'],boundary=boundary,N=n,outer_diameter_mm=request['diameter_mm'],edge_gap_mm=request['edge_gap_mm'],wall_clearance_mm=request['wall_clearance_mm'])
        if not validation['feasible']:raise ValueError('候选未通过独立几何核验，已停止采用：'+str(validation['failure_reasons']))
        specification=dict(engine_version=ENGINE,boundary=boundary.to_dict(),nozzle=dict(outer_diameter_mm=request['diameter_mm'],edge_gap_mm=request['edge_gap_mm'],wall_clearance_mm=request['wall_clearance_mm']),N=n,layout=dict(family=request['family'],parameters=best['parameters']),tolerance_mm=1e-9)
        layout_id='mounting_'+hashlib.sha256(json.dumps(specification,sort_keys=True).encode()).hexdigest()
        best=dict(layout_id=layout_id,specification=specification,coordinates_mm=best['coordinates_mm'],validation=validation,metrics=dict(minimum_boundary_margin_mm=validation['minimum_boundary_margin_mm'],boundary_area_mm2=boundary.area_mm2),selected=True,estimated_power_kW=None,target_met_by_model=None)
    return dict(candidate=best,capacity=capacity,attempts=attempts,feasible_count=feasible_count,engine=ENGINE,global_optimum_proven=False)
