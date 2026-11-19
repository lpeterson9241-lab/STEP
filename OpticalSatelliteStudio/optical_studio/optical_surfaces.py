"""Signed rotationally symmetric optical sag in millimeters.

Asphere coefficients A4..A12 have units mm^(1-order). Sign and vertex
conventions must be mapped explicitly by the installed Zemax adapter.
"""
from __future__ import annotations
import numpy as np

ORDERS=(4,6,8,10,12)


def asphere_sag(radius,conic,r,coefficients=None):
    """Conic sag plus even asphere coefficients; radius=0 denotes a plane."""
    r=np.asarray(r,dtype=float)
    curvature=0.0 if radius==0 else 1.0/float(radius)
    q=1.0-(1.0+float(conic))*(curvature*r)**2
    if np.any(q < -1e-12):
        raise ValueError('Conic radicand is negative inside the optical aperture')
    sag=curvature*r*r/(1+np.sqrt(np.maximum(q,0.0)))
    for order,value in (coefficients or {}).items():
        order=int(order)
        if order not in ORDERS:raise ValueError(f'Unsupported aspheric order {order}')
        sag=sag+float(value)*r**order
    return sag


def component_sag(comp,r,side='front'):
    p=comp.params
    if comp.kind=='mirror':
        return asphere_sag(p['radius'],p.get('conic',0),r,
                          {order:p.get(f'a{order}',0) for order in ORDERS})
    if comp.kind=='lens':
        prefix='front' if side=='front' else 'back'
        return asphere_sag(p[f'{prefix}_radius'],p.get(f'{prefix}_conic',0),r,
                          {order:p.get(f'{prefix}_a{order}',0) for order in ORDERS})
    raise ValueError('Only mirrors and lenses have parametric optical sag')


def validate_prescription(comp):
    if comp.kind not in ('mirror','lens'):return
    p=comp.params
    diameter=float(p['diameter'])
    if not np.isfinite(diameter) or diameter<=0:raise ValueError('Optical diameter must be positive')
    r=np.linspace(0,diameter/2,65)
    if comp.kind=='mirror':
        component_sag(comp,r)
        if p.get('substrate_thickness',5)<=0:raise ValueError('Mirror substrate thickness must be positive')
    else:
        if p['center_thickness']<=0:raise ValueError('Lens center thickness must be positive')
        front=component_sag(comp,r,'front')
        back=p['center_thickness']+component_sag(comp,r,'back')
        if np.any(back-front<=1e-6):
            raise ValueError('Lens surfaces intersect or have nonpositive edge thickness')
