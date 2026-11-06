"""M27 diagnostic counter-diffusion, isolated from production physics/gates.

Zero velocity, equal constant molecular diffusivities, isothermal mixture.
rho = C/(a+b*Y_H2), so log(a+b*Y_H2) is linear at steady state.
All fluxes are inward-positive; total bulk mass flow is zero.
"""
import math,re
from pathlib import Path
import numpy as np

SPECIES=('h2','o2','h2o','n2')
MW=np.array([2.01594,31.9988,18.01534,28.0134])
D=1e-4
L=.01
AREA=1e-4
LEFT=.1
RIGHT=.15
C=101325./(8314.46261815324*300.)
A=.1/MW[1]+.1/MW[2]+.8/MW[3]
B=1/MW[0]-1/MW[3]
PROFILE_TOL=2e-4
FLUX_REL_TOL=.01  # Declared coarse-grid truncation allowance, not a nozzle gate.
Q=D*C/B*math.log((A+B*RIGHT)/(A+B*LEFT))*AREA/L
TOL={'SV_Y':1e-12,'SV_U':1e-10,'SV_V':1e-10,'SV_W':1e-10,'SV_P':1e-8,'SV_T':1e-9,'SV_DENSITY':1e-12,'SV_FLUX':1e-15}

def field_difference(before,after):
    import h5py,numpy as np
    checks=[]
    with h5py.File(before) as a,h5py.File(after) as b:
        for location in ('cells','faces'):
            ga=a['results/1/phase-1/'+location];gb=b['results/1/phase-1/'+location]
            for name,tol in TOL.items():
                if location=='cells' and name=='SV_FLUX':continue
                if name not in ga or name not in gb:raise ValueError('Missing required field '+location+'/'+name)
                if set(ga[name])!=set(gb[name]):raise ValueError('Field chunk coverage differs')
                for key in ga[name]:
                    da=ga[name][key];db=gb[name][key]
                    if da.shape!=db.shape or any(not np.array_equal(da.attrs[k],db.attrs[k]) for k in ('minId','maxId')):raise ValueError('Field IDs/shapes differ')
                    va=da[()];vb=db[()]
                    if not np.isfinite(va).all() or not np.isfinite(vb).all():raise ValueError('Nonfinite field')
                    error=float(np.max(np.abs(va-vb))) if va.size else 0.
                    checks.append(dict(location=location,field=name,chunk=key,maximum_absolute_change=error,tolerance=tol,exactly_equal=bool(np.array_equal(va,vb)),within_tolerance=error<=tol))
    return dict(all_within_declared_tolerances=all(r['within_tolerance'] for r in checks),all_exact=all(r['exactly_equal'] for r in checks),checks=checks)

def profile(x):
    x=np.asarray(x,dtype=float)
    if not np.isfinite(x).all() or (x<0).any() or (x>L).any():raise ValueError('x outside slab')
    h=(np.exp(np.log(A+B*LEFT)+x/L*np.log((A+B*RIGHT)/(A+B*LEFT)))-A)/B
    return np.stack([h,np.full_like(h,.1),np.full_like(h,.1),.8-h],axis=-1)

def reference_rates():
    return {'inlet':[-Q,0.,0.,Q],'outlet':[Q,0.,0.,-Q]}

def mesh(path):
    from nozzle_platform.species_flux_benchmark import channel_mesh
    p=Path(path);metadata=channel_mesh(p);text=p.read_text(encoding='ascii')
    text,count=re.subn(r'\(13 \(5 ([0-9a-f]+) ([0-9a-f]+) 5 4\)\(',r'(13 (5 \1 \2 a 4)(',text)
    if count!=1 or text.count('(39 (5 pressure-outlet outlet 1)())')!=1:raise ValueError('Unexpected slab boundary')
    p.write_text(text.replace('(39 (5 pressure-outlet outlet 1)())','(39 (5 velocity-inlet outlet 1)())'),encoding='ascii',newline='\n')
    for key in ('density_kg_m3','inlet_velocity_m_s','expected_mass_flow_kg_s'):metadata.pop(key,None)
    metadata.update(case_scope='M27 constant-D diagnostic; two zero-velocity inlets, inlet diffusion ON; isothermal counter-diffusion',mass_diffusivity_m2_s=D,analytic_species_rates_kg_s=reference_rates())
    return metadata

def geometry(case):
    import h5py
    with h5py.File(case) as h:
        g=h['meshes/1'];pts=g['nodes/coords/1'][()]
        nn=g['faces/nodes/1/nnodes'][()].astype(int);ids=g['faces/nodes/1/nodes'][()].astype(int)-1
        c0=g['faces/c0/1'][()].astype(int)-1
        c1=np.full(len(c0),-1,dtype=int);ds=g['faces/c1/1'];start=int(ds.attrs['minId'][0])-1;c1[start:start+len(ds)]=ds[()].astype(int)-1
        vertices=[set() for _ in range(256)];offset=np.r_[0,np.cumsum(nn)]
        for f,(i,j) in enumerate(zip(c0,c1)):
            nodes=ids[offset[f]:offset[f+1]]
            if not 0<=i<256 or not -1<=j<256 or len(nodes)!=4:raise ValueError('Unexpected topology')
            vertices[i].update(nodes)
            if j>=0:vertices[j].update(nodes)
        if any(len(v)!=8 for v in vertices):raise ValueError('Expected 256 hexahedra')
        return np.array([pts[sorted(v)].mean(axis=0) for v in vertices])

def state(case,data):
    import h5py
    centres=geometry(case);reference=profile(centres[:,0]);fields={}
    with h5py.File(data) as h:
        for location in ('cells','faces'):
            g=h['results/1/phase-1/'+location]
            for name in ('SV_Y','SV_U','SV_V','SV_W','SV_P','SV_T','SV_DENSITY')+(('SV_FLUX',) if location=='faces' else ()):
                group=g[name]
                if set(group)!={'1'}:raise ValueError('Unsupported field chunks')
                ds=group['1'];v=ds[()]
                if len(v)!=int(ds.attrs['maxId'][0])-int(ds.attrs['minId'][0])+1 or not np.isfinite(v).all():raise ValueError('Incomplete/nonfinite field')
                fields[location+'/'+name]=v
        y=fields['cells/SV_Y'];yf=fields['faces/SV_Y']
        if y.shape!=(256,4) or yf.ndim!=2 or yf.shape[1]!=4:raise ValueError('Species shape')
        result=dict(cell_species_sum_error=float(abs(y.sum(axis=1)-1).max()),face_species_sum_error=float(abs(yf.sum(axis=1)-1).max()),
            minimum_Y=min(float(y.min()),float(yf.min())),maximum_Y=max(float(y.max()),float(yf.max())),
            maximum_analytic_profile_error=float(abs(y-reference).max()),h2_range=[float(y[:,0].min()),float(y[:,0].max())],
            maximum_speed_component=max(float(abs(fields['cells/'+n]).max()) for n in ('SV_U','SV_V','SV_W')),
            maximum_temperature_error_K=float(abs(fields['cells/SV_T']-300.).max()),
            maximum_density_formula_relative_error=float(abs(fields['cells/SV_DENSITY'].reshape(-1)/(C/(y/MW).sum(axis=1))-1).max()))
    result['passed']=bool(result['cell_species_sum_error']<=1e-8 and result['face_species_sum_error']<=1e-8 and result['minimum_Y']>=-1e-12 and result['maximum_Y']<=1+1e-12 and result['maximum_analytic_profile_error']<=PROFILE_TOL and result['maximum_speed_component']<=1e-8 and result['maximum_temperature_error_K']<=1e-8 and result['maximum_density_formula_relative_error']<=2e-6 and np.ptp(y[:,0])>.04)
    return result

def assess_report(parsed):
    checks=[]
    for boundary,wanted in reference_rates().items():
        for name,value,target in zip(SPECIES,parsed['boundary_rates_kg_s'][boundary],wanted):
            checks.append(dict(boundary=boundary,species=name,value=value,reference=target,absolute_tolerance=Q*FLUX_REL_TOL,passed=abs(value-target)<=Q*FLUX_REL_TOL))
    return dict(passed=all(r['passed'] for r in checks),checks=checks)

def reconstruct_boundary_fluxes(case,data):
    """Independent saved-file two-point flux on this orthogonal slab only.

    Includes convection and Fick diffusion; D is the configured diagnostic
    constant. No native species-flux cache or native reports are inputs.
    """
    import h5py
    centres=geometry(case);rates={};convective={}
    with h5py.File(case) as c,h5py.File(data) as d:
        g=c['meshes/1'];points=g['nodes/coords/1'][()]
        nn=g['faces/nodes/1/nnodes'][()].astype(int);nodes=g['faces/nodes/1/nodes'][()].astype(int)-1;offset=np.r_[0,np.cumsum(nn)]
        adjacent=g['faces/c0/1'][()].astype(int)-1
        y=d['results/1/phase-1/cells/SV_Y/1'][()];rho=d['results/1/phase-1/cells/SV_DENSITY/1'][()].reshape(-1)
        flux=d['results/1/phase-1/faces/SV_FLUX/1'];first=int(flux.attrs['minId'][0])-1;bulk=flux[()].reshape(-1)
        top=g['faces/zoneTopology'];names=top['name'][()][0].decode().split(';')
        for name,lo,hi in zip(names,top['minId'][()],top['maxId'][()]):
            if name not in ('inlet','outlet'):continue
            h=LEFT if name=='inlet' else RIGHT;yb=np.array([h,.1,.1,.8-h]);total=np.zeros(4);adv=np.zeros(4);area_sum=0.
            for f in range(int(lo)-1,int(hi)):
                p=points[nodes[offset[f]:offset[f+1]]];xc=centres[adjacent[f]];xf=p.mean(axis=0)
                area=float(np.linalg.norm(np.cross(p[1]-p[0],p[3]-p[0])));distance=float(abs(xf[0]-xc[0]))
                if len(p)!=4 or not np.allclose(xf[1:],xc[1:],atol=1e-14,rtol=0) or distance<=0:raise ValueError('Nonorthogonal boundary unsupported')
                if not first<=f<first+len(bulk):raise ValueError('Missing boundary bulk flux')
                q=-bulk[f-first];adv+=q*yb;total+=q*yb+rho[adjacent[f]]*D*(yb-y[adjacent[f]])/distance*area;area_sum+=area
            if int(hi)-int(lo)+1!=16 or abs(area_sum-AREA)>1e-16:raise ValueError('Boundary coverage/area mismatch')
            rates[name]=total.tolist();convective[name]=adv.tolist()
    if set(rates)!={'inlet','outlet'}:raise ValueError('Missing boundary')
    return dict(boundary_rates_kg_s=rates,convective_rates_kg_s=convective,net_kg_s=np.sum(list(rates.values()),axis=0).tolist())

def boundary_density_diagnostic(case,data):
    """Describe face density conventions; does not change predeclared gates."""
    import h5py
    rows=[]
    with h5py.File(case) as c,h5py.File(data) as d:
        top=c['meshes/1/faces/zoneTopology'];names=top['name'][()][0].decode().split(';')
        g=d['results/1/phase-1/faces'];ds=g['SV_DENSITY/1'];first=int(ds.attrs['minId'][0])
        if first!=int(g['SV_Y/1'].attrs['minId'][0]):raise ValueError('Different face coverage')
        rho=ds[()].reshape(-1);y=g['SV_Y/1'][()];cellrho=d['results/1/phase-1/cells/SV_DENSITY/1'][()].reshape(-1)
        if not len(rho):return [dict(status='not_populated',reason='Initial zero-iteration file contains an empty face-density dataset')]
        if len(rho)!=len(y):raise ValueError('Different face density/species coverage')
        adjacent=c['meshes/1/faces/c0/1'][()].astype(int)-1
        for name,lo,hi in zip(names,top['minId'][()],top['maxId'][()]):
            if name=='interior':continue
            a=int(lo)-first;b=int(hi)-first+1
            if a<0 or b>len(rho):
                rows.append(dict(zone=name,status='not_covered',reason='This saved dataset does not cover this zone; no density comparison claimed'))
                continue
            v=rho[a:b];reference=C/(y[a:b]/MW).sum(axis=1);neighbour=cellrho[adjacent[int(lo)-1:int(hi)]]
            rows.append(dict(zone=name,rho_range=[float(v.min()),float(v.max())],formula_range=[float(reference.min()),float(reference.max())],
                maximum_relative_formula_error=float(abs(v/reference-1).max()),maximum_absolute_adjacent_cell_density_difference=float(abs(v-neighbour).max())))
    return rows

def physical_signature(config,diffusion_flags):
    """This slab has TWO velocity inlets; no pressure-outlet assumptions."""
    m=config['models'];b=config['boundary_conditions'];options=m['species']['options']
    if m['viscous']['model']!='laminar' or m['species']['reactions']['enable_volumetric_reactions'] is not False:raise ValueError('Wrong physics')
    effective={k:options[k] for k in ('inlet_diffusion','diffusion_energy_source')}
    for key in ('thermal_diffusion','multi_component_diffusion'):
        effective[key]=options[key] if key in options else diffusion_flags[key]
        if type(effective[key]) is not bool:raise ValueError('Missing native diffusion evidence')
    if effective!={'inlet_diffusion':True,'diffusion_energy_source':False,'thermal_diffusion':False,'multi_component_diffusion':False}:raise ValueError('Wrong diffusion options')
    if set(b['velocity_inlet'])!={'inlet','outlet'} or set(b.get('pressure_outlet',{})):raise ValueError('Wrong boundary types')
    boundaries={}
    for name in ('inlet','outlet'):
        bc=b['velocity_inlet'][name]
        boundaries[name]=dict(momentum={k:bc['momentum'][k] for k in ('velocity_specification_method','reference_frame','velocity_magnitude')},thermal=bc['thermal'],species=bc['species'])
    return dict(general=config['general'],materials=config['materials'],energy=m['energy'],species_model=m['species']['model'],species_options=effective,boundaries=boundaries,symmetry_names=sorted(b['symmetry']))

def render_udf():
    return r'''#include "udf.h"
#define D27 1e-4
#define A27 %.17g
#define B27 %.17g
#define C27 %.17g
/* Explicit initial conditions only; never called in GPU/handoff workers. */
DEFINE_ON_DEMAND(m27_seed)
{
#if !RP_HOST
 Domain *d=Get_Domain(1);Thread *t;cell_t c;real x[ND_ND],h;
 thread_loop_c(t,d) { begin_c_loop(c,t) {
  C_CENTROID(x,c,t);h=(exp(log(A27+B27*.1)+x[0]/.01*log((A27+B27*.15)/(A27+B27*.1)))-A27)/B27;
  C_YI(c,t,0)=h;C_YI(c,t,1)=.1;C_YI(c,t,2)=.1;C_YI(c,t,3)=.8-h;
  C_U(c,t)=0;C_V(c,t)=0;C_W(c,t)=0;C_P(c,t)=0;C_T(c,t)=300;C_R(c,t)=C27/(A27+B27*h);
 } end_c_loop(c,t) }
#endif
}
/* Read-only independent two-point boundary diffusion reconstruction. */
DEFINE_ON_DEMAND(m27_probe)
{
#if !RP_HOST
 Domain *d=Get_Domain(1);int zid,i;
 for(zid=4;zid<=5;++zid) {
  Thread *t=Lookup_Thread(d,zid),*tc;face_t f;cell_t c;
  real cache[3]={0,0,0},pred[3]={0,0,0},conv[3]={0,0,0},q=0,area[ND_ND],xf[ND_ND],xc[ND_ND];
  real yb[3]={.1,.1,.1},dist,ar,di,dmin=1e30,dmax=-1e30;int count=0;
  if(zid==5)yb[0]=.15;
  if(t==NULL) {Message0("M27_MISSING_ZONE\n");continue;}
  tc=THREAD_T0(t);
  if(!SV_ALLOCATED_P(t,SV_SPECIES_FLUX)||sv_master[SV_SPECIES_FLUX].dimension!=SV_DIM_NSPE||!SV_ALLOCATED_P(t,SV_FLUX)||!SV_ALLOCATED_P(tc,SV_Y)) {Message0("M27_STORAGE_UNAVAILABLE zone=%%d\n",zid);continue;}
  begin_f_loop(f,t) {
   c=F_C0(f,t);F_CENTROID(xf,f,t);C_CENTROID(xc,c,tc);F_AREA(area,f,t);ar=NV_MAG(area);dist=fabs(xc[0]-xf[0]);q-=F_FLUX(f,t);count++;
   for(i=0;i<3;++i) {
    di=D27;
    if(SV_ALLOCATED_P(tc,SV_DIFF_COEFF)&&sv_master[SV_DIFF_COEFF].dimension==SV_DIM_NSPE)di=C_DIFF_L(c,tc,i,i);
    dmin=MIN(dmin,di);dmax=MAX(dmax,di);
    cache[i]+=F_STORAGE_R_XV(f,t,SV_SPECIES_FLUX,i);
    conv[i]-=F_FLUX(f,t)*yb[i];pred[i]-=F_FLUX(f,t)*yb[i];
    pred[i]+=C_R(c,tc)*di*(yb[i]-C_YI(c,tc,i))/dist*ar;
   }
  } end_f_loop(f,t)
  Message0("M27_FLUX zone=%%d faces=%%d total=%%.17g Dmin=%%.17g Dmax=%%.17g\n",zid,count,q,dmin,dmax);
  for(i=0;i<3;++i)Message0("M27_COMPONENT zone=%%d i=%%d cache=%%.17g reconstructed=%%.17g convection=%%.17g\n",zid,i,cache[i],pred[i],conv[i]);
 }
#endif
}
''' % (A,B,C)
