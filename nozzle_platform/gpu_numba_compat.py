"""Opt-in process-local ABI workaround for the inspected vendor Numba 0.57.0.

Upstream fixes the same symbol-selection bug for cuCtxGetDevice and
cuCtxSynchronize. No driver/Ansys files are changed; this is not a physics switch.
https://github.com/NVIDIA/numba-cuda/commit/6e08c9d08e9de59c7af28b720289debbbd384764
"""


def source():
    """CPython companion module; must not go through Cromwell AST rewriting."""
    return '''# Explicit experimental compatibility fix; applies in this process only.
import ctypes as _abi_ctypes
import numba as _abi_numba
from numba.cuda.cudadrv import driver as _abi_driver, drvapi as _abi_api
if _abi_numba.__version__ != '0.57.0' or _abi_driver.USE_NV_BINDING:
    raise RuntimeError('Experimental CUDA ABI fix requires inspected Numba 0.57.0 ctypes bindings')
_abi_driver.driver.ensure_initialized()
if _abi_driver.driver.get_version() >= (13, 0):
    if _abi_api.API_PROTOTYPES['cuCtxGetDevice'] != (_abi_ctypes.c_int, _abi_ctypes.POINTER(_abi_ctypes.c_int)):
        raise RuntimeError('Unexpected cuCtxGetDevice ABI; refusing workaround')
    if _abi_api.API_PROTOTYPES['cuCtxSynchronize'] != (_abi_ctypes.c_int,):
        raise RuntimeError('Unexpected cuCtxSynchronize ABI; refusing workaround')
    for _abi_name in ('cuCtxGetDevice', 'cuCtxSynchronize'):
        _abi_fn = getattr(_abi_driver.driver.lib, _abi_name)
        _abi_proto = _abi_api.API_PROTOTYPES[_abi_name]
        _abi_fn.restype = _abi_proto[0]
        _abi_fn.argtypes = _abi_proto[1:]
        setattr(_abi_driver.driver, _abi_name, _abi_driver.driver._ctypes_wrap_fn(_abi_name, _abi_fn))
    print('GPU_NUMBA_ABI_FIX: legacy cuCtxGetDevice and cuCtxSynchronize explicitly bound')
else:
    print('GPU_NUMBA_ABI_FIX: CUDA driver older than 13; no change required')

'''


def udf_prefix():
    """Import the companion before generated CUDA decorators; simple AST only."""
    return '''# Experimental process-local ABI fix in the accompanying module.
import sys as _abi_sys
_abi_paths = _abi_sys.path
_abi_paths.insert(0, '.')
import gpu_numba_runtime_fix

'''
