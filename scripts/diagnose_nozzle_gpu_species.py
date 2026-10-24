"""Localize preserved GPU species failures without starting Fluent."""
import argparse,csv,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from nozzle_platform.workflows import save,sha256
from nozzle_platform.gpu_stability import saved_geometry,write_geometry,alignment_bins,FLAGS
from nozzle_platform.nozzle_gpu_probe import _field
from coldflow_trial.solver import mesh_fingerprint


def main():
    import h5py
    from scipy.spatial import cKDTree
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference',type=Path,required=True);p.add_argument('--candidate',type=Path,action='append',required=True)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve()
    if out.exists():raise ValueError('Preserve old evidence; choose a new output directory')
    manifest=json.loads(a.manifest.read_text(encoding='utf-8'))
    records={str(Path(r['directory']).resolve()):r for r in manifest['native_runs']}
    folders=[a.reference.resolve()]+[x.resolve() for x in a.candidate]
    for folder in folders:
        r=records[str(folder)]
        assert r['cleanup_verified']
        for name in ['final.cas.h5','spec.json','first.dat.h5','second.dat.h5','third.dat.h5','penultimate.dat.h5','final.dat.h5']:
            assert sha256(folder/name)==r['files_sha256'][name],name
    case=a.reference.resolve()/'final.cas.h5';fingerprint=mesh_fingerprint(case)
    for folder in folders:assert mesh_fingerprint(folder/'final.cas.h5')==fingerprint
    geometry=saved_geometry(case);centres=geometry['centres'];alignment=geometry['alignment']
    out.mkdir(parents=True);write_geometry(out/'geometry.npz',geometry)
    trees={n:cKDTree(geometry['boundary'][n]['centres']) for n in ['fuel_inlet','wall','outlet']}
    stages={};plots=[]
    for folder in folders:
        trial={};worst=None
        for name in ['first','second','third','penultimate','final']:
            with h5py.File(folder/(name+'.dat.h5')) as f:y=_field(f['results/1/phase-1/cells'],'SV_Y',1,len(centres),width=4)
            error=y.sum(axis=1)-1;bad=abs(error)>1e-8;ids=np.flatnonzero(bad)
            distances={n:t.query(centres[bad])[0] for n,t in trees.items()}
            hist=alignment_bins(alignment,bad)
            row=dict(cells=len(y),rejected_cells=len(ids),maximum_sum_error=float(abs(error).max()),
                bad_with_zero_n2=int(np.count_nonzero(y[bad,3]==0)),
                bad_with_h2_above_point999=int(np.count_nonzero(y[bad,0]>.999)),
                geometry_alignment_bins=hist,
                bad_bounds_m=[centres[bad].min(axis=0).tolist(),centres[bad].max(axis=0).tolist()] if len(ids) else None,
                boundary_centroid_distance_quantiles_m={n:np.quantile(d,[0,.5,.95,1]).tolist() if len(d) else [] for n,d in distances.items()},
                saved_data_sha256=sha256(folder/(name+'.dat.h5')),accepted=False)
            trial[name]=row
            with (out/(folder.name+'_'+name+'_bad_cells.csv')).open('w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.writer(stream);writer.writerow(['cell_id','x_m','y_m','z_m','H2','O2','H2O','N2','sum_error','geometric_alignment_proxy','nearest_fuel_face_centroid_m','nearest_wall_face_centroid_m','nearest_outlet_face_centroid_m'])
                for j,i in enumerate(ids):writer.writerow([int(i+1),*centres[i],*y[i],error[i],alignment[i],*[distances[n][j] for n in ['fuel_inlet','wall','outlet']]])
            if folder!=a.reference.resolve() and (worst is None or len(ids)>worst[0]):
                worst=(len(ids),folder.name+'/'+name,centres[bad].copy(),error[bad].copy(),hist)
        stages[folder.name]=trial
        if worst is not None:plots.append(worst[1:])
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(len(plots),2,figsize=(10,4*len(plots)),squeeze=False,layout='constrained')
    for i,(name,xyz,error,hist) in enumerate(plots):
        s=axes[i,0].scatter(np.linalg.norm(xyz[:,:2],axis=1)*1000,xyz[:,2]*1000,c=error*1e8,s=2,cmap='viridis',rasterized=True)
        axes[i,0].set(xlabel='Radius [mm]',ylabel='z [mm]',title=name+': all rejected cells')
        fig.colorbar(s,ax=axes[i,0],label='Species sum error / 1e-8')
        axes[i,1].bar(np.arange(len(hist)),[r['rejected_fraction'] or 0 for r in hist])
        axes[i,1].set(xticks=np.arange(len(hist)),xticklabels=[str(r['lower'])+'-'+str(r['upper']) for r in hist],xlabel='Geometric normal-alignment proxy bin',ylabel='Rejected fraction of cells',title='Correlation only; not Fluent orthogonal quality')
    fig.savefig(out/'species_failure_locations.png',dpi=150);plt.close(fig)
    report=dict(status='preserved_species_failure_locations_analyzed',stages=stages,mesh_fingerprint=fingerprint,
        geometry_source_case=str(case),geometry_source_case_sha256=sha256(case),manifest_sha256=sha256(a.manifest),
        volume_m3=geometry['volume_m3'],geometry_sha256=sha256(out/'geometry.npz'),
        geometric_proxy_is_fluent_orthogonal_quality=False,boundary_distances_are_to_face_centroids=True,
        scope='All saved bad cells and a geometric proxy; correlation cannot establish the cause; no new Fluent iterations',
        source_sha256={n:sha256(ROOT/n) for n in ['scripts/diagnose_nozzle_gpu_species.py','nozzle_platform/gpu_stability.py','nozzle_platform/field_profiles.py']},**FLAGS)
    report['artifacts_sha256']={p.name:sha256(p) for p in out.iterdir() if p.is_file()};save(out/'audit.json',report)
    print(report['status'],out)


if __name__=='__main__':main()
