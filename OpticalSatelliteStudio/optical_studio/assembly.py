"""Rigid body assembly transforms, independent of GUI and Zemax (millimeters)."""
from __future__ import annotations
import numpy as np


def local_matrix(component):
    rx, ry, rz = np.radians(component.rotation)
    cx, sx = np.cos(rx), np.sin(rx)
    cy, sy = np.cos(ry), np.sin(ry)
    cz, sz = np.cos(rz), np.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    result = np.eye(4)
    result[:3, :3] = mz @ my @ mx
    result[:3, 3] = component.position
    return result


def world_matrix(project, component_id):
    lookup = {c.id: c for c in project.components}
    if component_id not in lookup:
        raise KeyError(component_id)
    seen = set()
    chain = []
    current = component_id
    while current is not None:
        if current in seen:
            raise ValueError('Cyclic parent hierarchy')
        seen.add(current)
        c = lookup[current]
        chain.append(c)
        current = c.parent_id
    transform = np.eye(4)
    for c in reversed(chain):
        transform = transform @ local_matrix(c)
    return transform


def world_point(project, component_id, point=(0, 0, 0)):
    return (world_matrix(project, component_id) @ np.array([*point, 1.0]))[:3]


def distance_between(project, first_id, second_id):
    return float(np.linalg.norm(world_point(project, first_id) - world_point(project, second_id)))


def euler_from_rotation(matrix):
    """XYZ angles in degrees for a rigid Rz @ Ry @ Rx rotation matrix."""
    r=np.asarray(matrix)[:3,:3]
    ry=np.arcsin(np.clip(-r[2,0],-1,1))
    if abs(np.cos(ry))>1e-8:
        rx=np.arctan2(r[2,1],r[2,2]);rz=np.arctan2(r[1,0],r[0,0])
    else:
        rx=0.0;rz=np.arctan2(-r[0,1],r[1,1])
    return np.degrees([rx,ry,rz]).tolist()


def set_world_pose(project, component_id, transform):
    """Place a component in world coordinates despite its assembly parent."""
    lookup={c.id:c for c in project.components}
    target=lookup[component_id]
    parent=np.eye(4) if target.parent_id is None else world_matrix(project,target.parent_id)
    local=np.linalg.inv(parent)@np.asarray(transform)
    target.position=local[:3,3].tolist()
    target.rotation=euler_from_rotation(local)


def reparent_preserving_world(project, component_id, new_parent_id):
    """Change parent while keeping a component's world location and rotation fixed."""
    lookup = {c.id: c for c in project.components}
    c = lookup[component_id]
    if new_parent_id == component_id:
        raise ValueError('Cannot parent a component to itself')
    previous = c.parent_id
    old_local = list(c.position), list(c.rotation)
    old_world = world_matrix(project, component_id)
    parent_world = np.eye(4) if new_parent_id is None else world_matrix(project, new_parent_id)
    local = np.linalg.inv(parent_world) @ old_world
    c.parent_id = new_parent_id
    c.position = local[:3, 3].tolist()
    c.rotation = euler_from_rotation(local)
    try:
        project.validate()
    except (ValueError, KeyError):
        c.parent_id = previous
        c.position, c.rotation = old_local
        raise

