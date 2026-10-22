"""Checks of physical limits, species indexing and misleading audit inputs."""
import copy
import math

import numpy as np
import pytest

from nozzle_platform.transport_audit import components, differences, evaluate, render_gpu_udf


@pytest.fixture
def material():
    return {'names': ['light', 'heavy'], 'molecular_weight': [1., 16.],
            'viscosity': [1., 4.], 'thermal_conductivity': [100., 2.]}


def test_pure_species_limits_for_both_laws(material):
    values = evaluate(np.eye(2), material)
    for prefix in ('ideal', 'mass'):
        np.testing.assert_allclose(values[prefix+'_viscosity'], [1, 4], rtol=1e-14)
        np.testing.assert_allclose(values[prefix+'_conductivity'], [100, 2], rtol=1e-14)


def test_equal_mole_binary_hand_solution_uses_mu_for_conductivity_phi(material):
    # M=(1,16), mu=(1,4), X=(1/2,1/2): phi12=4/sqrt(8.5), phi21=4/sqrt(136).
    a, b = 1/(1+4/math.sqrt(8.5)), 1/(1+4/math.sqrt(136))
    values = evaluate([[1/17, 16/17]], material)
    assert values['ideal_viscosity'][0] == pytest.approx(a+4*b, rel=1e-14)
    assert values['ideal_conductivity'][0] == pytest.approx(100*a+2*b, rel=1e-14)
    assert values['mass_viscosity'][0] == pytest.approx(65/17)


def test_hydrogen_air_reference_at_ten_percent_mass():
    # Values frozen from the separately inspected saved Fluent component table.
    data = {'names': ['h2','o2','n2'], 'molecular_weight': [2.01594,31.9988,28.0134],
            'viscosity': [8.411e-6,1.919e-5,1.663e-5], 'thermal_conductivity': [.1672,.0246,.0242]}
    result = evaluate([[.1,.9*.233,.9*.767]], data)
    assert result['ideal_viscosity'][0] == pytest.approx(1.581298430269534e-5, rel=1e-12)
    assert result['ideal_conductivity'][0] == pytest.approx(.09297509886462861, rel=1e-12)


def test_reordering_species_and_columns_together_preserves_values(material):
    before = evaluate([[.3,.7]], material)
    reverse = {k:list(reversed(v)) for k,v in material.items()}
    after = evaluate([[.7,.3]], reverse)
    for key in before:
        np.testing.assert_allclose(before[key], after[key], rtol=1e-14)


@pytest.mark.parametrize('y', [[], [1.,0.], [[.5,.4]], [[-.001,1.001]], [[float('nan'),0]], [[0,0]], [[.5,.5,0]]])
def test_invalid_composition_is_rejected_without_repair(material,y):
    with pytest.raises(ValueError):
        evaluate(y,material)


def test_valid_input_is_not_modified(material):
    y=np.array([[.3,.7]])
    before=y.copy()
    evaluate(y,material)
    np.testing.assert_array_equal(y,before)


def test_components_follows_solver_order_not_dictionary_order():
    item=lambda mu: {'molecular_weight':{'option':'value','value':1},
                     'viscosity':{'option':'value','value':mu},
                     'thermal_conductivity':{'option':'value','value':1}}
    state={'materials':{'mixture':{'hydrogen-air':{'species':{
        'volumetric':['heavy','light'], 'volumetric_species':{'light':item(1),'heavy':item(4)}}}}}}
    assert components(state)['viscosity'].tolist()==[4,1]
    changed=copy.deepcopy(state)
    changed['materials']['mixture']['hydrogen-air']['species']['volumetric_species']['heavy']['viscosity']['option']='kinetic-theory'
    with pytest.raises(ValueError,match='constant'):
        components(changed)
    changed['materials']['mixture']['hydrogen-air']['species']['volumetric']=['light','light']
    with pytest.raises(ValueError,match='order'):
        components(changed)


@pytest.mark.parametrize('values', [[0,1], [-1,1], [float('nan'),1], [1], [float('inf'),1]])
def test_invalid_component_properties_are_rejected(material, values):
    material['viscosity']=values
    with pytest.raises(ValueError):evaluate([[.3,.7]],material)


def test_signed_difference_uses_original_as_denominator_and_labels_weighting():
    result=differences([2,4],[1,6])
    assert result['signed_relative_min']==-.5
    assert result['signed_relative_max']==.5
    assert result['absolute_relative_max']==.5
    assert 'not volume weighted' in result['weighting']


@pytest.mark.parametrize('a,b', [([],[]), ([0],[1]), ([1],[float('nan')]), ([1,2],[1])])
def test_invalid_comparisons_rejected(a,b):
    with pytest.raises(ValueError):differences(a,b)


def test_generated_gpu_arithmetic_matches_offline_laws_including_water():
    from types import SimpleNamespace
    material={'names':['h2','o2','h2o','n2'],
              'molecular_weight':[2.01594,31.9988,18.01534,28.0134],
              'viscosity':[8.411e-6,1.919e-5,1.34e-5,1.663e-5],
              'thermal_conductivity':[.1672,.0246,.0261,.0242]}
    hooks=[]
    namespace={'UDF':SimpleNamespace(add_laminar_viscosity=lambda fn,name:hooks.append((fn.__name__,name)),
                                   add_conductivity=lambda fn,name:hooks.append((fn.__name__,name)))}
    exec(compile(render_gpu_udf(material),'<generated GPU candidate>','exec'),namespace)
    y=np.vstack((np.eye(4),np.random.default_rng(123).dirichlet(np.ones(4),size=100)))
    expected=evaluate(y,material)
    for fn,prop in [('udf_ideal_mu','viscosity'),('udf_ideal_k','conductivity')]:
        actual=np.array([namespace[fn](*row) for row in y])
        np.testing.assert_allclose(actual,expected['ideal_'+prop],rtol=2e-15)
    assert hooks==[('udf_ideal_mu','hydrogen-air'),('udf_ideal_k','hydrogen-air')]
