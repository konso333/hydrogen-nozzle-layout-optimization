"""Cross-check native species reports; consistency alone does not prove diffusion."""
import math
import re

SPECIES = ('h2', 'o2', 'h2o', 'n2')
BOUNDARIES = ('fuel_inlet', 'oxidizer_inlet', 'outlet', 'wall')


def parse_species_report(text):
    headers = re.findall(r'^\s*species:\s*\(([^()]*)\)\s+kg/s\s*$', text, re.M)
    if len(headers) != 1 or tuple(headers[0].split()) != SPECIES:
        raise ValueError('Missing, repeated or reordered species header / kg/s')
    result = {}; zone_ids = set()
    for zone, role, raw in re.findall(r'^\s*zone\s+(\d+)\s+\(([^()]*)\):\s*\(([^()]*)\)\s*$', text, re.M):
        if role not in BOUNDARIES or role in result or zone in zone_ids:
            raise ValueError('Unexpected or duplicate boundary')
        zone_ids.add(zone)
        values = [float(x) for x in raw.split()]
        if len(values) != len(SPECIES) or not all(math.isfinite(x) for x in values):
            raise ValueError('Invalid species flow values')
        result[role] = values
    if set(result) != set(BOUNDARIES):
        raise ValueError('Incomplete boundary report')
    nets = re.findall(r'^\s*net species-mass-flow:\s*\(([^()]*)\)\s*$', text, re.M)
    if len(nets) != 1:
        raise ValueError('Missing or repeated net species report')
    net = [float(x) for x in nets[0].split()]
    if len(net) != 4 or not all(math.isfinite(x) for x in net):
        raise ValueError('Invalid net species values')
    for i in range(4):
        scale = max(sum(abs(row[i]) for row in result.values()), 1e-30)
        if abs(sum(row[i] for row in result.values()) - net[i]) > max(scale * 2e-6, 1e-15):
            raise ValueError('Net does not match printed boundary flows')
    return dict(species=list(SPECIES), boundary_rates_kg_s=result, net_kg_s=net)


def assess_species_report(parsed, saved_summary, *, inlet_diffusion_enabled):
    """Pure-H2 inlet with disabled inlet diffusion is an independent sentinel.

    Other species/locations may include diffusion, so they are NOT required to
    equal convection. No total is certified without separate diffusive evidence.
    """
    if inlet_diffusion_enabled is not False:
        raise ValueError('Pure-inlet sentinel requires independently verified disabled inlet diffusion')
    rates = saved_summary['boundary_rates']
    if set(rates) != set(BOUNDARIES):
        raise ValueError('Incomplete independently saved boundaries')
    for row in rates.values():
        values = [row['mass_flow_kg_s']] + row['species_convective_flow_kg_s']
        if len(values) != 5 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in values):
            raise ValueError('Invalid independently saved boundary rates')
    native = parsed['boundary_rates_kg_s']
    checks = []
    for role in BOUNDARIES:
        mass = rates[role]['mass_flow_kg_s']
        value = sum(native[role])
        limit = max(abs(mass) * 2e-6, 1e-15)
        checks.append(dict(name='species_sum:' + role, value=value, reference=mass,
                           absolute_tolerance=limit, passed=abs(value - mass) <= limit))
    fuel = rates['fuel_inlet']
    q = fuel['mass_flow_kg_s']
    expected = fuel['species_convective_flow_kg_s']
    if q <= 0 or abs(expected[0] - q) > q * 1e-10 or any(abs(x) > q * 1e-10 for x in expected[1:]):
        raise ValueError('This sentinel requires the independently saved pure-H2 fuel inlet')
    for i, name in enumerate(SPECIES):
        limit = max(q * 2e-6, 1e-15)
        checks.append(dict(name='pure_fuel:' + name, value=native['fuel_inlet'][i], reference=expected[i],
                           absolute_tolerance=limit, passed=abs(native['fuel_inlet'][i] - expected[i]) <= limit))
    passed = all(c['passed'] for c in checks)
    return dict(report_consistent_with_saved_boundary=passed, checks=checks,
                rejected_checks=[c['name'] for c in checks if not c['passed']],
                full_species_flux_verified=False, full_species_flux_kg_s=None,
                reason=('Consistency checks passed; independent diffusive flux evidence is still required.' if passed
                        else 'Native species report contradicts saved boundary data; rejected as conservation evidence.'))


def report_block(transcript, label):
    if not re.fullmatch(r'[a-z0-9_]+', label):
        raise ValueError('Invalid report label')
    # Echoed Scheme commands contain the marker too; only actual output lines
    # are anchors. Fluent may print a literal backslash-n in older journals.
    match = re.search(r'^BEGIN_SPECIES_' + label + r'(?:\\n)?\s*\n(.*?)^> \(display "END_SPECIES_' + label,
                      transcript, re.M | re.S)
    if not match:
        raise ValueError('Missing bounded species report markers')
    return match[1]


def field_preservation(before,after):
    """A zero-iteration reader must not silently change its solution fields."""
    import numpy as np
    if set(before['fields']) != set(after['fields']):raise ValueError('Reader field set changed')
    checks=[]
    for name in list(before['fields'])+['species']:
        a=np.asarray(before['y'] if name=='species' else before['fields'][name],float)
        b=np.asarray(after['y'] if name=='species' else after['fields'][name],float)
        if a.shape!=b.shape or not a.size or not np.isfinite(a).all() or not np.isfinite(b).all():
            raise ValueError('Incomplete or nonfinite reader field')
        difference=float(np.max(np.abs(a-b)))
        tolerance=max(1e-12 if name=='species' else 1e-30,float(np.max(np.abs(a)))*1e-12)
        checks.append(dict(name=name,maximum_absolute_change=difference,absolute_tolerance=tolerance,passed=difference<=tolerance))
    return dict(verified=all(c['passed'] for c in checks),checks=checks)
