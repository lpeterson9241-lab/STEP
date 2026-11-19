"""Optional Open CASCADE/CadQuery STEP exchange for mechanical geometry.

CAD mirror surfaces are tessellated approximations of their optical prescriptions;
the Zemax adapter must use the prescription for accurate optical ray tracing.
"""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import hashlib
import re
import numpy as np
from .optical_surfaces import component_sag


def require_cadquery():
    try:
        import cadquery as cq
    except ImportError as exc:
        raise RuntimeError('STEP features need CadQuery: python -m pip install cadquery') from exc
    return cq


def make_shape(comp):
    """Create a *mechanical approximation* of a component in local mm coordinates."""
    cq = require_cadquery()
    p = comp.params
    if comp.kind in ('housing', 'baffle'):
        length = p['length']
        return (cq.Workplane('XY', origin=(0, 0, -length/2))
                .circle(p['outer_diameter']/2)
                .circle(p['inner_diameter']/2).extrude(length)).val()
    if comp.kind=='vane_stack':
        solids=[]
        for i in range(p['vane_count']):
            z=(i-(p['vane_count']-1)/2)*p['spacing']
            solids.append(cq.Workplane('XY',origin=(0,0,z))
                          .circle(p['outer_diameter']/2).circle(p['inner_diameter']/2)
                          .extrude(p['vane_thickness']).val())
        return cq.Compound.makeCompound(solids)
    if comp.kind=='spider':
        hub=cq.Workplane('XY').circle(p['hub_diameter']/2).extrude(p['thickness']/2,both=True)
        arm_length=(p['outer_diameter']-p['hub_diameter'])/2
        for i in range(p['arm_count']):
            arm=(cq.Workplane('XY').box(arm_length,p['arm_width'],p['thickness'])
                 .translate(((p['hub_diameter']+p['outer_diameter'])/4,0,0))
                 .rotate((0,0,0),(0,0,1),360*i/p['arm_count']))
            hub=hub.union(arm)
        return hub.val()
    if comp.kind=='mirror_mount':
        return (cq.Workplane('XY',origin=(0,0,-p['length']/2))
                .circle(p['outer_diameter']/2).circle(p['inner_diameter']/2)
                .extrude(p['length'])).val()
    if comp.kind=='optical_bench':
        return cq.Workplane('XY').box(p['width'],p['length'],p['thickness']).val()
    if comp.kind == 'mirror':
        r0, r1 = p.get('hole_diameter', 0)/2, p['diameter']/2
        # Profile through a conic; CAD exchange uses finite segments and is not
        # suitable as the authoritative optical surface prescription.
        radii = np.linspace(max(r0, 0.0), r1, 65)
        sag = [float(component_sag(comp,float(r))) for r in radii]
        thickness = float(p.get('substrate_thickness', 5.0))
        if thickness <= 0: raise ValueError('Mirror substrate thickness must be positive')
        profile = list(zip(radii.tolist(), sag))
        profile.extend([(r1, sag[-1]-thickness), (r0, sag[0]-thickness)])
        return cq.Workplane('XZ').polyline(profile).close().revolve(360, (0,0),(0,1)).val()
    if comp.kind=='lens':
        radii=np.linspace(0,p['diameter']/2,129)
        front=component_sag(comp,radii,'front')
        back=p['center_thickness']+component_sag(comp,radii,'back')
        if np.any(back-front<=0):raise ValueError('Lens has intersecting front/back surfaces')
        profile=list(zip(radii.tolist(),front.tolist()))
        profile.extend(zip(radii[::-1].tolist(),back[::-1].tolist()))
        return cq.Workplane('XZ').polyline(profile).close().revolve(360,(0,0),(0,1)).val()
    if comp.kind=='aperture':
        # Diameter is the CLEAR opening, not an opaque disk blocking the beam.
        clear=float(p['diameter'])
        outer=clear+2*max(float(p.get('rim_width',clear*.05)),.25)
        thickness=float(p.get('thickness',1.0))
        return cq.Workplane('XY').circle(outer/2).circle(clear/2).extrude(thickness).val()
    if comp.kind in ('detector','filter'):
        thickness = float(p.get('thickness', 1.0))
        return cq.Workplane('XY').circle(p['diameter']/2).extrude(thickness).val()
    if comp.kind == 'cad':
        source = Path(comp.cad_file)
        if not source.is_file(): raise FileNotFoundError(source)
        return cq.importers.importStep(str(source)).val()
    return None


def tessellate_component(comp, tolerance=0.8):
    """Triangles for rendering; source B-rep remains independent of preview mesh."""
    shape = make_shape(comp)
    if shape is None: return None
    vertices, triangles = shape.tessellate(tolerance)
    xyz = np.asarray([[v.x, v.y, v.z] for v in vertices], dtype=float)
    faces = np.asarray(triangles, dtype=int)
    return xyz, faces


def export_cad_package(project, directory, nominal_project=None):
    """Write local STEP solids and optical handoff data in a self-contained folder."""
    from .assembly import world_matrix
    project.validate()
    if nominal_project is not None:
        nominal_project.validate()
        if {c.id for c in nominal_project.components}!={c.id for c in project.components}:
            raise ValueError('Nominal project and operating export must have the same component IDs')
    cq = require_cadquery()
    from .assembly import euler_from_rotation
    folder = Path(directory)
    if folder.exists() and any(folder.iterdir()):
        raise FileExistsError(f'Export directory must be empty: {folder}')
    # Preflight all solids before writing any part or manifest. A missing CAD
    # reference or invalid shell cannot silently disappear from the handoff.
    shapes=[]
    for comp in project.components:
        if comp.kind=='assembly':continue
        shape=make_shape(comp)
        if shape is None:raise ValueError(f'{comp.name}: unsupported STEP geometry ({comp.kind})')
        if shape.isNull() or not shape.isValid() or not shape.Solids():
            raise ValueError(f'{comp.name}: invalid or non-solid STEP geometry')
        shapes.append((comp,shape,world_matrix(project,comp.id)))
    folder.mkdir(parents=True, exist_ok=True)
    from .export import export_manifest
    manifest_path = folder/'zemax_handoff.json'
    manifest = export_manifest(project, str(manifest_path))
    from .results_exchange import geometry_signature
    (folder/'external_results_manifest_template.json').write_text(json.dumps({
        'format':'optical-satellite-studio-external-results','version':1,
        'coordinate_frame':'project_world_xyz_mm',
        'project_geometry_sha256':geometry_signature(project),
        'rays_csv':'rays.csv','detector_csv':'detector.csv',
        'note':'Template only. Copy to results_manifest.json and supply normalized ray/detector CSV files from an independently validated solver export.'
    },indent=2),encoding='utf-8')
    from .surface_catalog import export_surface_assignments
    (folder/'surface_assignments.json').write_text(json.dumps(export_surface_assignments(project),indent=2),encoding='utf-8')
    from .sequential import export_sequential_prescription
    export_sequential_prescription(project, folder/'optical_prescription_draft.json')
    manifest['cad_parts'] = []
    assembly=cq.Assembly(name='OpticalSatelliteStudio_Complete_Satellite')
    for comp,shape,matrix in shapes:
        filename = f'{comp.id}.step'
        cq.exporters.export(shape, str(folder/filename), exportType='STEP')
        location=cq.Location(tuple(map(float,matrix[:3,3])),
                             tuple(map(float,euler_from_rotation(matrix))))
        safe_name=re.sub(r'[^A-Za-z0-9_]+','_',comp.name).strip('_')[:60]
        assembly.add(shape,name=f'{safe_name}_{comp.id[:8]}',loc=location)
        manifest['cad_parts'].append({
            'component_id': comp.id,
            'filename': filename,
            'world_transform_row_major': world_matrix(project, comp.id).tolist(),
            'material': comp.material,
            'default_coating': comp.coating,
            'surface_properties': comp.surface_properties,
            'optical_prescription': comp.params if comp.kind in ('mirror','lens') else None,
            'optical_geometry_exact': False if comp.kind in ('mirror','lens') else None,
            'step_sha256':hashlib.sha256((folder/filename).read_bytes()).hexdigest(),
            'geometry_representation':'sampled_cad_surface_not_optical_authority' if comp.kind in ('mirror','lens') else 'cad_solid',
        })
    assembly_file='Complete_Satellite_Assembly.step'
    if shapes:
        assembly.save(str(folder/assembly_file),exportType='STEP')
    else:
        assembly_file=None
    manifest['complete_assembly_step']=assembly_file
    manifest['complete_assembly_purpose']='physical CAD visualization only; not imported alongside separate parts (would double geometry)'
    from .surface_catalog import export_surface_assignments
    from .thermo_optics import thermal_report
    from .validation import validate_project
    from .sequential import build_sequential_prescription
    from .assembly import world_matrix
    files={
        'Material_Properties.json':project.material_library,
        'Coating_Properties.json':project.coating_library,
        'Surface_Properties.json':export_surface_assignments(project),
        'Assembly_Transforms.json':{
            c.id:{'name':c.name,'parent_id':c.parent_id,
                  'local_position_mm':c.position,'local_rotation_xyz_deg':c.rotation,
                  'world_transform_row_major':world_matrix(project,c.id).tolist()}
            for c in project.components},
        'Operating_Configurations.json':{
            'defined_profiles':project.metadata.get('operating_profiles',{}),
            'exported_profile':project.metadata.get('active_operating_profile'),
            'method':project.metadata.get('operating_profile_method'),
            'note':'STEP geometry reflects the profile selected at export; Nominal_Project.json stores the unmodified original design.'},
        'Nominal_Project.json':(nominal_project or project).to_dict(),
        'Optical_Prescription.json':build_sequential_prescription(project),
        'Thermo_Optical_Audit.json':thermal_report(project),
    }
    for name,data in files.items():
        (folder/name).write_text(json.dumps(data,indent=2),encoding='utf-8')
    manifest['supplementary_files']=list(files)
    manifest['supplementary_files'].extend(['Validation_Report.json','README_IMPORT.txt'])
    manifest['status'] = 'cad_step_package_created_zos_api_model_not_generated'
    manifest['limitations'].append('STEP optical mirrors and lenses use sampled aspheric profiles; preserve optical prescriptions for native Zemax surfaces.')
    manifest['windows_zos_api_builder']='build_zemax.py'
    manifest['limitations'].append('Windows ZOS-API builder creates an NSC CAD-only .zos, not an optically validated system; materials, sources and detectors require further mapping.')
    manifest['limitations'].append('Do not import the complete assembly STEP and individual STEP files together: that duplicates solids.')
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    shutil.copy2(Path(__file__).with_name('zemax_windows.py'),folder/'build_zemax.py')
    shutil.copy2(Path(__file__).with_name('zemax_plan.py'),folder/'zemax_plan.py')
    from .zemax_plan import audit_package
    audit=audit_package(folder)
    (folder/'zemax_cad_audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    validation={
        'format':'optical-satellite-studio-export-validation','version':1,
        'status':'cad_export_complete_optical_validation_pending',
        'cad_part_count':len(shapes),
        'complete_assembly_step':assembly_file,
        'project_preflight_issues':validate_project(project),
        'zemax_import_audit':audit,
        'optical_precision_note':'Mirrors/lenses are CAD approximations; native optical prescriptions must be verified in OpticStudio.',
        'surface_face_mapping_verified':False,
        'simulation_performed':False,
    }
    (folder/'Validation_Report.json').write_text(json.dumps(validation,indent=2),encoding='utf-8')
    (folder/'README_IMPORT.txt').write_text(
        'Optical Satellite Studio CAD / OpticStudio handoff\n'
        '================================================\n'
        '1. Use Complete_Satellite_Assembly.step to inspect the physical assembly in CAD.\n'
        '2. Use the individual <component-id>.step files for OpticStudio NSC objects.\n'
        '   Never import both the assembly and the separate parts in the same ray-trace scene.\n'
        '3. Run: python build_zemax.py . target.zos --dry-run\n'
        '4. Review Validation_Report.json and zemax_cad_audit.json.\n'
        '5. On Windows with a compatible OpticStudio ZOS-API, run the builder after audit.\n'
        '6. Import/verify native high-precision surfaces from Optical_Prescription.json;\n'
        '   set coatings, scatter, optical materials, sources, detectors and all face maps.\n'
        '7. This package does NOT validate optical ray tracing or produce a ready-to-simulate telescope.\n',
        encoding='utf-8')
    return manifest
