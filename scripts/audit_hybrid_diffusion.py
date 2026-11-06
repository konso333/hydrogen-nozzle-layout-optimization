"""Read-only native evidence audit for M27. Never launches Fluent."""
import argparse,ast,importlib.util,json,math,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import psutil,numpy as np
from nozzle_platform.workflows import save,sha256
from nozzle_platform.hybrid_diffusion_benchmark import state,physical_signature,reconstruct_boundary_fluxes,boundary_density_diagnostic,assess_report,field_difference,Q,D,FLUX_REL_TOL,render_udf,TOL
from nozzle_platform.species_flux_benchmark import parse_report,iteration_count,saved_diffusion_flags
from coldflow_trial.solver import parse_residual_history,mesh_fingerprint

def parse_probe(text,label):
    blocks=re.findall(r'^M27_BEGIN_'+re.escape(label)+r'\r?\n(.*?)^M27_END_'+re.escape(label)+r'\r?$',text,re.M|re.S)
    if len(blocks)!=1:raise ValueError('Missing/duplicate actual probe')
    block=blocks[0];zones={};components={}
    if 'M27_STORAGE_UNAVAILABLE' in block or 'M27_MISSING_ZONE' in block:raise ValueError('Probe storage missing')
    for line in re.findall(r'^M27_FLUX (.+)$',block,re.M):
        d={k:float(v) for k,v in (x.split('=') for x in line.split())};z=int(d['zone'])
        if z in zones or not all(math.isfinite(v) for v in d.values()):raise ValueError('Duplicate/nonfinite probe')
        if d['faces']!=16 or abs(d['total'])>1e-15 or abs(d['Dmin']-D)>1e-15 or abs(d['Dmax']-D)>1e-15:raise ValueError('Unexpected diagnostic flux/D')
        zones[z]=d
    for line in re.findall(r'^M27_COMPONENT (.+)$',block,re.M):
        d={k:float(v) for k,v in (x.split('=') for x in line.split())};key=(int(d['zone']),int(d['i']))
        if key in components or not all(math.isfinite(v) for v in d.values()):raise ValueError('Duplicate/nonfinite component')
        if abs(d['convection'])>1e-15:raise ValueError('Unexpected bulk convection')
        components[key]=d
    if set(zones)!={4,5} or set(components)!={(z,i) for z in (4,5) for i in range(3)}:raise ValueError('Probe coverage')
    return dict(zones={str(k):v for k,v in zones.items()},components={f'{z}/{i}':v for (z,i),v in components.items()})

def function_ast(path,name):
    tree=ast.parse(path.read_text(encoding='utf-8'));return ast.dump(next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name==name),include_attributes=False)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,default=ROOT/'outputs/platform_m27_hybrid_diffusion_20261105');parser.add_argument('--output',type=Path);args=parser.parse_args()
    source=args.source.resolve();output=args.output.resolve() if args.output else source/'final_audit'
    if output.exists():raise ValueError('Use a new audit directory')
    ledger=json.loads((ROOT/'.local/m27_native_budget.json').read_text(encoding='utf-8'))
    dirs={stage:Path(next(r['folder'] for r in reversed(ledger['attempts']) if r['stage']==stage)) for stage in ('prepare','gpu','cpu')}
    backends={};reference=json.loads((dirs['prepare']/'loaded_configured.json').read_text(encoding='utf-8'))
    expected_signature=physical_signature(reference,dict(thermal_diffusion=False,multi_component_diffusion=False))
    fingerprints=[]
    for stage,p in dirs.items():
        spec=json.loads((p/'spec.json').read_text(encoding='utf-8'));result=json.loads((p/'result.json').read_text(encoding='utf-8'))
        assert result['status']=='completed' and result['cleanup']['all_tracked_processes_exited']
        for name,h in spec['implementation_sha256'].items():assert sha256(p/'source_snapshot'/name)==h
        assert (p/'probe.c').read_text(encoding='ascii')==render_udf()
        first=0 if stage=='prepare' else iteration_count(p/'input.dat.h5');last=iteration_count(p/'final.dat.h5')
        if stage=='prepare':assert first==last==0
        else:
            prior=dirs['prepare' if stage=='gpu' else 'gpu']
            assert all(sha256(prior/n)==h for n,h in spec['source_sha256'].items())
            assert sha256(p/'input.cas.h5')==spec['source_sha256']['final.cas.h5'] and sha256(p/'input.dat.h5')==spec['source_sha256']['final.dat.h5']
            assert 1<=last-first<=spec['requested_iterations']
        saved=state(p/'final.cas.h5',p/'final.dat.h5');assert saved['passed']
        actual=json.loads((p/'loaded_configured.json').read_text(encoding='utf-8'))
        assert physical_signature(actual,saved_diffusion_flags(p/'final.cas.h5'))==expected_signature
        assert actual['materials']['mixture']['hydrogen-air']['mass_diffusivity']['option']=='constant-dilute-appx'
        assert abs(actual['materials']['mixture']['hydrogen-air']['mass_diffusivity']['constant_mass_diffusivity']-D)<1e-15
        fingerprint=mesh_fingerprint(p/'final.cas.h5');fingerprints.append(fingerprint)
        entry=dict(initial_iteration=first,final_iteration=last,actual_added_iterations=last-first,compute_wall_seconds=result.get('compute_wall_seconds'),saved_state=saved,
            boundary_density_diagnostic=boundary_density_diagnostic(p/'final.cas.h5',p/'final.dat.h5'))
        text=(p/'fluent.trn').read_text(encoding='utf-8',errors='replace')
        if stage!='prepare':
            assert all('maximum_relative_formula_error' in row for row in entry['boundary_density_diagnostic'])
            parse_residual_history(text,first,last)
            if last-first<spec['requested_iterations']:assert f'!    {last} solution is converged' in text
            entry['probes']={label:parse_probe(text,label) for label in ('read_probe','fresh_probe')}
            entry['reports']={}
            for label in ('read_report','fresh_report'):
                raw=parse_report(text.replace('M27_BEGIN_','M24_BEGIN_').replace('M27_END_','M24_END_'),label)
                assessment=assess_report(raw);assert assessment['passed']==(stage=='cpu' and label=='fresh_report')
                entry['reports'][label]=dict(raw=raw,assessment=assessment)
            reconstruction=reconstruct_boundary_fluxes(p/'final.cas.h5',p/'final.dat.h5');assert assess_report(reconstruction)['passed']
            entry['saved_file_flux_reconstruction']=reconstruction
            live=entry['probes']['fresh_probe']
            for zone,name in [(4,'inlet'),(5,'outlet')]:
                for i in range(3):
                    row=live['components'][f'{zone}/{i}'];target=reconstruction['boundary_rates_kg_s'][name][i]
                    assert abs(row['reconstructed']-target)<Q*1e-10
                    if stage=='gpu':assert row['cache']==0
                    else:assert abs(row['cache']-target)<=Q*FLUX_REL_TOL
            if stage=='cpu':
                assert iteration_count(p/'read.dat.h5')==first
                read=field_difference(p/'input.dat.h5',p/'read.dat.h5');assert read['all_within_declared_tolerances']
                step=field_difference(p/'read.dat.h5',p/'final.dat.h5')
                entry['read_field_difference']=read;entry['step_field_difference']=step
                entry['maximum_step_field_changes']={n:max(r['maximum_absolute_change'] for r in step['checks'] if r['field']==n) for n in TOL}
                entry['maximum_step_changes_by_location']={loc:{n:max((r['maximum_absolute_change'] for r in step['checks'] if r['field']==n and r['location']==loc),default=0.) for n in TOL} for loc in ('cells','faces')}
                entry['fresh_report_maximum_analytic_relative_error']=max(abs(r['value']-r['reference'])/Q for r in entry['reports']['fresh_report']['assessment']['checks'])
                entry['fresh_report_maximum_final_reconstruction_relative_difference']=max(abs(v-reconstruction['boundary_rates_kg_s'][name][i])/Q for name,vs in entry['reports']['fresh_report']['raw']['boundary_rates_kg_s'].items() for i,v in enumerate(vs))
                entry['relative_species_imbalance']=max(abs(v) for v in entry['reports']['fresh_report']['raw']['net_kg_s'])/Q
                old=p/'source_snapshot/.local/m26_handoff.py'
                assert function_ast(old,'field_difference')==function_ast(ROOT/'nozzle_platform/hybrid_diffusion_benchmark.py','field_difference')
                oldmod=importlib.util.spec_from_file_location('m26_evidence_read_helper',old);helper=importlib.util.module_from_spec(oldmod);oldmod.loader.exec_module(helper)
                assert helper.TOL==TOL and helper.field_difference(p/'input.dat.h5',p/'read.dat.h5')==read
        evidence=['spec.json','result.json','fluent.trn','probe.c','final.cas.h5','final.dat.h5','loaded_configured.json','cleanup.json','controller_outcome.json']
        if stage!='prepare':evidence+=['input.cas.h5','input.dat.h5']
        if stage=='cpu':evidence+=['read.cas.h5','read.dat.h5']
        entry['files_sha256']={n:sha256(p/n) for n in evidence};entry['folder']=str(p);backends[stage]=entry
    assert all(f==fingerprints[0] for f in fingerprints)
    assert backends['gpu']['actual_added_iterations']==2 and backends['cpu']['actual_added_iterations']==1
    assert backends['cpu']['initial_iteration']==backends['gpu']['final_iteration']
    # Current seed/model/analytic checks unchanged since execution; only additions.
    frozen=dirs['gpu']/'source_snapshot/nozzle_platform/hybrid_diffusion_benchmark.py'
    for name in ('profile','reference_rates','mesh','geometry','state','assess_report','render_udf','physical_signature'):
        assert function_ast(frozen,name)==function_ast(ROOT/'nozzle_platform/hybrid_diffusion_benchmark.py',name)
    assert function_ast(dirs['cpu']/'source_snapshot/scripts/benchmark_hybrid_diffusion.py','configure')==function_ast(ROOT/'scripts/benchmark_hybrid_diffusion.py','configure')
    native=[p.info for p in psutil.process_iter(['pid','name']) if (p.info['name'] or '').lower() in ('fluent.exe','fl2520.exe','cx2520.exe','cortex.exe') or (p.info['name'] or '').lower().startswith('fl_mpi')];assert not native
    old=json.loads((ROOT/'outputs/platform_m24_review/milestone_review_20261104/review_manifest.json').read_text(encoding='utf-8'));assert all(sha256(ROOT/n)==h for n,h in old['working_file_sha256'].items())
    for n in ('m25_root_cause','m26_handoff'):
        chk=json.loads((ROOT/('outputs/platform_'+n+'_20261105/checkpoint.json')).read_text(encoding='utf-8'));assert all(sha256(ROOT/f)==h for f,h in chk['files_sha256'].items())
    audit=dict(status='nonuniform_counter_diffusion_handoff_audited',backends=backends,analytic_diffusion_rate_kg_s=Q,declared_flux_relative_truncation_tolerance=FLUX_REL_TOL,
        actual_new_gpu_iterations=2,actual_new_cpu_iterations=1,all_native_processes_exited=True,previous_frozen_files_and_evidence_unchanged=True,
        classification='GPU nonuniform saved fields pass while zero-cache flux report fails; one CPU continuation produces nonzero complete boundary diffusion flux agreeing with analytic reference and independent saved-file reconstruction within declared coarse-grid allowance',
        post_native_changes='Added independent HDF flux reconstruction, face-density diagnostic and audit; extracted unchanged read comparator from M26 into M27 module (AST, tolerances and actual output equality checked) and replaced its import in runner. No native rerun with final import path.',
        full_field_equivalence_validated=False,additional_attention='GPU inlet/outlet face density differs from the density formula evaluated at its saved boundary composition by about 0.76 percent; those face densities equal adjacent-cell density. CPU continuation updates them to match boundary-composition density within the existing 2e-6 relative formula allowance. Underlying vendor evaluation convention unresolved; not a confirmed bulk-cell solution error.',
        formal_physics_validated=False,real_nozzle_handoff_validated=False,nonzero_bulk_convection_handoff_validated=False,grid_independence_verified=False,vendor_source_level_root_cause_confirmed=False)
    # NumPy comparison booleans become ordinary JSON booleans; values unchanged.
    def json_scalar(value):
        if isinstance(value,np.generic):return value.item()
        raise TypeError('Unexpected audit value type: '+type(value).__name__)
    audit=json.loads(json.dumps(audit,default=json_scalar))
    output.mkdir(parents=True);save(output/'audit.json',audit)
    for row in ledger['attempts']:
        row['independently_verified_actual_iterations']=0 if row['folder']==str(source/'gpu') else backends[row['stage']]['actual_added_iterations']
    ledger.update(closed=True,unused_allowances_locked=True,actual_new_gpu_iterations=2,actual_new_cpu_iterations=1,reason='M27 native work ended; independent audit complete. Do not reopen any unused requested steps.')
    save(ROOT/'.local/m27_native_budget.json',ledger)
    print(json.dumps(dict(status=audit['status'],Q=Q,CPU=backends['cpu']['maximum_step_field_changes'],analytic_relative_error=backends['cpu']['fresh_report_maximum_analytic_relative_error'],reconstruction_relative_difference=backends['cpu']['fresh_report_maximum_final_reconstruction_relative_difference'],relative_species_imbalance=backends['cpu']['relative_species_imbalance'],all_native_processes_exited=True),ensure_ascii=False))

if __name__=='__main__':main()
