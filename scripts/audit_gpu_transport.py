"""Read saved Fluent fields and compare mixture laws without starting Fluent.

Example:
python scripts/audit_gpu_transport.py --original DIRECTORY --gpu DIRECTORY --output NEW_DIRECTORY
Requires numpy/h5py; optional --plot uses matplotlib. Inputs are read-only.
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import h5py
import numpy as np
from nozzle_platform.transport_audit import components, evaluate, differences


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def load(folder, expected):
    state = json.loads((folder/'configured.json').read_text(encoding='utf-8'))
    mix = state['materials']['mixture']['hydrogen-air']
    if any(mix[key]['option'] != expected for key in ('viscosity', 'thermal_conductivity')):
        raise ValueError('Saved transport model does not match the requested audit role.')
    if state['models']['species']['reactions']['enable_volumetric_reactions']:
        raise ValueError('This audit is scoped to the saved nonreacting case.')
    material = components(state)
    with h5py.File(folder/'final.cas.h5', 'r') as case:
        top = case['meshes/1/cells/zoneTopology']
        cell_count = int(np.sum(top['maxId'][()]-top['minId'][()]+1))
    with h5py.File(folder/'final.dat.h5', 'r') as data:
        cells = data['results/1/phase-1/cells']
        # Reject unknown/chunked layouts instead of auditing only a subset.
        if any(set(cells[key]) != {'1'} for key in ('SV_Y', 'SV_MU_LAM', 'SV_T')):
            raise ValueError('Unsupported field chunk layout; no partial audit allowed.')
        y = cells['SV_Y/1'][()]
        stored = cells['SV_MU_LAM/1'][()].reshape(-1)
        temperature = cells['SV_T/1'][()].reshape(-1)
        for key in ('SV_Y', 'SV_MU_LAM', 'SV_T'):
            field = cells[key+'/1']
            if int(field.attrs['minId'][0]) != 1 or int(field.attrs['maxId'][0]) != cell_count:
                raise ValueError('Saved fields do not cover all solver cell IDs.')
    if len(y) != cell_count or len(stored) != len(y) or len(temperature) != len(y) or not np.isfinite(temperature).all():
        raise ValueError('Saved cell field lengths or temperatures are invalid.')
    values = evaluate(y, material)
    comparison = {key: differences(values['ideal_'+key], values['mass_'+key])
                  for key in ('viscosity', 'conductivity')}
    actual_key = 'ideal_viscosity' if expected == 'ideal-gas-mixing-law' else 'mass_viscosity'
    saved_check = differences(values[actual_key], stored)
    result = {'directory': str(folder), 'transport_model': expected, 'cells': len(y),
              'temperature_K': [float(temperature.min()), float(temperature.max())],
              'mass_fraction_sum_max_error': float(np.abs(y.sum(axis=1)-1).max()),
              'same_composition_model_difference': comparison,
              'saved_viscosity_vs_offline_law': saved_check,
              'files_sha256': {name: sha(folder/name) for name in ('configured.json', 'final.cas.h5', 'final.dat.h5', 'job.json')}}
    return material, result


def run(original, gpu, output, plot=False):
    if output.exists():
        raise ValueError('Output must be a new directory; previous evidence is not overwritten.')
    material, cpu = load(original, 'ideal-gas-mixing-law')
    gpu_material, accelerated = load(gpu, 'mass-weighted-mixing-law')
    if material['names'] != gpu_material['names'] or any(not np.array_equal(material[k], gpu_material[k]) for k in
            ('molecular_weight', 'viscosity', 'thermal_conductivity')):
        raise ValueError('Component properties/order differ; cannot isolate the mixing law.')
    if cpu['saved_viscosity_vs_offline_law']['absolute_relative_max'] > 1e-10:
        raise ValueError('Offline ideal mixing formula does not reproduce the saved CPU viscosity.')
    config = json.loads((gpu/'job.json').read_text(encoding='utf-8'))['config']
    air_o2 = float(config['oxidizer_o2_mass_fraction'])
    if set(material['names']) != {'h2', 'o2', 'h2o', 'n2'} or not 0 < air_o2 < 1:
        raise ValueError('Expected the existing H2/O2/H2O/N2 cold-flow configuration.')
    z = np.linspace(0, 1, 10001)
    fractions = {'h2': z, 'o2': (1-z)*air_o2, 'h2o': np.zeros_like(z), 'n2': (1-z)*(1-air_o2)}
    values = evaluate(np.column_stack([fractions[name] for name in material['names']]), material)
    sweep = {}
    for key in ('viscosity', 'conductivity'):
        sweep[key] = differences(values['ideal_'+key], values['mass_'+key])
        index = int(np.argmax(np.abs(values['mass_'+key]/values['ideal_'+key]-1)))
        sweep[key]['maximum_at_h2_mass_fraction'] = float(z[index])
    output.mkdir(parents=True)
    distinct = any(sweep[key]['absolute_relative_max'] > 1e-10 for key in sweep)
    report = {'purpose': 'offline transport-law applicability audit; no Fluent solve',
              'decision': ('mass_weighted_not_equivalent_to_original' if distinct else 'sampled_laws_agree')+'; formal_GPU_remains_blocked',
              'formal_physics_validated': False, 'numerical_criteria_met': False,
              'scientific_eligible': False, 'grid_independence_verified': False,
              'new_fluent_iterations': 0, 'original_saved_case': cpu, 'gpu_saved_case': accelerated,
              'components': {k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in material.items()},
              'dry_h2_air_sweep': sweep, 'air_o2_mass_fraction': air_o2,
              'sample_count': len(z), 'scope': ['constant species mu/k from saved configurations',
                  'relative differences use original ideal mixing law as denominator',
                  'same-composition property differences are not differences between converged CFD solutions',
                  'saved CPU and GPU states have different iterations and are not a solution-accuracy comparison',
                  'thermal conductivity uses viscosity-based phi; it is not independently validated against a saved k field',
                  'GPU stored mu mismatch is reported as-is; no lag explanation is assumed',
                  'maximum over 10001 sampled dry mixtures is not an analytic global bound',
                  'no input clipping or normalization; no combustion applicability claim'],
              'sources': ['https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/flu_ug/flu_ug_sec_viscosity.html',
                  'https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/flu_ug/flu_ug_sec_thermcond.html'],
              'implementation_sha256': {str(p.relative_to(ROOT)): sha(p) for p in
                  (ROOT/'nozzle_platform/transport_audit.py', Path(__file__).resolve())}}
    with (output/'mixture_sweep.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['h2_mass_fraction', 'ideal_mu_Pa_s', 'mass_mu_Pa_s', 'ideal_k_W_m_K', 'mass_k_W_m_K'])
        writer.writerows(zip(z, values['ideal_viscosity'], values['mass_viscosity'],
                            values['ideal_conductivity'], values['mass_conductivity']))
    if plot:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), layout='constrained')
        for ax, key, label in zip(axes, ('viscosity', 'conductivity'), ('Viscosity [Pa s]', 'Conductivity [W / (m K)]')):
            ax.plot(z, values['ideal_'+key], label='Original ideal-gas mixing')
            ax.plot(z, values['mass_'+key], '--', label='GPU mass-weighted mixing')
            ax.set(xlabel='Hydrogen mass fraction (dry H2 + air)', ylabel=label)
            ax.grid(alpha=.2)
            ax.legend(fontsize=8)
        fig.suptitle('Same composition, different material laws — not CFD solution error')
        fig.savefig(output/'mixture_laws.png', dpi=160)
        plt.close(fig)
    report['artifacts_sha256'] = {p.name: sha(p) for p in output.iterdir() if p.is_file()}
    (output/'audit.json').write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--gpu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--plot', action='store_true')
    args = parser.parse_args()
    result = run(args.original.resolve(), args.gpu.resolve(), args.output.resolve(), args.plot)
    print(json.dumps({'decision': result['decision'], 'sweep': result['dry_h2_air_sweep']}, ensure_ascii=False))
