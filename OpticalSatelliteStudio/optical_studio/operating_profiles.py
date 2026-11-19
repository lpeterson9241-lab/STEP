"""Reversible first-order thermal/pose configuration of a nominal CAD project.

Linear isotropic expansion is *only* a geometric what-if approximation:
there is no FEM, mounting stress, nonlinear CTE, or thermal ray tracing.
"""
from __future__ import annotations

import copy
import math

DIMENSION_KEYS={
    'diameter','hole_diameter','outer_diameter','inner_diameter','hub_diameter',
    'length','width','thickness','center_thickness','substrate_thickness',
    'arm_width','vane_thickness','spacing','radius','front_radius','back_radius',
    'pixel_pitch_um',
}


def _vector(value,label):
    if (not isinstance(value,list) or len(value)!=3 or
        any(isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) for x in value)):
        raise ValueError(f'{label} must be three finite numbers')


def validate_profiles(project):
    profiles=project.metadata.get('operating_profiles',{})
    if not isinstance(profiles,dict):raise ValueError('Operating profiles must be named records')
    lookup={c.id for c in project.components}
    for name,profile in profiles.items():
        if not isinstance(name,str) or not name.strip() or not isinstance(profile,dict):
            raise ValueError('Operating profile requires a nonempty name and an object')
        for key in ('reference_temperature_K','default_temperature_K'):
            number=profile.get(key)
            if isinstance(number,bool) or not isinstance(number,(float,int)) or not math.isfinite(number) or number<=0:
                raise ValueError(f'{name}: {key} must be positive and finite')
        overrides=profile.get('component_overrides',{})
        if not isinstance(overrides,dict):raise ValueError(f'{name}: component overrides must be an object')
        for cid,record in overrides.items():
            if cid not in lookup:raise ValueError(f'{name}: unknown component {cid}')
            if not isinstance(record,dict):raise ValueError(f'{name}: component override must be an object')
            if 'temperature_K' in record:
                v=record['temperature_K']
                if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or v<=0:
                    raise ValueError(f'{name}: component temperature must be positive and finite')
            for field in ('position_offset_mm','rotation_offset_deg'):
                if field in record:_vector(record[field],field)


def resolve_profile(project,name):
    """Deep-copy a project, applying linear CTE and explicit *local* pose offsets."""
    validate_profiles(project)
    if name not in project.metadata.get('operating_profiles',{}):
        raise ValueError(f'Unknown operating profile {name}')
    profile=project.metadata['operating_profiles'][name]
    result=copy.deepcopy(project)
    reference=profile['reference_temperature_K']
    default=profile['default_temperature_K']
    overrides=profile.get('component_overrides',{})
    warnings=[];changes=[]
    for c in result.components:
        override=overrides.get(c.id,{})
        temperature=override.get('temperature_K',default)
        delta=temperature-reference
        c.metadata['operating_temperature_K']=temperature
        material=result.material_library.get(c.material,{})
        coefficient=material.get('cte_per_K')
        if coefficient is None:
            if abs(delta)>1e-9 and c.params:
                warnings.append(f'{c.name}: no CTE for {c.material}; dimensions unchanged')
            scale=1.0
        else:
            scale=1.0+coefficient*delta
            if not math.isfinite(scale) or scale<=0:
                raise ValueError(f'{c.name}: thermal scale is nonphysical at {temperature} K')
        for key,value in tuple(c.params.items()):
            if not isinstance(value,(int,float)) or isinstance(value,bool):continue
            if key in DIMENSION_KEYS:c.params[key]=float(value)*scale
            elif key in {f'a{n}' for n in (4,6,8,10,12)}:
                order=int(key[1:]);c.params[key]=float(value)*scale**(1-order)
            elif any(key==f'{prefix}_a{n}' for prefix in ('front','back') for n in (4,6,8,10,12)):
                order=int(key.rsplit('a',1)[-1]);c.params[key]=float(value)*scale**(1-order)
        c.position=[a+b for a,b in zip(c.position,override.get('position_offset_mm',[0.,0.,0.]))]
        c.rotation=[a+b for a,b in zip(c.rotation,override.get('rotation_offset_deg',[0.,0.,0.]))]
        if scale!=1.0 or override:
            changes.append({'component_id':c.id,'component_name':c.name,
                            'temperature_K':temperature,'linear_scale':scale,
                            'local_position_offset_mm':override.get('position_offset_mm',[0.,0.,0.]),
                            'local_rotation_offset_deg':override.get('rotation_offset_deg',[0.,0.,0.])})
    result.metadata['active_operating_profile']=name
    result.metadata['operating_profile_method']='linear_isotropic_CTE_on_local_dimensions_explicit_local_pose_offsets'
    result.validate()
    return result,{'profile_name':name,'reference_temperature_K':reference,
                   'default_temperature_K':default,'components_changed':changes,
                   'warnings':warnings+[
                       'CTE is assumed constant between reference and operating temperatures; cryogenic nonlinearities are not modeled.',
                       'Assembly positions are unchanged by contraction unless explicit pose offsets are supplied.',
                       'This is not a thermomechanical, STOP, or optical simulation.']}
