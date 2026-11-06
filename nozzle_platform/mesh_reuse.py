"""Narrow compatibility: air solver-only updates do not change mesh generation.

Original mesh/job hashes are preserved. Every other recorded dependency must
match; geometry, mesher, controls, and workflow changes still revoke reuse.
"""
import re

AIR_SOLVER_ONLY={'nozzle_platform/air_model.py','nozzle_platform/air_solver.py'}
MESH_REQUIRED={'nozzle_platform/volume_mesh.py','nozzle_platform/layered_mesh.py','nozzle_platform/poly_mesh.py','nozzle_platform/domain_cad.py','nozzle_platform/mesh_quality.py','nozzle_platform/workflows.py','nozzle_platform/workflow_worker.py'}

def compatibility(original,current):
    if not isinstance(original,dict) or not isinstance(current,dict) or set(original)!=set(current) or not MESH_REQUIRED<=set(current):return dict(compatible=False,changed_files=[])
    if any(not isinstance(h,str) or re.fullmatch('[0-9a-f]{64}',h) is None for h in [*original.values(),*current.values()]):return dict(compatible=False,changed_files=[])
    changed=sorted(n for n in current if original[n]!=current[n])
    return dict(compatible=set(changed)<=AIR_SOLVER_ONLY,changed_files=changed,reason='air solver-only changes; mesh-producing dependencies identical' if changed else 'all recorded implementation files identical',source_implementation_sha256=original,current_implementation_sha256=current)
