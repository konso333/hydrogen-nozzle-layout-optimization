"""Scoped saved-field diagnostics for bounded original-law GPU continuation."""
import numpy as np
from nozzle_platform.nozzle_gpu_probe import _field,read_saved


FLAGS=dict(formal_physics_validated=False,numerical_criteria_met=False,
    scientific_eligible=False,grid_independence_verified=False,
    CPU_GPU_converged_nozzle_agreement=False,real_nozzle_conductivity_field_validated=False)


def alignment_bins(alignment,bad):
    a=np.asarray(alignment,float);b=np.asarray(bad)
    if a.ndim!=1 or b.shape!=a.shape or b.dtype!=bool or not np.isfinite(a).all() or np.any(a<0) or np.any(a>1):
        raise ValueError('Invalid complete geometry and rejection arrays')
    bins=np.array([0,.1,.2,.4,.6,.8,1.00000001])
    total=np.histogram(a,bins)[0];rejected=np.histogram(a[b],bins)[0]
    return [dict(lower=float(bins[i]),upper=min(float(bins[i+1]),1.),cells=int(n),
        rejected_cells=int(rejected[i]),rejected_fraction=float(rejected[i]/n) if n else None) for i,n in enumerate(total)]


def saved_geometry(case_path):
    """Reconstruct positions and a geometric proxy, not Fluent orthogonal quality.

    Proxy = minimum absolute cosine between a face normal and its two cell
    centres (boundary: cell centre to face centre). Polygon fans are streamed.
    """
    import h5py
    from nozzle_platform.field_profiles import cell_centres
    from coldflow_trial.solver import mesh_fingerprint
    centres,volume=cell_centres(case_path)
    with h5py.File(case_path) as f:
        m=f['meshes/1'];points=m['nodes/coords/1'][()]
        counts=m['faces/nodes/1/nnodes'][()].astype(np.int64)
        nodes=m['faces/nodes/1/nodes'][()].astype(np.int64)-1
        owner=m['faces/c0/1'][()].astype(np.int64)-1
        ds=m['faces/c1/1'];start=int(ds.attrs['minId'][0])-1
        neighbour=np.full(len(owner),-1,dtype=np.int64);neighbour[start:start+len(ds)]=ds[()].astype(np.int64)-1
        top=m['faces/zoneTopology'];names=top['name'][()][0].decode().split(';')
        ranges={n:(int(a),int(b)) for n,a,b in zip(names,top['minId'][()],top['maxId'][()]) if n!='interior'}
    if len(owner)!=len(counts) or np.any(counts<3) or nodes.min()<0 or nodes.max()>=len(points):raise ValueError('Invalid complete face topology')
    if owner.min()<0 or owner.max()>=len(centres) or neighbour.max()>=len(centres):raise ValueError('Invalid cell owner IDs')
    offsets=np.r_[0,np.cumsum(counts)];alignment=np.ones(len(centres))
    boundary={n:dict(ids=np.arange(a,b+1),areas=np.empty(b-a+1),centres=np.empty((b-a+1,3))) for n,(a,b) in ranges.items()}
    for first in range(0,len(counts),20000):
        faces=np.arange(first,min(first+20000,len(counts)));triangles=counts[faces]-2
        ids=np.repeat(np.arange(len(faces)),triangles);anchor=np.repeat(offsets[faces],triangles)
        local=np.arange(triangles.sum())-np.repeat(np.r_[0,np.cumsum(triangles)[:-1]],triangles)
        p,q,r=points[nodes[anchor]],points[nodes[anchor+1+local]],points[nodes[anchor+2+local]]
        cross=np.cross(q-p,r-p)/2;area=np.linalg.norm(cross,axis=1)
        areas=np.bincount(ids,weights=area,minlength=len(faces))
        normals=np.column_stack([np.bincount(ids,weights=cross[:,j],minlength=len(faces)) for j in range(3)])
        fc=np.column_stack([np.bincount(ids,weights=area*(p[:,j]+q[:,j]+r[:,j])/3,minlength=len(faces)) for j in range(3)])/areas[:,None]
        internal=neighbour[faces]>=0;direction=fc-centres[owner[faces]]
        direction[internal]=centres[neighbour[faces[internal]]]-centres[owner[faces[internal]]]
        denominator=np.linalg.norm(normals,axis=1)*np.linalg.norm(direction,axis=1)
        if np.any(areas<=0) or np.any(denominator<=0) or not np.isfinite(fc).all():raise ValueError('Degenerate saved face geometry')
        cosine=np.minimum(abs(np.einsum('ij,ij->i',normals,direction))/denominator,1.)
        np.minimum.at(alignment,owner[faces],cosine)
        np.minimum.at(alignment,neighbour[faces[internal]],cosine[internal])
        for role,(a,b) in ranges.items():
            mask=(faces>=a-1)&(faces<b);index=faces[mask]-(a-1)
            boundary[role]['areas'][index]=areas[mask];boundary[role]['centres'][index]=fc[mask]
    return dict(centres=centres,alignment=alignment,boundary=boundary,volume_m3=volume,mesh_fingerprint=mesh_fingerprint(case_path))


def write_geometry(path,geometry):
    arrays=dict(centres=geometry['centres'],alignment=geometry['alignment'],volume_m3=geometry['volume_m3'],mesh_fingerprint=geometry['mesh_fingerprint'])
    for role,values in geometry['boundary'].items():
        for key,value in values.items():arrays[role+'_'+key]=value
    np.savez(path,**arrays)


def static_pressure_drops(case_path,data_path,geometry_path):
    """Area-weighted saved static gauge pressure; not Fluent total pressure."""
    import h5py
    from coldflow_trial.solver import mesh_fingerprint
    result={}
    with h5py.File(data_path) as f,np.load(geometry_path) as g:
        if mesh_fingerprint(case_path)!=str(g['mesh_fingerprint']):raise ValueError('Geometry cache mesh changed')
        group=f['results/1/phase-1/faces'];ds=group['SV_P/1']
        first,last=(int(ds.attrs[k][0]) for k in ['minId','maxId'])
        values=_field(group,'SV_P',first,last)
        for role in ['fuel_inlet','oxidizer_inlet','outlet']:
            ids=g[role+'_ids'];areas=g[role+'_areas']
            if ids.min()<first or ids.max()>last or not np.isfinite(areas).all() or np.any(areas<=0):raise ValueError('Incomplete saved pressure or area')
            result[role]=float(np.average(values[ids-first],weights=areas))
    return {n:result[n]-result['outlet'] for n in ['fuel_inlet','oxidizer_inlet']}


def relative_drift(current,previous):
    a=np.asarray(current,float);b=np.asarray(previous,float)
    if a.shape!=b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('Mismatched or nonfinite saved fields')
    norm=float(np.linalg.norm(b))
    return float(np.linalg.norm(a-b)/norm) if norm>1e-12 else None


def require_batch(audit,expected_input_hash,baseline):
    """Fail closed before another GPU batch can start."""
    if audit.get('status')!='saved_probe_audited' or not audit.get('nonuniform_original_viscosity_evidence_verified'):
        raise ValueError('Independent viscosity/field audit rejected')
    if not audit.get('restart') or audit.get('initial_iteration')!=baseline or audit.get('saved_iteration')!=baseline+25 or audit.get('complete_added_residuals')!=25:
        raise ValueError('Wrong restart interval')
    if audit['files_sha256']['input.dat.h5']!=expected_input_hash:raise ValueError('Restart input chain changed')
    controls=audit['saved_solver_controls']
    if controls.get('species_relax')!=[.1]*3 or controls.get('species_scheme')!=[1]*3 or controls.get('linear_absolute_tolerance_double')!=1e-20:
        raise ValueError('Saved numerical settings changed')
    for key,value in [('species_amg_alpha',1e-6),('species_iter_tolerance',1e-6),('species_max_iter',100.)]:
        if controls.get(key)!=[value]*3:raise ValueError('Strict species controls changed')
    stages=audit['saved_fields']
    if set(stages)!={'first','second','third','penultimate','final'}:raise ValueError('Incomplete checkpoints')
    for row in stages.values():
        error=row.get('species_sum_max_error')
        if row.get('cells')!=851803 or not row.get('all_required_cell_fields_finite') or not isinstance(error,(int,float)) or not np.isfinite(error) or not 0<=error<=1e-8:
            raise ValueError('Incomplete or rejected saved cell fields')


def batch_budget(value):
    if type(value) is not int or value not in [1,2,3,4]:raise ValueError('Choose 1 to 4 batches of 25 steps; maximum 100')
    return value*25


def observe_batch(folder,previous,geometry_path,history):
    from pathlib import Path
    from coldflow_trial.solver import parse_residual_history,saved_iteration
    from nozzle_platform.convergence import VERIFICATION_CRITERIA,checks
    folder,previous=Path(folder),Path(previous)
    a=read_saved(folder/'final.cas.h5',folder/'final.dat.h5')
    b=read_saved(previous/'final.cas.h5',previous/'final.dat.h5')
    start=saved_iteration(folder/'input.dat.h5');end=saved_iteration(folder/'final.dat.h5')
    rows=parse_residual_history((folder/'journal.trn').read_text(encoding='utf-8',errors='replace'),start,end)
    pressure=static_pressure_drops(folder/'final.cas.h5',folder/'final.dat.h5',geometry_path)
    old=static_pressure_drops(previous/'final.cas.h5',previous/'final.dat.h5',geometry_path)
    drift=max(abs(pressure[k]-old[k])/max(abs(pressure[k]),1.) for k in pressure)
    point=dict(iteration=end,block_steps=25,block_maximum_residuals={k:max(r[k] for r in rows.values()) for k in VERIFICATION_CRITERIA['scaled_residuals']},
        relative_mass_imbalance=a['summary']['relative_mass_imbalance'],h2_relative_convective_imbalance=a['summary']['h2_relative_convective_imbalance'],
        area_weighted_static_pressure_drop_Pa=pressure,relative_static_pressure_drop_change=drift,
        cell_h2_relative_l2_drift=relative_drift(a['y'][:,0],b['y'][:,0]),
        cell_velocity_relative_l2_drift=relative_drift(np.column_stack([a['fields'][n] for n in ['SV_U','SV_V','SV_W']]),np.column_stack([b['fields'][n] for n in ['SV_U','SV_V','SV_W']])),
        relative_pressure_drop_change=None,outlet_profile_relative_drift=None,interior_profile_relative_drift=None)
    # Static pressure and unweighted cell L2 are useful trends, but do not
    # substitute the formal total-pressure and common-section profile checks.
    point['criteria_met']=all(r['passed'] for r in checks(point,VERIFICATION_CRITERIA))
    return dict(point=point,strict_assessment=study_assessment(history+[point]),**FLAGS)


def study_assessment(history):
    from nozzle_platform.convergence import assessment,VERIFICATION_CRITERIA
    added=0;local=[]
    for point in history:
        added+=point['block_steps'];local.append(dict(point,iteration=added))
    return assessment(local,VERIFICATION_CRITERIA,200,3)
