"""Numerical preview meshes for parametric optomechanical components."""
import numpy as np
from math import radians
from .optical_surfaces import component_sag


def transform_xyz(vertices, position, rotation):
    rx, ry, rz = np.radians(rotation)
    cx, sx = np.cos(rx), np.sin(rx)
    cy, sy = np.cos(ry), np.sin(ry)
    cz, sz = np.cos(rz), np.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return np.asarray(vertices) @ (mz @ my @ mx).T + np.asarray(position)


def surface_mesh(c, ntheta=52, nr=15, world_transform=None):
    """Return arrays X/Y/Z for Matplotlib surface rendering; None for CAD."""
    p = c.params
    theta = np.linspace(0, 2*np.pi, ntheta)
    if c.kind == 'mirror':
        radii = np.linspace(p.get('hole_diameter', 0)/2, p['diameter']/2, nr)
        R, T = np.meshgrid(radii, theta)
        x, y = R*np.cos(T), R*np.sin(T)
        z = component_sag(c,R)
    elif c.kind == 'lens':
        radii=np.linspace(0,p['diameter']/2,nr)
        R,T=np.meshgrid(radii,theta)
        front=component_sag(c,R,'front')
        back=p['center_thickness']+component_sag(c,R,'back')
        x=np.concatenate([R*np.cos(T),R*np.cos(T)],axis=0)
        y=np.concatenate([R*np.sin(T),R*np.sin(T)],axis=0)
        z=np.concatenate([front,back],axis=0)
    elif c.kind in ('baffle', 'housing'):
        R, T = np.meshgrid(np.array([p['outer_diameter']/2, p['outer_diameter']/2]), theta)
        Z, _ = np.meshgrid(np.array([-p['length']/2, p['length']/2]), theta)
        x, y, z = R*np.cos(T), R*np.sin(T), Z
    elif c.kind in ('detector', 'aperture', 'filter'):
        radii = np.linspace(0, p['diameter']/2, 2)
        R, T = np.meshgrid(radii, theta)
        x, y, z = R*np.cos(T), R*np.sin(T), np.zeros_like(R)
    else:
        return None
    points = np.stack([x, y, z], axis=-1).reshape((-1,3))
    if world_transform is None:
        points = transform_xyz(points, c.position, c.rotation)
    else:
        points = points @ world_transform[:3, :3].T + world_transform[:3, 3]
    points = points.reshape(x.shape+(3,))
    return points[..., 0], points[..., 1], points[..., 2]


def preview_surfaces(c, world_transform=None):
    """Return independent visible sheets for multi-part mechanical previews."""
    p=c.params
    if c.kind not in ('vane_stack','spider','mirror_mount','optical_bench'):
        existing=surface_mesh(c,world_transform=world_transform)
        return None if existing is None else [existing]
    sheets=[]
    def add(x,y,z):
        xyz=np.stack(np.broadcast_arrays(x,y,z),axis=-1)
        flat=xyz.reshape((-1,3))
        if world_transform is not None:
            flat=flat@world_transform[:3,:3].T+world_transform[:3,3]
        else:
            flat=transform_xyz(flat,c.position,c.rotation)
        xyz=flat.reshape(xyz.shape)
        sheets.append((xyz[...,0],xyz[...,1],xyz[...,2]))
    def ring(inner,outer,z):
        angle=np.linspace(0,2*np.pi,56)
        rad=np.array([inner,outer])
        r,t=np.meshgrid(rad,angle)
        add(r*np.cos(t),r*np.sin(t),np.full_like(r,z))
    if c.kind=='vane_stack':
        count=p['vane_count']
        for i in range(count):
            z=(i-(count-1)/2)*p['spacing']
            ring(p['inner_diameter']/2,p['outer_diameter']/2,z)
            ring(p['inner_diameter']/2,p['outer_diameter']/2,z+p['vane_thickness'])
    elif c.kind=='mirror_mount':
        ring(p['inner_diameter']/2,p['outer_diameter']/2,-p['length']/2)
        ring(p['inner_diameter']/2,p['outer_diameter']/2,p['length']/2)
        angle=np.linspace(0,2*np.pi,56)
        z=np.array([-p['length']/2,p['length']/2])
        Z,T=np.meshgrid(z,angle)
        r=p['outer_diameter']/2
        add(r*np.cos(T),r*np.sin(T),Z)
    elif c.kind=='spider':
        ring(0,p['hub_diameter']/2,0)
        for i in range(p['arm_count']):
            angle=2*np.pi*i/p['arm_count']
            direction=np.array([np.cos(angle),np.sin(angle)])
            across=np.array([-direction[1],direction[0]])
            rad,width=np.meshgrid([p['hub_diameter']/2,p['outer_diameter']/2],
                                  [-p['arm_width']/2,p['arm_width']/2])
            xy=rad[...,None]*direction+width[...,None]*across
            for z in (-p['thickness']/2,p['thickness']/2):
                add(xy[...,0],xy[...,1],np.full(rad.shape,z))
    elif c.kind=='optical_bench':
        x0,y0,z0=p['width']/2,p['length']/2,p['thickness']/2
        for z in (-z0,z0):
            x,y=np.meshgrid([-x0,x0],[-y0,y0]);add(x,y,np.full(x.shape,z))
        for x in (-x0,x0):
            y,z=np.meshgrid([-y0,y0],[-z0,z0]);add(np.full(y.shape,x),y,z)
        for y in (-y0,y0):
            x,z=np.meshgrid([-x0,x0],[-z0,z0]);add(x,np.full(x.shape,y),z)
    return sheets
