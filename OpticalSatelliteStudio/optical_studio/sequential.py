"""Explicit, solver-neutral optical path handoff; never claims a Zemax ray solve.

The selected path order is an author decision. Geometric separations are not
sequential thicknesses, especially after reflective folds or tilted surfaces.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .assembly import world_matrix

OPTICAL_KINDS = frozenset(('aperture', 'mirror', 'lens', 'filter', 'detector'))


def default_sequence(project):
    """Suggested order only: descending world Z, with the entrance first."""
    return [c.id for c in sorted(
        (c for c in project.components if c.enabled and c.kind in OPTICAL_KINDS),
        key=lambda c: (-float(world_matrix(project, c.id)[2, 3]), c.name))]


def validate_sequence(project, settings):
    if not isinstance(settings, dict):
        raise ValueError('Optical path settings must be an object')
    ids = settings.get('component_ids', [])
    lookup = {c.id: c for c in project.components}
    if not isinstance(ids, list) or len(ids) != len(set(ids)) or not ids:
        raise ValueError('Optical path needs at least one component, without duplicates')
    for cid in ids:
        if cid not in lookup or lookup[cid].kind not in OPTICAL_KINDS:
            raise ValueError(f'Unknown or non-optical component in path: {cid}')
    wavelengths = settings.get('wavelengths_um', [])
    if (not isinstance(wavelengths, list) or not wavelengths or
        any(not isinstance(w, (int, float)) or not math.isfinite(w) or w <= 0 for w in wavelengths) or
        any(a >= b for a, b in zip(wavelengths, wavelengths[1:]))):
        raise ValueError('Wavelengths must be positive, finite, and strictly increasing (µm)')
    fields = settings.get('field_angles_deg', [[0.0, 0.0]])
    if (not isinstance(fields, list) or not fields or
        any(not isinstance(f, list) or len(f) != 2 or
            any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in f) for f in fields)):
        raise ValueError('Field angles must be nonempty [X,Y] degree pairs')
    stop = settings.get('stop_component_id')
    if stop is not None and (stop not in ids or lookup[stop].kind != 'aperture'):
        raise ValueError('The stop must be an aperture included in the optical path')


def _surface(component, which, world, radius, conic, coefficients, position_offset):
    matrix = world.copy()
    matrix[:3, 3] += world[:3, 2] * position_offset
    return {
        'component_id': component.id, 'component_name': component.name,
        'surface': which, 'interaction': 'reflect' if component.kind == 'mirror' else
            ('transmit' if component.kind in ('lens', 'filter') else 'reference'),
        'radius_mm': radius, 'conic_constant': conic,
        'even_asphere_coefficients_mm_1_minus_order': coefficients,
        'clear_diameter_mm': component.params.get('diameter'),
        'center_obscuration_mm': component.params.get('hole_diameter', 0),
        'material_label': component.material, 'coating_label': component.coating,
        'world_transform_row_major': matrix.tolist(),
        'local_normal_world': matrix[:3, 2].tolist(),
    }


def build_sequential_prescription(project, settings=None):
    """Build explicit surfaces and warnings, not a ZOS-API-ready LDE."""
    project.validate()
    if settings is None:
        settings = project.metadata.get('optical_sequence') or {
            'component_ids': default_sequence(project), 'wavelengths_um': [4.0],
            'field_angles_deg': [[0.0, 0.0]], 'stop_component_id': None}
    if not settings.get('component_ids') and 'optical_sequence' not in project.metadata:
        return {'format': 'optical-satellite-studio-sequential-prescription', 'version': 1,
                'status': 'no_optical_path_defined',
                'units': {'length': 'mm', 'wavelength': 'um', 'angle': 'deg'},
                'component_order': [], 'wavelengths_um': [], 'field_angles_deg': [],
                'stop_component_id': None, 'surfaces': [],
                'warnings': ['No optical components are available; CAD-only export.']}
    validate_sequence(project, settings)
    lookup = {c.id: c for c in project.components}
    surfaces = []
    warnings = []
    from .catalog import has_refractive_data,refractive_index
    for cid in settings['component_ids']:
        c = lookup[cid]
        if not c.enabled:
            warnings.append(f'{c.name}: disabled component included in path')
        w = world_matrix(project, cid)
        if c.kind == 'mirror':
            surfaces.append(_surface(c, 'reflective_face', w, c.params['radius'],
                c.params.get('conic', 0), {f'A{n}': c.params.get(f'a{n}', 0.0) for n in (4, 6, 8, 10, 12)}, 0))
        elif c.kind == 'lens':
            for face, offset in (('front', 0.0), ('back', c.params['center_thickness'])):
                surfaces.append(_surface(c, face, w, c.params[f'{face}_radius'],
                    c.params.get(f'{face}_conic', 0),
                    {f'A{n}': c.params.get(f'{face}_a{n}', 0.0) for n in (4, 6, 8, 10, 12)}, offset))
        else:
            surfaces.append(_surface(c, c.kind, w, None, None, {}, 0))
        if c.kind == 'lens':
            record=project.material_library.get(c.material,{})
            if not has_refractive_data(record):
                warnings.append(f'{c.name}: no measured/catalog refractive-index spectrum for {c.material}')
            elif not record.get('spectral_constants'):
                warnings.append(f'{c.name}: refractive index supplied but wavelength-dependent extinction k missing')
            if has_refractive_data(record):
                for wavelength in settings['wavelengths_um']:
                    try:refractive_index(record,wavelength)
                    except ValueError as exc:
                        warnings.append(f'{c.name}: {wavelength:g} µm unavailable for {c.material} ({exc})')
    for left, right in zip(surfaces, surfaces[1:]):
        a, b = left['world_transform_row_major'], right['world_transform_row_major']
        left['next_surface_origin_distance_mm'] = math.dist(
            [a[i][3] for i in range(3)], [b[i][3] for i in range(3)])
    if any(c.kind == 'mirror' for c in (lookup[cid] for cid in settings['component_ids'])):
        warnings.append('Reflective path folds: origin distances are NOT signed sequential LDE thicknesses.')
    if settings.get('stop_component_id') is None:
        warnings.append('No stop surface chosen; aperture/entrance pupil must be verified in OpticStudio.')
    if len(settings['component_ids']) < 2:
        warnings.append('Optical path contains fewer than two components.')
    warnings.append('Surface normals, curvature signs, coordinate breaks, glass dispersion, and ray direction require installed-version Zemax verification.')
    from .thermo_optics import thermal_report
    thermo=thermal_report(project,settings['wavelengths_um'])
    index_by_component={}
    for datum in thermo['conditioned_refractive_indices']:
        index_by_component.setdefault(datum['component_id'],[]).append(datum)
    for surface in surfaces:
        if surface['interaction']=='transmit':
            surface['temperature_conditioned_material_samples']=index_by_component.get(surface['component_id'],[])
    warnings.extend(thermo['warnings'])
    return {
        'format': 'optical-satellite-studio-sequential-prescription', 'version': 1,
        'status': 'draft_mapping_only_not_a_zemax_file', 'units': {'length': 'mm', 'wavelength': 'um', 'angle': 'deg'},
        'component_order': list(settings['component_ids']),
        'wavelengths_um': list(settings['wavelengths_um']),
        'field_angles_deg': settings.get('field_angles_deg', [[0., 0.]]),
        'stop_component_id': settings.get('stop_component_id'),
        'surfaces': surfaces, 'warnings': warnings,
        'thermo_optical_status':'local_temperature_conditioning_no_ray_trace',
    }


def export_sequential_prescription(project, path, settings=None):
    result = build_sequential_prescription(project, settings)
    Path(path).write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result
