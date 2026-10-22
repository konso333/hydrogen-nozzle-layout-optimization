"""Offline comparison of constant-component Fluent mixture transport laws.

Fluent 25.2 User's Guide, equations 9-30/31 and 9-49/50 use the
same viscosity-based interaction coefficient for BOTH mu and k.
This module does not choose a CFD model or certify a converged solution.
"""

import numpy as np


def components(state, mixture='hydrogen-air'):
    """Read explicit solver species order; never rely on dictionary order."""
    mix = state['materials']['mixture'][mixture]
    species = mix['species']
    names = species['volumetric']
    if not names or len(set(names)) != len(names) or set(names) != set(species['volumetric_species']):
        raise ValueError('Missing, duplicated or inconsistent solver species order.')
    result = {'names': list(names)}
    for key in ('molecular_weight', 'viscosity', 'thermal_conductivity'):
        values = [species['volumetric_species'][name][key] for name in names]
        if any(value.get('option') != 'value' for value in values):
            raise ValueError('Offline audit requires constant component properties: '+key)
        data = np.asarray([value['value'] for value in values], dtype=float)
        if not np.isfinite(data).all() or np.any(data <= 0):
            raise ValueError('Component properties must be finite and positive: '+key)
        result[key] = data
    return result


def evaluate(mass_fractions, material):
    """Evaluate both laws on the SAME composition, without clipping/normalizing.

    X_i = (Y_i / M_i) / sum(Y_j / M_j). The common normalization cancels
    in X_i / sum_j(X_j phi_ij). Conductivity phi uses mu, not k.
    """
    y = np.asarray(mass_fractions, dtype=float)
    n = len(material['names'])
    if y.ndim != 2 or y.shape[1] != n or not len(y):
        raise ValueError('Expected a nonempty cells-by-species array.')
    if not np.isfinite(y).all() or y.min() < 0 or y.max() > 1:
        raise ValueError('Invalid mass fractions; input is not repaired.')
    if np.max(np.abs(y.sum(axis=1)-1)) > 1e-8:
        raise ValueError('Mass fractions do not sum to one; input is not normalized.')
    mw, mu, k = (np.asarray(material[key], dtype=float) for key in
                 ('molecular_weight', 'viscosity', 'thermal_conductivity'))
    if any(v.shape != (n,) or not np.isfinite(v).all() or np.any(v <= 0) for v in (mw, mu, k)):
        raise ValueError('Invalid component properties.')
    phi = (1+np.sqrt(mu[:, None]/mu[None, :])*(mw[None, :]/mw[:, None])**.25)**2
    phi /= np.sqrt(8*(1+mw[:, None]/mw[None, :]))
    mol = y/mw
    weights = mol/(mol @ phi.T)
    return {'ideal_viscosity': weights @ mu, 'mass_viscosity': y @ mu,
            'ideal_conductivity': weights @ k, 'mass_conductivity': y @ k}


def render_gpu_udf(material, mixture='hydrogen-air', *, legacy_cuda_compat=False):
    """Experimental 25.2 Python UDF source; NOT enabled in platform workflows.

    Constants and explicit species indices preserve the saved constant-component
    ideal mixture laws. Runtime compilation/hook verification is still required.
    Only our generated arithmetic is emitted; no user-supplied native commands.
    """
    n = len(material['names'])
    evaluate(np.eye(n), material)  # validates all constants before emitting code
    mw, mu = (np.asarray(material[key], dtype=float) for key in ('molecular_weight', 'viscosity'))
    phi = (1+np.sqrt(mu[:, None]/mu[None, :])*(mw[None, :]/mw[:, None])**.25)**2
    phi /= np.sqrt(8*(1+mw[:, None]/mw[None, :]))
    args = ', '.join('c_species_'+str(i) for i in range(n))
    lines = ['# Experimental constant-component ideal mixing; generated offline.',
             '# Species order: '+repr(material['names']), '']
    for prop, function, hook in [('viscosity', 'udf_ideal_mu', 'add_laminar_viscosity'),
                                ('thermal_conductivity', 'udf_ideal_k', 'add_conductivity')]:
        lines.append('def '+function+'('+args+'):')
        for i in range(n):
            lines.append(f'    q{i} = c_species_{i} / {float(mw[i])!r}')
        for i in range(n):
            lines.append(f'    d{i} = '+' + '.join(f'q{j} * {float(phi[i,j])!r}' for j in range(n)))
        lines.append('    return '+' + '.join(f'q{i} * {float(material[prop][i])!r} / d{i}' for i in range(n)))
        lines += ['', f'UDF.{hook}({function}, {mixture!r})', '']
    prefix = ''
    if legacy_cuda_compat:
        from nozzle_platform.gpu_numba_compat import udf_prefix
        prefix = udf_prefix()
    return prefix+'\n'.join(lines)


def differences(reference, candidate):
    """Unweighted sample statistics, not volume averages or CFD error bounds."""
    ref, other = np.asarray(reference), np.asarray(candidate)
    if ref.shape != other.shape or not ref.size or not np.isfinite(ref).all() or not np.isfinite(other).all() or np.any(ref <= 0):
        raise ValueError('Invalid property comparison.')
    relative = (other-ref)/ref
    return {'sample_count': int(ref.size), 'weighting': 'equal samples; not volume weighted',
            'signed_relative_min': float(relative.min()), 'signed_relative_max': float(relative.max()),
            'absolute_relative_max': float(np.abs(relative).max()),
            'absolute_relative_p50': float(np.quantile(np.abs(relative), .5)),
            'absolute_relative_p95': float(np.quantile(np.abs(relative), .95)),
            'absolute_relative_p99': float(np.quantile(np.abs(relative), .99))}
