"""Stable semantic surface labels and uncalibrated optical/thermal assignments.

Semantic labels describe generated parametric components. Imported CAD uses
OCCT face numbers/signatures instead. Neither form guarantees persistent
cross-CAD topology after arbitrary geometry edits.
"""
from __future__ import annotations

import math
from pathlib import Path


INTERACTIONS=('unspecified','reflective','transmissive','absorbing','scattering','detector','structural')
SCATTER_MODELS=('none','lambertian','abg','brdf_file')


def semantic_surfaces(comp):
    """Names are stable under dimension changes; count-dependent names are not."""
    kind=comp.kind
    if kind=='mirror':return ['optical_front','substrate_back','outer_edge']+(['central_bore'] if comp.params.get('hole_diameter',0)>0 else [])
    if kind=='lens':return ['optical_front','optical_back','barrel_edge']
    if kind=='filter':return ['optical_front','optical_back','rim']
    if kind=='detector':return ['active_face','rear_face','edge']
    if kind=='aperture':return ['entrance_rim','exit_rim','edge']
    if kind in ('housing','baffle'):return ['inner_wall','outer_wall','front_annulus','rear_annulus']
    if kind=='mirror_mount':return ['inner_wall','outer_wall','front_annulus','rear_annulus']
    if kind=='vane_stack':
        return [f'vane_{i:03d}_{part}' for i in range(1,comp.params['vane_count']+1)
                for part in ('front','rear','inner_edge','outer_edge')]
    if kind=='spider':
        return ['hub_front','hub_rear','hub_edge']+[
            f'arm_{i:03d}_{part}' for i in range(1,comp.params['arm_count']+1)
            for part in ('front','rear','side')]
    if kind=='optical_bench':return ['top','bottom','edge_positive_x','edge_negative_x',
                                    'edge_positive_y','edge_negative_y']
    return []


def semantic_key(name):
    return 'semantic:'+name


def validate_surface_assignment(data):
    if not isinstance(data,dict):raise ValueError('Surface assignment must be a JSON object')
    interaction=data.get('interaction','unspecified')
    if interaction not in INTERACTIONS:raise ValueError(f'Invalid interaction: {interaction}')
    scatter=data.get('scatter_model','none')
    if scatter not in SCATTER_MODELS:raise ValueError(f'Invalid scatter model: {scatter}')
    for field in ('coating','source','brdf_file'):
        if field in data and not isinstance(data[field],str):raise ValueError(f'{field} must be text')
    for key in ('temperature_K','emissivity','roughness_rms_nm'):
        if key not in data:continue
        value=data[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError(f'{key} must be a finite number')
        if key=='temperature_K' and value<=0:raise ValueError('Surface temperature must be positive kelvin')
        if key=='emissivity' and not 0<=value<=1:raise ValueError('Surface emissivity must be within [0,1]')
        if key=='roughness_rms_nm' and value<0:raise ValueError('RMS roughness cannot be negative')
    if scatter=='brdf_file' and not data.get('brdf_file'):
        raise ValueError('File-based BRDF requires a dataset path')
    if scatter=='abg':
        params=data.get('scatter_parameters',{})
        if not isinstance(params,dict) or set(params)!={'A','B','g'}:
            raise ValueError('ABg scattering requires A, B and g coefficients')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0
               for v in params.values()):
            raise ValueError('ABg coefficients must be nonnegative and finite')


def surface_assignment_warnings(project,comp):
    warnings=[]
    names=set(semantic_surfaces(comp))
    if names:
        uncovered=sum(semantic_key(name) not in comp.surface_properties for name in names)
        if uncovered:
            warnings.append(f'WARNING — {uncovered}/{len(names)} semantic surfaces unassigned: {comp.name}')
    for key,data in comp.surface_properties.items():
        if not key.startswith('semantic:'):continue
        name=key[len('semantic:'):]
        if name not in names:
            warnings.append(f'ERROR — obsolete semantic surface assignment: {comp.name} → {name}')
            continue
        try:validate_surface_assignment(data)
        except ValueError as exc:
            warnings.append(f'ERROR — invalid surface behavior: {comp.name} → {name}: {exc}')
            continue
        coat=data.get('coating')
        if coat and coat not in project.coating_library:
            warnings.append(f'WARNING — surface coating not in coating catalog: {comp.name} → {name} ({coat})')
        scatter=data.get('scatter_model','none')
        if scatter!='none' and not data.get('source'):
            warnings.append(f'WARNING — scatter model lacks source/citation: {comp.name} → {name}')
        if scatter=='brdf_file' and not Path(data['brdf_file']).is_file():
            warnings.append(f'WARNING — BRDF file not found: {comp.name} → {name} ({data["brdf_file"]})')
        if data.get('interaction')=='absorbing' and 'emissivity' not in data:
            warnings.append(f'WARNING — absorbing surface has no thermal emissivity: {comp.name} → {name}')
    return warnings


def export_surface_assignments(project):
    """A CAD/solver-neutral list keyed by both component id and stable face label."""
    records=[]
    for comp in project.components:
        for name in semantic_surfaces(comp):
            key=semantic_key(name)
            records.append({'component_id':comp.id,'component_name':comp.name,
                            'surface_id':name,'assignment':comp.surface_properties.get(key),
                            'default_material':comp.material,'default_coating':comp.coating,
                            'is_explicitly_assigned':key in comp.surface_properties})
        for key,assignment in comp.surface_properties.items():
            if comp.kind=='cad' and key.startswith('face_'):
                records.append({'component_id':comp.id,'component_name':comp.name,
                                'surface_id':key,'assignment':assignment,
                                'default_material':comp.material,'default_coating':comp.coating,
                                'is_explicitly_assigned':True,'face_index_may_change':True})
    return records
