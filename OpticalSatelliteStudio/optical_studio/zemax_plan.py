"""Deterministic, offline OpticStudio nonsequential CAD handoff audit.

No OpticStudio installation is needed. This plans *CAD placement* only, not
optical surface/coating/BSDF translation or a validated ray tracing model.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def _valid_rigid(matrix):
    try:
        import numpy as np
        a=np.asarray(matrix,dtype=float)
        if a.shape!=(4,4) or not np.isfinite(a).all():return False
        if not np.allclose(a[3], [0,0,0,1],atol=1e-8):return False
        rotation=a[:3,:3]
        return bool(np.allclose(rotation.T@rotation,np.eye(3),atol=1e-6)
                    and abs(np.linalg.det(rotation)-1)<1e-6)
    except (TypeError,ValueError,OverflowError):return False


def audit_handoff(manifest,package_directory=None,allow_rotations=False):
    """Return actionable review data; never silently map face/coating labels."""
    blockers=[];warnings=[];planned=[]
    if manifest.get('format')!='optical-satellite-studio-zemax-handoff':
        blockers.append('Unknown handoff format')
    if manifest.get('geometry_units')!='mm':
        blockers.append('Only millimeter CAD handoffs are supported')
    package=Path(package_directory).resolve() if package_directory is not None else None
    parts=manifest.get('cad_parts',[])
    if not isinstance(parts,list) or not parts:blockers.append('No CAD parts in handoff')
    seen_ids=set();seen_files=set()
    for i,part in enumerate(parts if isinstance(parts,list) else [],1):
        identifier=part.get('component_id','')
        filename=part.get('filename','')
        if not isinstance(identifier,str) or not identifier or identifier in seen_ids:
            blockers.append(f'Part {i}: empty or duplicate component ID')
        seen_ids.add(identifier)
        if (not isinstance(filename,str) or not filename or Path(filename).name!=filename
            or '\\' in filename or ':' in filename or filename.startswith('.')
            or not filename.lower().endswith(('.step','.stp')) or filename in seen_files):
            blockers.append(f'Part {i}: unsafe, missing or duplicate STEP filename')
            continue
        seen_files.add(filename)
        matrix=part.get('world_transform_row_major')
        if not _valid_rigid(matrix):
            blockers.append(f'Part {i}: invalid 4×4 rigid world transform')
            continue
        rotation=any(abs(float(matrix[r][c])-(1 if r==c else 0))>1e-8
                     for r in range(3) for c in range(3))
        if rotation and not allow_rotations:
            blockers.append(f'Part {i}: rotation needs explicit --allow-rotations after installed-version verification')
        sha=None
        if package is not None:
            path=package/filename
            if not path.is_file():blockers.append(f'Part {i}: STEP file missing: {filename}')
            else:
                sha=hashlib.sha256(path.read_bytes()).hexdigest()
                declared=part.get('step_sha256')
                if declared and declared!=sha:
                    blockers.append(f'Part {i}: STEP checksum differs from export manifest ({filename})')
        planned.append({'nsc_object_index':i,'component_id':identifier,'step_filename':filename,
                        'sha256':sha,'translation_mm':[float(matrix[r][3]) for r in range(3)],
                        'rotation_matrix_row_major':[list(map(float,matrix[r][:3])) for r in range(3)],
                        'rotation_requires_verification':rotation,
                        'material_label':part.get('material'),
                        'default_coating_label':part.get('default_coating')})
    assignment_records=manifest.get('component_surface_assignments',[])
    if not isinstance(assignment_records,list):
        blockers.append('Surface assignment list must be an array')
        assignment_records=[]
    assigned=[r for r in assignment_records if isinstance(r,dict) and r.get('is_explicitly_assigned')]
    unresolved=[{'component_id':r.get('component_id'),'surface_id':r.get('surface_id'),
                 'reason':'Semantic labels need a verified CAD-face-to-Zemax-face map' if
                    not str(r.get('surface_id','')).startswith('face_') else
                    'OCCT face indices are not guaranteed to match Zemax CAD face numbers'}
                for r in assigned]
    if unresolved:warnings.append(f'{len(unresolved)} optical/thermal face assignments require manual face mapping')
    if not assigned:warnings.append('No explicit per-surface optical/thermal assignments found')
    if any(p.get('material_label') for p in planned):
        warnings.append('Material labels are not automatically mapped into OpticStudio catalogs')
    if manifest.get('draft_sequential_prescription',{}).get('surfaces'):
        warnings.append('Draft optical surfaces do not create native Zemax prescription surfaces')
    assembly_filename=manifest.get('complete_assembly_step')
    if assembly_filename is not None:
        if (not isinstance(assembly_filename,str) or
            Path(assembly_filename).name!=assembly_filename or
            not assembly_filename.endswith('.step')):
            blockers.append('Unsafe complete assembly STEP filename')
        elif package is not None and not (package/assembly_filename).is_file():
            blockers.append('Complete assembly STEP file is missing')
        warnings.append('Complete assembly STEP is CAD visualization only; importing it with individual parts duplicates geometry')
    warnings.append('No source objects, ray tracing, detector setup or scattering models are generated')
    return {
        'format':'optical-satellite-studio-zemax-cad-audit','version':1,
        'status':'blocked' if blockers else 'cad_import_ready_optics_unmapped',
        'cad_import_ready':not blockers,'optical_simulation_ready':False,
        'nsc_objects':planned,'surface_assignments_review':unresolved,
        'unassigned_surface_count':len(assignment_records)-len(assigned),
        'blockers':blockers,'warnings':warnings,
        'required_next_steps':['Verify installed OpticStudio ZOS-API version/license',
                               'Verify CAD object positions and tilt order',
                               'Map OCCT/semantic surfaces to Zemax CAD face numbers',
                               'Map material and coating catalogs and scatter models',
                               'Configure source, detector and wavelength before ray tracing'],
    }


def audit_package(folder,allow_rotations=False):
    package=Path(folder)
    manifest=json.loads((package/'zemax_handoff.json').read_text(encoding='utf-8'))
    return audit_handoff(manifest,package,allow_rotations)
