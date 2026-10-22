"""A legacy driver API prototype must never bind a newer incompatible symbol."""
import ctypes
import sys
from types import ModuleType,SimpleNamespace
import pytest
from nozzle_platform.gpu_numba_compat import source,udf_prefix


@pytest.fixture
def binding(monkeypatch):
    calls=[]
    device=SimpleNamespace();synchronize=SimpleNamespace()
    legacy=object();unrelated=object()
    driver=SimpleNamespace(lib=SimpleNamespace(cuCtxGetDevice=device,cuCtxSynchronize=synchronize),
        cuCtxGetDevice=legacy,cuCtxSynchronize=legacy,cuMemcpyDtoH=unrelated,
        ensure_initialized=lambda:calls.append('init'),get_version=lambda:(13,2))
    def wrap(name,fn):calls.append((name,fn));return fn
    driver._ctypes_wrap_fn=wrap
    api=SimpleNamespace(API_PROTOTYPES={'cuCtxGetDevice':(ctypes.c_int,ctypes.POINTER(ctypes.c_int)),
                                       'cuCtxSynchronize':(ctypes.c_int,)})
    modules={name:ModuleType(name) for name in ['numba','numba.cuda','numba.cuda.cudadrv']}
    modules['numba'].__version__='0.57.0'
    wrapper=SimpleNamespace(driver=driver,USE_NV_BINDING=False)
    modules['numba.cuda.cudadrv'].driver=wrapper
    modules['numba.cuda.cudadrv'].drvapi=api
    for name,module in modules.items():monkeypatch.setitem(sys.modules,name,module)
    return SimpleNamespace(driver=driver,calls=calls,device=device,synchronize=synchronize,
        unrelated=unrelated,legacy=legacy,modules=modules,api=api,wrapper=wrapper)


def test_cuda13_binds_exact_legacy_symbols_and_prototypes(binding):
    exec(source(),{})
    assert binding.driver.cuCtxGetDevice is binding.device
    assert binding.device.argtypes==(ctypes.POINTER(ctypes.c_int),)
    assert binding.device.restype is ctypes.c_int
    assert binding.driver.cuCtxSynchronize is binding.synchronize
    assert binding.synchronize.argtypes==()
    assert binding.driver.cuMemcpyDtoH is binding.unrelated
    assert [v[0] for v in binding.calls if isinstance(v,tuple)]==['cuCtxGetDevice','cuCtxSynchronize']


def test_driver12_does_not_change_any_bindings(binding):
    binding.driver.get_version=lambda:(12,8)
    exec(source(),{})
    assert binding.driver.cuCtxGetDevice is binding.legacy
    assert binding.calls==['init']


@pytest.mark.parametrize('change',['version','nvidia_binding','device_abi','synchronize_abi'])
def test_unknown_abi_or_runtime_rejected_before_binding_mutations(binding,change):
    if change=='version':binding.modules['numba'].__version__='0.63.0'
    elif change=='nvidia_binding':binding.wrapper.USE_NV_BINDING=True
    elif change=='device_abi':binding.api.API_PROTOTYPES['cuCtxGetDevice']=(ctypes.c_int,ctypes.c_void_p)
    else:binding.api.API_PROTOTYPES['cuCtxSynchronize']=(ctypes.c_int,ctypes.c_void_p)
    with pytest.raises(RuntimeError):exec(source(),{})
    assert binding.driver.cuCtxGetDevice is binding.legacy
    assert binding.driver.cuCtxSynchronize is binding.legacy


def test_abi_fix_can_be_imported_again_without_selecting_v2(binding):
    exec(source(),{});exec(source(),{})
    assert binding.driver.cuCtxGetDevice is binding.device
    assert binding.driver.cuCtxSynchronize is binding.synchronize


def test_udf_prefix_keeps_compatibility_implementation_in_companion():
    import ast
    tree=ast.parse(udf_prefix())
    assert any(isinstance(node,ast.Import) and node.names[0].name=='gpu_numba_runtime_fix' for node in tree.body)
    assert 'cuCtxGetDevice' not in udf_prefix()
