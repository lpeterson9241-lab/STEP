"""Deterministic rigid placement constraints for optomechanical assemblies.

One controlling constraint per target. This is not a nonlinear CAD mate solver;
invalid/cyclic/over-constrained relationships fail explicitly.
"""
from __future__ import annotations
from types import SimpleNamespace
import numpy as np
from .assembly import local_matrix, world_matrix, set_world_pose


TYPES=('coincident','coaxial','axial_gap','relative_pose')


def order_constraints(project):
    lookup={c.id:c for c in project.components}
    used_targets=set()
    remaining=[]
    for rule in project.constraints:
        if not isinstance(rule,dict) or rule.get('type') not in TYPES:
            raise ValueError('Unknown or invalid geometric constraint type')
        source,target=rule.get('source_id'),rule.get('target_id')
        if source not in lookup or target not in lookup or source==target:
            raise ValueError('Constraint refers to an invalid source or target')
        if target in used_targets:
            raise ValueError('Overconstraint: target already controlled by another placement rule')
        used_targets.add(target)
        ancestor=lookup[source].parent_id
        while ancestor is not None:
            if ancestor==target:
                raise ValueError('Cannot constrain an ancestor from its own descendant')
            ancestor=lookup[ancestor].parent_id
        if rule['type']=='axial_gap':
            _finite(rule.get('gap_mm',0),'gap_mm')
        if rule['type']=='relative_pose':
            for key in ('offset_mm','rotation_deg'):
                vector=rule.get(key,[0,0,0])
                if not isinstance(vector,(list,tuple)) or len(vector)!=3:
                    raise ValueError(f'Constraint {key} must have three elements')
                for v in vector:_finite(v,key)
        remaining.append(rule)
    ordered=[]
    while remaining:
        target_ids={rule['target_id'] for rule in remaining}
        ready=[rule for rule in remaining if rule['source_id'] not in target_ids]
        if not ready:raise ValueError('Cyclic geometric placement constraints')
        ordered.extend(ready)
        remaining=[rule for rule in remaining if rule not in ready]
    return ordered


def _finite(x,name):
    if not isinstance(x,(int,float)) or not np.isfinite(x):
        raise ValueError(f'Invalid {name}')


def apply_constraints(project):
    lookup={c.id:c for c in project.components}
    for rule in order_constraints(project):
        source=world_matrix(project,rule['source_id'])
        target=world_matrix(project,rule['target_id'])
        kind=rule['type']
        if kind=='coincident':
            transform=source
        elif kind=='relative_pose':
            offset=SimpleNamespace(position=rule.get('offset_mm',[0,0,0]),
                                   rotation=rule.get('rotation_deg',[0,0,0]))
            transform=source@local_matrix(offset)
        elif kind=='axial_gap':
            offset=SimpleNamespace(position=[0,0,rule.get('gap_mm',0)],rotation=[0,0,0])
            transform=source@local_matrix(offset)
        elif kind=='coaxial':
            # Preserve axial distance measured in the source's local Z frame.
            axial=(source[:3,:3].T@(target[:3,3]-source[:3,3]))[2]
            transform=source.copy()
            transform[:3,3]=source[:3,3]+source[:3,2]*axial
        set_world_pose(project,rule['target_id'],transform)
    return len(project.constraints)
