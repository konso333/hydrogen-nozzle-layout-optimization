"""Analytic variable-composition conduction diagnostic; not a nozzle-flow model.

Y varies across y in four strips. T varies only along x, so T=300+1000*x
solves div(k(y)*grad(T))=0. Actual wall heat, not a self-reported UDF value,
independently identifies the conductivity in each strip.
"""
import numpy as np
from nozzle_platform.transport_audit import evaluate

H2_LEVELS=np.array([0.,.1,.5,1.])
LENGTH=.01


def saved_energy_controls(case_path):
    """Require the actual saved energy-only diagnostic, not controller intent."""
    import h5py,re
    with h5py.File(case_path) as f:text=f['settings/Rampant Variables'][0].decode()
    expected={'flow/solve?':'#f','species/solve?':'(#f #f #f)',
              'temperature/solve?':'#t','energy/species-diffusion?':'#f'}
    for key,value in expected.items():
        matches=re.findall(r'\('+re.escape(key)+r'\s+(\([^()]*\)|[^()\s]+)\)',text)
        if matches!=[value]:raise ValueError('Unexpected saved diagnostic control: '+key)
    return expected


def patch_schedule(h2):
    if isinstance(h2,bool) or not np.isfinite(h2) or not 0<=h2<=1:
        raise ValueError('Invalid target H2 fraction')
    return [(1,.233*(1-h2)),(2,0.),(0,float(h2))]


def energy_history(path):
    import h5py
    with h5py.File(path) as f:
        group=f['results/residuals/phase-1/energy'];ids=group['iterations'][()];values=group['data'][()]
    if ids.ndim!=1 or not len(ids) or not np.isfinite(ids).all() or not np.isfinite(values).all() or values.shape!=(len(ids),4):
        raise ValueError('Invalid complete energy residual history')
    if np.any(ids!=ids.astype(int)) or np.any(np.diff(ids)<=0):raise ValueError('Invalid energy iteration IDs')
    return int(ids[-1]),ids.astype(int),values


def band_indices(y):
    y=np.asarray(y,dtype=float)
    if y.ndim!=1 or not len(y) or not np.isfinite(y).all() or y.min()<0 or y.max()>LENGTH:
        raise ValueError('Expected y positions in the 10 mm diagnostic box')
    return np.minimum((y/(LENGTH/4)).astype(int),3)


def prescribed_y(centres):
    xyz=np.asarray(centres,dtype=float)
    if xyz.ndim!=2 or xyz.shape[1]!=3 or not np.isfinite(xyz).all() or np.any(xyz<0) or np.any(xyz>LENGTH):
        raise ValueError('Invalid diagnostic cell positions')
    h2=H2_LEVELS[band_indices(xyz[:,1])]
    return np.column_stack([h2,.233*(1-h2),np.zeros_like(h2),.767*(1-h2)])


def face_heat_check(roles,areas,bands,heat,conductivity,relative_limit=1e-5):
    roles=np.asarray(roles);areas=np.asarray(areas,dtype=float);bands=np.asarray(bands)
    heat=np.asarray(heat,dtype=float);k=np.asarray(conductivity,dtype=float)
    n=len(roles)
    if roles.shape!=(n,) or any(a.shape!=(n,) for a in [areas,bands,heat]) or k.shape!=(4,):
        raise ValueError('Mismatched complete wall fields')
    if not np.isfinite(areas).all() or np.any(areas<=0) or not np.isfinite(heat).all() or not np.isfinite(k).all() or np.any(k<=0):
        raise ValueError('Nonfinite or nonpositive heat-check inputs')
    if not np.issubdtype(bands.dtype,np.integer) or np.any(bands<0) or np.any(bands>3) or set(roles)!={'cold','hot','insulated'}:
        raise ValueError('Unknown strip or wall role')
    if not 0<relative_limit<1:raise ValueError('Invalid diagnostic tolerance')
    expected=k[bands]*areas*1000.
    heated=roles!='insulated'
    error=float(np.max(abs(abs(heat[heated])-expected[heated])/expected[heated]))
    signs=bool(np.all(heat[roles=='cold']<0) and np.all(heat[roles=='hot']>0))
    total_expected=float(expected[roles=='hot'].sum())
    imbalance=float(abs(heat.sum())/total_expected)
    insulated=float(abs(heat[roles=='insulated']).sum()/total_expected)
    strip=[]
    for b in range(4):
        row=dict(band=b,expected_k_W_m_K=float(k[b]))
        for role in ['cold','hot']:
            selected=(bands==b)&(roles==role)
            if not selected.any():raise ValueError('A strip wall is missing')
            area=float(areas[selected].sum());q=float(heat[selected].sum())
            row[role+'_area_m2']=area;row[role+'_heat_W']=q
            row[role+'_inferred_k_W_m_K']=abs(q)/(1000.*area)
        strip.append(row)
    return dict(passed=bool(error<=relative_limit and signs and imbalance<=relative_limit and insulated<=relative_limit),
        maximum_relative_face_heat_error=error,relative_heat_imbalance=imbalance,relative_insulated_heat=insulated,
        correct_heat_signs=signs,relative_limit=relative_limit,strips=strip,
        boundary_heat_W={role:float(heat[roles==role].sum()) for role in ['cold','hot','insulated']})


def read_heat_faces(case_path,data_path):
    import h5py
    with h5py.File(case_path) as c,h5py.File(data_path) as d:
        mesh=c['meshes/1'];top=mesh['faces/zoneTopology']
        names=top['name'][()][0].decode().split(';')
        if len(names)!=4 or set(names)!={'interior','cold','hot','insulated'}:raise ValueError('Unexpected diagnostic walls')
        counts=mesh['faces/nodes/1/nnodes'][()].astype(int)
        nodes=mesh['faces/nodes/1/nodes'][()].astype(int)-1
        points=mesh['nodes/coords/1'][()];offsets=np.r_[0,np.cumsum(counts)]
        heat_group=d['results/1/phase-1/faces/SV_HEAT_FLUX']
        if set(heat_group)!={'1'}:raise ValueError('Chunked saved wall heat rejected')
        ds=heat_group['1'];first,last=(int(ds.attrs[n][0]) for n in ['minId','maxId']);heat=ds[()].reshape(-1)
        if len(heat)!=last-first+1:raise ValueError('Partial saved wall heat')
        roles=[];areas=[];bands=[];values=[];centres=[]
        for role,a,b in zip(names,top['minId'][()],top['maxId'][()]):
            if role=='interior':continue
            if not first<=a<=b<=last:raise ValueError('Wall heat IDs do not cover every boundary')
            for face in range(int(a)-1,int(b)):
                if counts[face]!=4:raise ValueError('Expected rectangular quadrilateral boundary faces')
                ids=nodes[offsets[face]:offsets[face+1]]
                if ids.min()<0 or ids.max()>=len(points):raise ValueError('Invalid wall connectivity')
                p=points[ids];centre=p.mean(axis=0)
                area=float(np.linalg.norm(np.cross(p[1]-p[0],p[3]-p[0])))
                if role in ['cold','hot']:
                    expected_x=0. if role=='cold' else LENGTH
                    if np.max(abs(p[:,0]-expected_x))>1e-12:raise ValueError('Heated wall is not planar at the required x')
                roles.append(role);areas.append(area);bands.append(int(band_indices([centre[1]])[0]))
                values.append(float(heat[face+1-first]));centres.append(centre)
    return dict(roles=np.array(roles),areas=np.array(areas),bands=np.array(bands),heat=np.array(values),centres=np.array(centres))


def assess_saved(case_path,data_path,material,law='ideal'):
    from nozzle_platform.transport_benchmark import saved_fields
    if law not in ['ideal','mass']:raise ValueError('Unknown comparison law')
    if material['names']!=['h2','o2','h2o','n2']:raise ValueError('Unexpected species order')
    saved=saved_fields(case_path,data_path);xyz=saved['cell_centres_m'];y=saved['fields']['SV_Y']
    expected_y=prescribed_y(xyz)
    if saved['cells']!=256 or not np.allclose(saved['bounds_m'],[[0,0,0],[LENGTH]*3],rtol=0,atol=1e-12):
        raise ValueError('Only the documented 256-cell diagnostic is supported')
    if np.max(abs(y-expected_y))>1e-12:raise ValueError('Prescribed species changed; values are not repaired')
    if any(np.max(abs(saved['fields'][n]))>1e-12 for n in ['SV_U','SV_V','SV_W']):raise ValueError('Velocity is not zero')
    groups=band_indices(xyz[:,1]);population=np.bincount(groups,minlength=4)
    if not np.array_equal(population,[64]*4):raise ValueError('Incomplete strip cell populations')
    values=evaluate(prescribed_y(np.column_stack([np.zeros(4),(np.arange(4)+.5)*LENGTH/4,np.zeros(4)])),material)
    faces=read_heat_faces(case_path,data_path)
    check=face_heat_check(faces['roles'],faces['areas'],faces['bands'],faces['heat'],values[law+'_conductivity'])
    error=float(abs(saved['fields']['SV_T'].reshape(-1)-(300.+1000.*xyz[:,0])).max())
    check.update(cells=saved['cells'],species_profile_max_error=float(abs(y-expected_y).max()),
        maximum_temperature_error_K=error,cell_population_per_strip=population.tolist(),law=law,
        passed=bool(check['passed'] and error<1e-5),conductivity_response_verified=bool(check['passed'] and error<1e-5),
        formal_physics_validated=False,scientific_eligible=False,grid_independence_verified=False,
        scope='Frozen nonuniform composition, zero velocity, energy-only diagnostic; not a nozzle solution')
    return check
