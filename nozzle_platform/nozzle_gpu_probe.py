"""Read-only checks for a bounded native-GPU nonuniform nozzle experiment.

Never enables the production GPU or grid-independence paths. Saved viscosity is
compared against current AND explicitly saved preceding composition; neither
comparison alone certifies a converged solution or thermal conductivity.
"""
import numpy as np
from nozzle_platform.transport_audit import evaluate, differences


def saved_solver_controls(case_path):
    """Read the actual saved Scheme values, independently of worker metadata."""
    import h5py,re
    with h5py.File(case_path) as case:
        text=case['settings/Rampant Variables'][0].decode()
    schemes=re.findall(r'\(species/scheme\s+\(([^()]*)\)\)',text)
    tolerances=re.findall(r'\(gpuapp/solver/absolute-tolerance-double\s+([^()\s]+)\)',text)
    if len(schemes)!=1 or len(tolerances)!=1:
        raise ValueError('Missing or ambiguous saved solver controls')
    orders=[int(x) for x in schemes[0].split()]
    absolute=float(tolerances[0])
    if len(orders)!=3 or any(x not in [0,1] for x in orders) or not np.isfinite(absolute) or absolute<=0:
        raise ValueError('Invalid saved solver controls')
    result=dict(species_scheme=orders,linear_absolute_tolerance_double=absolute)
    for key in ['amg-alpha','iter-tolerance','max-iter','relax']:
        values=re.findall(r'\(species/'+key+r'\s+\(([^()]*)\)\)',text)
        if len(values)>1:raise ValueError('Ambiguous saved species linear controls')
        if values:
            array=[float(x) for x in values[0].split()]
            if len(array)!=3 or not np.isfinite(array).all() or min(array)<=0:
                raise ValueError('Invalid saved species linear controls')
            result['species_'+key.replace('-','_')]=array
    return result


def iteration_plan(capture_startup=False):
    if type(capture_startup) is not bool:raise ValueError('Startup capture must be explicit boolean')
    return [(1,'first'),(1,'second'),(1,'third'),(21,'penultimate'),(1,'final')] if capture_startup else [(1,'first'),(23,'penultimate'),(1,'final')]


def _field(group, name, first, last, width=None):
    if name not in group or set(group[name]) != {'1'}:
        raise ValueError('Missing/chunked saved field: '+name)
    field = group[name+'/1']
    ids = [np.asarray(field.attrs.get(n, [])) for n in ['minId','maxId']]
    if any(v.shape!=(1,) for v in ids) or int(ids[0][0]) != first or int(ids[1][0]) != last:
        raise ValueError('Incomplete saved field IDs: '+name)
    values = field[()]
    expected = (last-first+1, width) if width else (last-first+1,)
    if width is None:
        values = values.reshape(-1)
    if values.shape != expected or not np.isfinite(values).all():
        raise ValueError('Invalid saved field shape/values: '+name)
    return values


def read_saved(case_path, data_path):
    import h5py
    with h5py.File(case_path) as case, h5py.File(data_path) as data:
        cells = case['meshes/1/cells/zoneTopology']
        if list(cells['minId'][()]) != [1] or len(cells['maxId']) != 1:
            raise ValueError('Expected one contiguous fluid cell zone')
        count = int(cells['maxId'][0])
        group = data['results/1/phase-1/cells']
        fields = {n:_field(group,n,1,count) for n in ['SV_U','SV_V','SV_W','SV_P','SV_T','SV_DENSITY','SV_MU_LAM']}
        y = _field(group,'SV_Y',1,count,width=4)
        if y.min()<0 or y.max()>1 or np.max(abs(y.sum(axis=1)-1))>1e-8:
            raise ValueError('Invalid species; values are not clipped or normalized')
        if any(fields[n].min()<=0 for n in ['SV_T','SV_DENSITY','SV_MU_LAM']):
            raise ValueError('Non-positive temperature, density or viscosity')
        top = case['meshes/1/faces/zoneTopology']
        names = top['name'][()][0].decode().split(';')
        if len(set(names))!=len(names) or set(names)!={'interior','fuel_inlet','oxidizer_inlet','outlet','wall'}:
            raise ValueError('Unexpected/duplicate nozzle face zone')
        starts,ends = top['minId'][()].astype(int),top['maxId'][()].astype(int)
        faces = data['results/1/phase-1/faces']
        flux = _field(faces,'SV_FLUX',1,int(ends.max()))
        yf = faces['SV_Y/1']
        yf_first,yf_last = (int(yf.attrs[n][0]) for n in ['minId','maxId'])
        boundary_y = _field(faces,'SV_Y',yf_first,yf_last,width=4)
        if boundary_y.min()<0 or boundary_y.max()>1 or np.max(abs(boundary_y.sum(axis=1)-1))>1e-8:
            raise ValueError('Invalid saved boundary composition')
        rates = {}
        for name,start,end in zip(names,starts,ends):
            if name not in {'fuel_inlet','oxidizer_inlet','outlet','wall'}:
                continue
            if name in rates or not yf_first<=start<=end<=yf_last:
                raise ValueError('Invalid/duplicate boundary range')
            q = -flux[start-1:end]
            rates[name] = dict(mass_flow_kg_s=float(q.sum()),
                species_convective_flow_kg_s=(q[:,None]*boundary_y[start-yf_first:end-yf_first+1]).sum(axis=0).tolist())
        if set(rates)!={'fuel_inlet','oxidizer_inlet','outlet','wall'}:
            raise ValueError('Missing nozzle boundary')
    incoming = sum(rates[n]['mass_flow_kg_s'] for n in ['fuel_inlet','oxidizer_inlet'])
    if incoming<=0 or rates['fuel_inlet']['mass_flow_kg_s']<=0 or rates['oxidizer_inlet']['mass_flow_kg_s']<=0 or rates['outlet']['mass_flow_kg_s']>=0:
        raise ValueError('Unexpected inlet/outlet flow direction')
    net = sum(x['mass_flow_kg_s'] for x in rates.values())
    species_net = np.sum([x['species_convective_flow_kg_s'] for x in rates.values()],axis=0)
    h2_in = sum(rates[n]['species_convective_flow_kg_s'][0] for n in ['fuel_inlet','oxidizer_inlet'])
    if h2_in<=0:
        raise ValueError('Missing hydrogen inlet flux')
    summary = dict(cells=count,all_required_cell_fields_finite=True,
        species_sum_max_error=float(abs(y.sum(axis=1)-1).max()),h2_range=[float(y[:,0].min()),float(y[:,0].max())],
        temperature_range_K=[float(fields['SV_T'].min()),float(fields['SV_T'].max())],
        boundary_rates=rates,relative_mass_imbalance=abs(net)/incoming,
        h2_relative_convective_imbalance=float(abs(species_net[0])/h2_in),
        convective_species_only=True,scientific_eligible=False,numerical_criteria_met=False,
        formal_physics_validated=False,grid_independence_verified=False)
    return dict(y=y,mu=fields['SV_MU_LAM'],fields=fields,summary=summary)


def compare_compositions(current, previous, material):
    if current['y'].shape!=previous['y'].shape:
        raise ValueError('Cannot compare different cell layouts')
    now,before = evaluate(current['y'],material),evaluate(previous['y'],material)
    mu = current['mu']
    comparisons = {name:differences(values,mu) for name,values in {
        'ideal_current':now['ideal_viscosity'],'ideal_previous':before['ideal_viscosity'],
        'mass_current':now['mass_viscosity'],'mass_previous':before['mass_viscosity']}.items()}
    comparisons['current_composition_matches_ideal'] = comparisons['ideal_current']['absolute_relative_max']<1e-10
    comparisons['previous_composition_matches_ideal'] = comparisons['ideal_previous']['absolute_relative_max']<1e-10
    comparisons['maximum_h2_step_change'] = float(abs(current['y'][:,0]-previous['y'][:,0]).max())
    comparisons['conductivity_independently_verified'] = False
    return comparisons


def diagnose_species(case_path, data_path):
    """Describe rejected species without repairing them or accepting the run."""
    import h5py
    with h5py.File(case_path) as case, h5py.File(data_path) as data:
        count=int(case['meshes/1/cells/zoneTopology/maxId'][0])
        cells=data['results/1/phase-1/cells']
        y=_field(cells,'SV_Y',1,count,width=4)
        mu=_field(cells,'SV_MU_LAM',1,count)
        error=y.sum(axis=1)-1;bad=abs(error)>1e-8
        worst=np.argsort(abs(error))[-3:][::-1]
        report=dict(cells=count,species_dtype=str(y.dtype),species_range=[float(y.min()),float(y.max())],
            cell_sum_error_range=[float(error.min()),float(error.max())],
            cell_sum_max_error=float(abs(error).max()),cell_sum_limit=1e-8,
            cells_above_sum_limit=int(bad.sum()),bad_cells_with_zero_n2=int((bad&(y[:,-1]==0)).sum()),
            worst_cells=[dict(cell_id=int(i+1),Y=y[i].tolist(),sum_error=float(error[i])) for i in worst],
            viscosity_range=[float(mu.min()),float(mu.max())],boundary_species={},
            accepted=False,formal_physics_validated=False,scientific_eligible=False)
        faces=data['results/1/phase-1/faces'];d=faces['SV_Y/1']
        first,last=(int(d.attrs[n][0]) for n in ['minId','maxId'])
        yf=_field(faces,'SV_Y',first,last,width=4)
        top=case['meshes/1/faces/zoneTopology'];flux=faces['SV_FLUX/1'][()].reshape(-1)
        for name,a,b in zip(top['name'][()][0].decode().split(';'),top['minId'][()],top['maxId'][()]):
            if name=='interior':continue
            e=yf[a-first:b-first+1].sum(axis=1)-1;q=flux[a-1:b]
            report['boundary_species'][name]=dict(max_sum_error=float(abs(e).max()),above_limit=int((abs(e)>1e-8).sum()),
                nonzero_flux_faces_above_limit=int(((abs(e)>1e-8)&(abs(q)>1e-30)).sum()))
    return report
