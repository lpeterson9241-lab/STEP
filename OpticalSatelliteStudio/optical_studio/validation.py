"""Explicit preflight checks; not a substitute for Zemax optical verification."""
from pathlib import Path
import numpy as np
from .assembly import world_matrix
from .catalog import has_refractive_data
from .surface_catalog import surface_assignment_warnings


def validate_project(project):
    problems = []
    try:
        project.validate()
    except ValueError as exc:
        return [f'ERROR — {exc}']
    for comp in project.components:
        matrix=world_matrix(project,comp.id)
        if not np.all(np.isfinite(matrix)):
            problems.append(f'ERROR — non-finite world transform: {comp.name}')
        if comp.kind == 'cad' and not Path(comp.cad_file).is_file():
            problems.append(f'ERROR — missing STEP: {comp.name} → {comp.cad_file}')
        if comp.kind == 'mirror':
            aperture = comp.params.get('diameter',0)
            if comp.params.get('hole_diameter',0) >= aperture:
                problems.append(f'ERROR — invalid mirror central aperture: {comp.name}')
        if comp.material.lower() == 'unspecified':
            problems.append(f'WARNING — material unspecified: {comp.name}')
        elif comp.material not in project.material_library:
            problems.append(f'WARNING — material has no measured property record: {comp.name} → {comp.material}')
        elif project.material_library[comp.material].get('status','').startswith('name_only'):
            problems.append(f'WARNING — material is a name-only candidate without sourced properties: {comp.name} → {comp.material}')
        elif comp.kind in ('lens','mirror','filter') and not has_refractive_data(project.material_library[comp.material]):
            problems.append(f'WARNING — material optical constants missing: {comp.name} → {comp.material}')
        elif comp.kind in ('lens','filter') and not project.material_library[comp.material].get('spectral_constants'):
            problems.append(f'WARNING — extinction k not supplied for optical material: {comp.name} → {comp.material}')
        if comp.kind not in ('assembly',) and comp.coating.lower() == 'unspecified':
            problems.append(f'WARNING — surface/coating unspecified: {comp.name}')
        elif comp.kind in ('lens','mirror','filter') and comp.coating not in project.coating_library:
            problems.append(f'WARNING — coating has no validated spectral record: {comp.name} → {comp.coating}')
        elif comp.kind in ('lens','mirror','filter') and not project.coating_library[comp.coating].get('spectral_rt'):
            problems.append(f'WARNING — coating spectral reflection/transmission data missing: {comp.name} → {comp.coating}')
        for face, data in comp.surface_properties.items():
            if not isinstance(face,str) or not isinstance(data,dict):
                problems.append(f'ERROR — invalid face property mapping: {comp.name}')
        problems.extend(surface_assignment_warnings(project,comp))
        if comp.kind=='cad' and comp.surface_properties:
            try:
                from .cad_analysis import face_descriptors
                descriptors={f'face_{d["index"]}':d for d in face_descriptors(comp)}
                for face_key,data in comp.surface_properties.items():
                    if not face_key.startswith('face_'):continue
                    expected=data.get('face_signature') if isinstance(data,dict) else None
                    current=descriptors.get(face_key)
                    if current is None:
                        problems.append(f'ERROR — no longer existing CAD face: {comp.name} {face_key}')
                    elif expected and expected!=current['signature']:
                        problems.append(f'WARNING — changed CAD face geometry: {comp.name} {face_key}')
            except Exception as exc:
                problems.append(f'WARNING — cannot verify CAD faces for {comp.name}: {exc}')
    try:
        from .cad_analysis import check_alignment_tolerances
        problems.extend('TOLERANCE — '+x for x in check_alignment_tolerances(project))
    except (ValueError,TypeError,KeyError) as exc:
        problems.append(f'ERROR — invalid alignment tolerance data: {exc}')
    if project.metadata.get('optical_sequence'):
        from .sequential import build_sequential_prescription
        prescription=build_sequential_prescription(project)
        problems.extend('OPTICAL PATH — '+warning for warning in prescription['warnings'])
    return problems


def validation_issues(project):
    """Stable, UI-friendly issue records while preserving the legacy text API.

    No informational warning is suppressed: every preflight line is retained.
    """
    import re
    result=[]
    names=sorted(project.components,key=lambda c:len(c.name),reverse=True)
    for index,message in enumerate(validate_project(project)):
        severity='ERROR' if message.startswith('ERROR') else ('TOLERANCE' if message.startswith('TOLERANCE') else 'WARNING')
        comp=next((c for c in names if c.name in message),None)
        low=message.lower()
        if 'coating' in low or 'surface' in low and 'optical path' not in low:
            category='Coating / surface';property_name='coating'
            suggestion='Assign a source-backed wavelength-dependent coating record.'
        elif 'material' in low or 'optical constants' in low:
            category='Material';property_name='material'
            suggestion='Import a sourced material record or set the correct material name.'
        elif 'step' in low or 'cad face' in low:
            category='CAD geometry';property_name='cad_file'
            suggestion='Check the referenced STEP file and face assignments.'
        elif 'alignment' in low or severity=='TOLERANCE':
            category='Alignment';property_name='position'
            suggestion='Inspect transforms, mates and assigned alignment tolerances.'
        elif 'optical path' in low or 'pupil' in low or 'reflective path' in low:
            category='Optical path';property_name='optical_sequence'
            suggestion='Review the ordered optical path, stop and wavelength settings.'
        else:
            category='Assembly';property_name=''
            suggestion='Inspect the component and its project parameters.'
        result.append({'issue_id':f'issue_{index:04d}', 'severity':severity,
                       'category':category,'component_id':comp.id if comp else None,
                       'component_name':comp.name if comp else 'Project',
                       'property':property_name,'message':message,
                       'suggestion':suggestion})
    return result
