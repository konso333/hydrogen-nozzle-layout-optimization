"""Physical probe placement on circle, wrapped and reentrant sector domains."""
import copy
import math

import numpy as np
import pytest

from nozzle_platform.probe_points import interior_xy,outlet_xy


def domain(boundary=None,coordinates=None):
    config={'chamber_radius_mm':42}
    if boundary is not None:config['boundary']=boundary
    return {'config':config,'coordinates_mm':coordinates or [[0.,0.]]}


@pytest.mark.parametrize('boundary',[None,{'kind':'circle'}])
def test_circle_retains_the_previous_exact_common_probe_locations(boundary):
    model=domain(boundary);original=copy.deepcopy(model);radius=.042
    axis=np.linspace(-.8*radius,.8*radius,17)
    expected=[(x,y) for x in axis for y in axis if x*x+y*y<=(.8*radius)**2]
    np.testing.assert_array_equal(outlet_xy(model),expected)
    inside=set(expected)
    for dx,dy in [(0,0),(1,0),(-1,0),(0,1),(0,-1),(3,0),(-3,0),(0,3),(0,-3)]:
        inside.add((dx*.001,dy*.001))
    assert interior_xy(model)==sorted(inside)
    assert model==original


def test_quarter_sector_avoids_both_radial_walls_and_outside_points():
    model=domain({'kind':'sector','start_angle_deg':0,'sweep_angle_deg':90},[[15.,15.]])
    for points in [outlet_xy(model),np.array(interior_xy(model))]:
        assert len(points)>20
        assert np.all(points[:,0]>.00084) and np.all(points[:,1]>.00084)
        assert np.max(np.linalg.norm(points,axis=1))<.9*.042
        assert any(np.allclose(point,[.015,.015],atol=1e-14) for point in points)
    np.testing.assert_array_equal(outlet_xy(model),outlet_xy(model))


def test_sector_wraps_across_zero_degrees():
    model=domain({'kind':'sector','start_angle_deg':330,'sweep_angle_deg':90},[[22.,5.]])
    points=outlet_xy(model)
    angles=np.degrees(np.arctan2(points[:,1],points[:,0]))%360
    assert np.all((angles>330)|(angles<60))
    assert np.any(points[:,1]<0) and np.any(points[:,1]>0)
    for angle in [330,60]:
        ray=np.array([math.cos(math.radians(angle)),math.sin(math.radians(angle))])
        distance=np.abs(points[:,0]*ray[1]-points[:,1]*ray[0])
        assert distance.min()>.00084


def test_reentrant_sector_keeps_points_opposite_a_boundary_ray():
    model=domain({'kind':'sector','start_angle_deg':0,'sweep_angle_deg':270},[[-20.,0.]])
    points=outlet_xy(model)
    assert any(np.allclose(point,[-.0168,0],atol=1e-14) for point in points)
    assert not np.any((points[:,0]>0)&(points[:,1]<0))
    assert not np.any(np.linalg.norm(points,axis=1)<.00084)


@pytest.mark.parametrize('boundary',[
    {'kind':'triangle'},
    {'kind':'sector','sweep_angle_deg':0},
    {'kind':'sector','sweep_angle_deg':361},
    {'kind':'sector','start_angle_deg':float('nan')},
])
def test_invalid_sampling_geometry_is_rejected(boundary):
    with pytest.raises(ValueError):outlet_xy(domain(boundary))
