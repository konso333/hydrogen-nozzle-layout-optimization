"""Native report parsing; unavailable species totals are never replaced by zero."""
import math,re
from pathlib import Path
from nozzle_platform.workflows import sha256

ROLES=['fuel_inlet','oxidizer_inlet','outlet']

def pressure_commands(stage):
    if str(stage) not in ['input','25','50','75','100']:raise ValueError('Unknown report checkpoint')
    return ''.join('/report/surface-integrals/area-weighted-avg '+role+' () total-pressure yes total_'+str(stage)+'_'+role+'.txt\n' for role in ROLES)


def parse_pressure(text,role):
    if role not in ROLES or 'Total Pressure' not in text or 'Area-Weighted Average' not in text or '[Pa]' not in text:
        raise ValueError('Wrong native total-pressure report')
    matches=re.findall(r'^\s*'+re.escape(role)+r'\s+([^\s]+)\s*$',text,re.M)
    if len(matches)!=1:raise ValueError('Missing/ambiguous native pressure value')
    value=float(matches[0])
    if not math.isfinite(value):raise ValueError('Nonfinite pressure report')
    return value


def total_pressure(folder,stage):
    folder=Path(folder);values={};hashes={}
    for role in ROLES:
        path=folder/('total_'+str(stage)+'_'+role+'.txt')
        values[role]=parse_pressure(path.read_text(encoding='utf-8',errors='replace'),role);hashes[path.name]=sha256(path)
    return dict(area_weighted_total_pressure_Pa=values,
        area_weighted_total_pressure_drop_Pa={role:values[role]-values['outlet'] for role in ROLES[:2]},
        files_sha256=hashes,source='native Fluent surface integral; printed report precision retained')
