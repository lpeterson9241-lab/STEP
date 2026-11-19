"""Editable signed axial spacing between optical component coordinate origins.

The reference axis is the source component's local +Z direction in world space.
The operation preserves transverse offset and target world orientation; it is
not a surface-to-surface clearance or a sequential ray-path thickness.
"""
from __future__ import annotations

import math
import numpy as np

from .assembly import world_matrix,set_world_pose
from .constraints import apply_constraints
from .sequential import default_sequence


def optical_order(project):
    settings=project.metadata.get('optical_sequence',{})
    ids=settings.get('component_ids') if settings else None
    if ids:return list(ids)
    return default_sequence(project)


def mechanical_order(project):
    """Enabled physical parts (not grouping nodes), sorted by world Z."""
    return [c.id for c in sorted((c for c in project.components if c.kind!='assembly' and c.enabled),
                                key=lambda c:(-float(world_matrix(project,c.id)[2,3]),c.name))]


def spacing_measure(project,source_id,target_id):
    source=world_matrix(project,source_id)
    target=world_matrix(project,target_id)
    delta=target[:3,3]-source[:3,3]
    axial=float(np.dot(delta,source[:3,2]))
    lateral=delta-axial*source[:3,2]
    return {'signed_axial_mm':axial,'transverse_mm':float(np.linalg.norm(lateral)),
            'center_distance_mm':float(np.linalg.norm(delta))}


def set_axial_spacing(project,source_id,target_id,gap_mm):
    """Transactional target move along source optical axis, honoring axial_gap mates."""
    if source_id==target_id or not math.isfinite(gap_mm):
        raise ValueError('Select distinct components and a finite axial separation')
    lookup={c.id:c for c in project.components}
    if source_id not in lookup or target_id not in lookup:
        raise ValueError('Unknown source or target component')
    rule=next((c for c in project.constraints if c['target_id']==target_id),None)
    if rule is not None and (rule['type']!='axial_gap' or rule['source_id']!=source_id):
        raise ValueError('Target is controlled by another placement constraint; edit that constraint')
    previous_pose=[(c.id,list(c.position),list(c.rotation)) for c in project.components]
    previous_gap=rule.get('gap_mm') if rule is not None else None
    try:
        if rule is not None:
            rule['gap_mm']=float(gap_mm)
            apply_constraints(project)
        else:
            measure=spacing_measure(project,source_id,target_id)
            source=world_matrix(project,source_id)
            target=world_matrix(project,target_id)
            target[:3,3]+=(float(gap_mm)-measure['signed_axial_mm'])*source[:3,2]
            set_world_pose(project,target_id,target)
            apply_constraints(project)
        project.validate()
        actual=spacing_measure(project,source_id,target_id)['signed_axial_mm']
        if not math.isclose(actual,gap_mm,abs_tol=1e-5,rel_tol=1e-9):
            raise ValueError('Placement constraints prevented the requested separation')
    except Exception:
        for cid,pos,rot in previous_pose:
            lookup[cid].position=pos;lookup[cid].rotation=rot
        if rule is not None:rule['gap_mm']=previous_gap
        raise
    return spacing_measure(project,source_id,target_id)
