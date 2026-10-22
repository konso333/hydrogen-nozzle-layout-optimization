"""Deterministic physical sampling points inside circle or sector chambers.

Existing circle sampling is preserved; sector points stay away from the two
radial walls.
"""

import math

import numpy as np


def _geometry(domain):
    config=domain['config'];radius=config['chamber_radius_mm']*.001
    boundary=config.get('boundary',{'kind':'circle'})
    if not math.isfinite(radius) or radius<=0:raise ValueError('采样半径无效。')
    kind=boundary['kind']
    if kind not in {'circle','sector'}:raise ValueError('采样仅支持圆形或扇形公共腔。')
    start=math.radians(boundary.get('start_angle_deg',0))
    sweep=math.radians(boundary.get('sweep_angle_deg',360))
    if not math.isfinite(start) or not math.isfinite(sweep) or not 0<sweep<=2*math.pi:
        raise ValueError('采样扇形角度无效。')
    return radius,kind,start,sweep


def _inside_sector(x,y,radius,start,sweep):
    if sweep>=2*math.pi-1e-12:return True
    angle=(math.atan2(y,x)-start)%(2*math.pi)
    if angle>sweep+1e-12:return False
    # Distance to each finite radial wall also works for sectors above 180°.
    for theta in (start,start+sweep):
        vx,vy=math.cos(theta),math.sin(theta)
        length=min(radius,max(0.,x*vx+y*vy))
        if math.hypot(x-length*vx,y-length*vy)<=radius*.02:return False
    return True


def _lattice(domain):
    radius,kind,start,sweep=_geometry(domain)
    axis=np.linspace(-.8*radius,.8*radius,17)
    points=[(float(x),float(y)) for x in axis for y in axis
            if x*x+y*y<=(.8*radius)**2 and
            (kind=='circle' or _inside_sector(x,y,radius,start,sweep))]
    return radius,kind,start,sweep,points


def interior_xy(domain):
    radius,kind,start,sweep,grid=_lattice(domain);points=set(grid)
    for x,y in domain['coordinates_mm']:
        for dx,dy in [(0,0),(1,0),(-1,0),(0,1),(0,-1),(3,0),(-3,0),(0,3),(0,-3)]:
            px,py=(x+dx)*.001,(y+dy)*.001
            if px*px+py*py<(.9*radius)**2 and (kind=='circle' or _inside_sector(px,py,radius,start,sweep)):
                points.add((px,py))
    if len(points)<3:raise ValueError('有效内部采样点不足，请检查公共腔与喷嘴位置。')
    return sorted(points)


def outlet_xy(domain):
    _,kind,_,_,grid=_lattice(domain)
    # Narrow sectors may have only one lattice column. Nozzle-centred probes
    # retain useful coverage without asking the interpolator for outside data.
    points=grid if kind=='circle' else interior_xy(domain)
    if len(points)<3:raise ValueError('有效出口采样点不足，请检查公共腔与喷嘴位置。')
    return np.asarray(points,float)
