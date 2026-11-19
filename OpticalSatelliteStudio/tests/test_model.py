import json
import pytest
import numpy as np
from optical_studio.model import cassegrain_template, Project, component
from optical_studio.geometry import transform_xyz, surface_mesh
from optical_studio.export import export_manifest
from optical_studio.model import opir_template
from optical_studio.assembly import world_point, reparent_preserving_world, distance_between
from optical_studio.validation import validate_project

def test_operating_profile_linear_cte_and_nominal_preservation(tmp_path):
    from optical_studio.operating_profiles import resolve_profile
    p=cassegrain_template()
    mirror=next(c for c in p.components if c.kind=='mirror')
    mirror.material='Germanium'
    mirror.params['a4']=1e-9
    original_diameter=mirror.params['diameter']
    original_position=list(mirror.position)
    p.metadata['operating_profiles']={'Cold alignment':{
        'reference_temperature_K':293.15,'default_temperature_K':293.15,
        'component_overrides':{mirror.id:{'temperature_K':80.0,
                                         'position_offset_mm':[0.1,0,0],
                                         'rotation_offset_deg':[0,0.01,0]}}}}
    transformed,report=resolve_profile(p,'Cold alignment')
    derived=next(c for c in transformed.components if c.id==mirror.id)
    scale=1+6.1e-6*(80-293.15)
    assert derived.params['diameter']==pytest.approx(original_diameter*scale)
    assert derived.params['a4']==pytest.approx(1e-9*scale**-3)
    assert derived.position[0]==pytest.approx(original_position[0]+0.1)
    assert derived.rotation[1]==pytest.approx(0.01)
    assert mirror.params['diameter']==original_diameter
    assert mirror.params['a4']==1e-9
    assert mirror.position==original_position
    assert report['components_changed']
    path=tmp_path/'configured.json';p.save(path)
    loaded=Project.load(path)
    assert loaded.metadata['operating_profiles']==p.metadata['operating_profiles']

def test_operating_profile_validation_and_unknown_cte():
    from optical_studio.operating_profiles import resolve_profile
    p=cassegrain_template();mirror=next(c for c in p.components if c.kind=='mirror')
    mirror.material='Uncataloged substrate'
    p.metadata['operating_profiles']={'Cold':{'reference_temperature_K':290,
        'default_temperature_K':90,'component_overrides':{}}}
    derived,report=resolve_profile(p,'Cold')
    assert next(c for c in derived.components if c.id==mirror.id).params['diameter']==mirror.params['diameter']
    assert any('no CTE' in x for x in report['warnings'])
    p.metadata['operating_profiles']['Cold']['component_overrides']={'missing-component':{'temperature_K':80}}
    with pytest.raises(ValueError,match='unknown component'):p.validate()

def test_opir_has_cryo_profile():
    from optical_studio.operating_profiles import resolve_profile
    p=opir_template();p.validate()
    assert 'Cryogenic detector concept' in p.metadata['operating_profiles']
    derived,report=resolve_profile(p,'Cryogenic detector concept')
    detector=next(c for c in derived.components if c.kind=='detector')
    assert detector.metadata['operating_temperature_K']==80
    assert report['profile_name']=='Cryogenic detector concept'

def test_planck_radiance_temperature_and_emissivity():
    from optical_studio.thermo_optics import planck_radiance_w_m2_sr_um,thermal_report
    assert planck_radiance_w_m2_sr_um(4.,290)>planck_radiance_w_m2_sr_um(4.,80)>0
    p=opir_template()
    mirror=next(c for c in p.components if c.kind=='mirror')
    mirror.surface_properties['semantic:optical_front']={'emissivity':0.05,'temperature_K':290}
    result=thermal_report(p,[4.])
    emitted=next(x for x in result['surface_thermal_radiance'] if x['component_id']==mirror.id)
    assert emitted['spectral_exit_radiance_W_m2_sr_um'][0]==pytest.approx(
        0.05*planck_radiance_w_m2_sr_um(4.,290))
    assert 'no_ray_tracing' in result['status']

def test_thermo_index_requires_range_and_source():
    from optical_studio.thermo_optics import conditioned_index
    p=opir_template();record=p.material_library['Germanium']
    with pytest.raises(ValueError,match='Missing sourced dn/dT'):
        conditioned_index(record,4.,250.)
    record.update(dn_dT_per_K=4e-4,thermo_optic_source='User supplied test coefficient',
                  thermo_optic_temperature_range_K=[200.,320.])
    p.validate()
    base=conditioned_index(record,4.,293.)
    conditioned=conditioned_index(record,4.,250.)
    assert conditioned['n']==pytest.approx(base['n']+4e-4*(250.-293.))
    assert conditioned['k'] is None
    with pytest.raises(ValueError,match='outside'):
        conditioned_index(record,4.,80.)
    record.pop('thermo_optic_source')
    with pytest.raises(ValueError,match='provenance'):p.validate()

def test_thermo_audit_in_zemax_manifest(tmp_path):
    p=opir_template();path=tmp_path/'handoff.json'
    export_manifest(p,path)
    data=json.loads(path.read_text())
    assert data['thermo_optical_audit']['status']=='local_properties_only_no_ray_tracing_or_radiative_transfer'

def test_temperature_conditioned_coating_with_energy_bounds():
    from optical_studio.thermo_optics import conditioned_coating
    from optical_studio.catalog import validate_physical_record
    record={'spectral_rt':[{'wavelength_um':3.,'reflectance':0.6,'transmittance':0.2},
                           {'wavelength_um':5.,'reflectance':0.5,'transmittance':0.3}],
            'thermal_reference_temperature_K':290.,
            'thermal_valid_temperature_range_K':[200.,320.],
            'thermal_source':'User-provided test slope',
            'temperature_coefficients_per_K':{'reflectance':0.001,'transmittance':0.0}}
    validate_physical_record(record,'coating')
    result=conditioned_coating(record,4.,280.)
    assert result['reflectance']==pytest.approx(0.54)
    assert result['transmittance']==pytest.approx(0.25)
    assert result['absorptance']==pytest.approx(0.21)
    with pytest.raises(ValueError,match='validity range'):
        conditioned_coating(record,4.,150.)

def test_editable_optical_spacing_preserves_transverse_and_nominal_orientation():
    from optical_studio.spacing import spacing_measure,set_axial_spacing,optical_order
    p=cassegrain_template()
    ids=optical_order(p)
    first,second=ids[0],ids[1]
    before=spacing_measure(p,first,second)
    target=next(c for c in p.components if c.id==second)
    previous_rotation=list(target.rotation)
    result=set_axial_spacing(p,first,second,before['signed_axial_mm']+12.5)
    assert result['signed_axial_mm']==pytest.approx(before['signed_axial_mm']+12.5)
    assert result['transverse_mm']==pytest.approx(before['transverse_mm'])
    assert target.rotation==pytest.approx(previous_rotation)

def test_spacing_obeys_and_updates_axial_constraint():
    from optical_studio.spacing import spacing_measure,set_axial_spacing
    p=opir_template()
    rule=next(r for r in p.constraints if r['type']=='axial_gap')
    set_axial_spacing(p,rule['source_id'],rule['target_id'],420.)
    assert rule['gap_mm']==420.
    assert spacing_measure(p,rule['source_id'],rule['target_id'])['signed_axial_mm']==pytest.approx(420.)
    with pytest.raises(ValueError,match='another placement constraint'):
        set_axial_spacing(p,p.components[0].id,rule['target_id'],20.)

def test_mechanical_spacing_order_includes_structure():
    from optical_studio.spacing import mechanical_order,optical_order
    p=opir_template()
    mechanical=mechanical_order(p)
    assert all(next(c for c in p.components if c.id==cid).kind!='assembly' for cid in mechanical)
    assert len(mechanical)>len(optical_order(p))
    assert any(next(c for c in p.components if c.id==cid).kind=='baffle' for cid in mechanical)

def test_template():
    p=cassegrain_template()
    assert len(p.components)==6
    assert len({c.id for c in p.components})==6
    p.validate()

def test_round_trip(tmp_path):
    p=cassegrain_template();file=tmp_path/'scene.json'
    p.save(file);q=Project.load(file)
    assert q.to_dict()==p.to_dict()

def test_invalid_geometry():
    p=cassegrain_template();p.components[0].params['diameter']=-5
    with pytest.raises(ValueError):p.validate()

def test_rotation():
    result=transform_xyz([[1,0,0]],[1,2,3],[0,0,90])
    np.testing.assert_allclose(result,[[1,3,3]],atol=1e-10)

def test_mesh():
    p=cassegrain_template()
    for c in p.components:
        xyz=surface_mesh(c)
        assert len(xyz)==3
        assert all(np.isfinite(a).all() for a in xyz)

def test_handoff(tmp_path):
    p=cassegrain_template();file=tmp_path/'handoff.json'
    export_manifest(p,file)
    data=json.loads(file.read_text())
    assert data['target_mode']=='non_sequential'
    assert data['status']=='preflight_only_not_an_opticstudio_file'
    assert data['draft_sequential_prescription']['surfaces']

def test_sequential_lens_two_faces_and_reflection():
    from optical_studio.sequential import build_sequential_prescription
    p=Project()
    aperture=component('aperture','Stop',(0,0,30))
    lens=component('lens','Infrared lens',(0,0,20),center_thickness=10,front_a4=1e-9)
    mirror=component('mirror','Fold',(0,0,0))
    p.components=[aperture,lens,mirror]
    settings={'component_ids':[aperture.id,lens.id,mirror.id],
              'wavelengths_um':[3.0,4.0,5.0],
              'field_angles_deg':[[0.,0.],[0.,2.]],'stop_component_id':aperture.id}
    p.metadata['optical_sequence']=settings
    p.validate()
    result=build_sequential_prescription(p)
    assert [s['surface'] for s in result['surfaces']]==['aperture','front','back','reflective_face']
    assert result['surfaces'][1]['even_asphere_coefficients_mm_1_minus_order']['A4']==1e-9
    assert result['surfaces'][1]['next_surface_origin_distance_mm']==pytest.approx(10.0)
    assert result['surfaces'][-1]['interaction']=='reflect'
    assert any('NOT signed sequential' in warning for warning in result['warnings'])

def test_sequential_path_validation_rejects_duplicate_and_wrong_stop():
    p=cassegrain_template()
    aperture=next(c for c in p.components if c.kind=='aperture')
    mirror=next(c for c in p.components if c.kind=='mirror')
    p.metadata['optical_sequence']={'component_ids':[mirror.id,mirror.id],
        'wavelengths_um':[4.], 'field_angles_deg':[[0.,0.]],'stop_component_id':None}
    with pytest.raises(ValueError,match='duplicates'):p.validate()
    p.metadata['optical_sequence']['component_ids']=[aperture.id,mirror.id]
    p.metadata['optical_sequence']['stop_component_id']=mirror.id
    with pytest.raises(ValueError,match='stop'):p.validate()

def test_sequential_handoff_for_cad_only(tmp_path):
    from optical_studio.sequential import build_sequential_prescription
    p=Project(components=[component('cad','Bus')])
    assert build_sequential_prescription(p)['status']=='no_optical_path_defined'
    export_manifest(p,tmp_path/'handoff.json')

def test_sequential_settings_persist_roundtrip(tmp_path):
    p=cassegrain_template()
    from optical_studio.sequential import default_sequence
    p.metadata['optical_sequence']={'component_ids':default_sequence(p),
        'wavelengths_um':[3.,5.], 'field_angles_deg':[[0.,0.]], 'stop_component_id':None}
    target=tmp_path/'project.json';p.save(target)
    assert Project.load(target).metadata['optical_sequence']==p.metadata['optical_sequence']

def test_opir_transform_hierarchy_and_reparent():
    p=opir_template()
    primary=next(c for c in p.components if c.name=='Primary IR mirror')
    assert np.allclose(world_point(p, primary.id), [0,0,450])
    original=world_point(p, primary.id).copy()
    reparent_preserving_world(p, primary.id, None)
    np.testing.assert_allclose(world_point(p, primary.id),original,atol=1e-9)
    assert primary.parent_id is None

def test_parent_cycle_rejected():
    p=opir_template()
    p.components[0].parent_id=p.components[2].id
    with pytest.raises(ValueError,match='Cyclic'):p.validate()

def test_links_and_roundtrip(tmp_path):
    p=opir_template()
    primary=next(c for c in p.components if c.name=='Primary IR mirror')
    tube=next(c for c in p.components if c.name=='Optical enclosure')
    primary.params['diameter']=340
    p.apply_links()
    assert tube.params['inner_diameter']==360
    file=tmp_path/'opir.json';p.save(file)
    assert Project.load(file).to_dict()==p.to_dict()

def test_link_cycle_rejected():
    p=opir_template()
    a,b=p.components[3:5]
    p.links=[dict(source_id=a.id,source_param='diameter',target_id=b.id,target_param='diameter',scale=1,offset=0),
             dict(source_id=b.id,source_param='diameter',target_id=a.id,target_param='diameter',scale=1,offset=0)]
    with pytest.raises(ValueError,match='Cyclic parameter'):p.validate()

def test_material_spectral_validation():
    p=opir_template()
    p.material_library={'Test':{'spectral_constants':[{'wavelength_um':3.0,'n':1.5,'k':0.01},
                                                  {'wavelength_um':5.0,'n':1.6,'k':0.02}]}}
    p.validate()
    p.material_library['Test']['spectral_constants'][1]['wavelength_um']=2
    with pytest.raises(ValueError,match='Wavelength'):p.validate()

def test_cad_package(tmp_path):
    pytest.importorskip('cadquery')
    from optical_studio.cad_exchange import export_cad_package
    p=opir_template()
    result=export_cad_package(p,tmp_path/'cad_export')
    assert len(result['cad_parts'])==8
    assert len(list((tmp_path/'cad_export').glob('*.step')))==9
    assert (tmp_path/'cad_export'/'Complete_Satellite_Assembly.step').is_file()
    for filename in ('Optical_Prescription.json','Material_Properties.json',
                     'Coating_Properties.json','Surface_Properties.json',
                     'Assembly_Transforms.json','Operating_Configurations.json',
                     'Thermo_Optical_Audit.json','Nominal_Project.json',
                     'Validation_Report.json','README_IMPORT.txt'):
        assert (tmp_path/'cad_export'/filename).is_file()
    validation=json.loads((tmp_path/'cad_export'/'Validation_Report.json').read_text())
    assert validation['cad_part_count']==8
    assert not validation['simulation_performed']
    assert not validation['surface_face_mapping_verified']
    assert (tmp_path/'cad_export'/'optical_prescription_draft.json').is_file()
    assert all(len(x['world_transform_row_major'])==4 for x in result['cad_parts'])

def test_complete_assembly_step_preserves_nonzero_world_placements(tmp_path):
    cq=pytest.importorskip('cadquery')
    from optical_studio.cad_exchange import export_cad_package
    p=Project(components=[component('detector','Sensor',(32.,-18.,90.),diameter=8.),
                          component('optical_bench','Bench',(5.,8.,-40.),width=80.,length=100.,thickness=10.)])
    folder=tmp_path/'package';export_cad_package(p,folder)
    assembly=cq.importers.importStep(str(folder/'Complete_Satellite_Assembly.step')).val()
    bb=assembly.BoundingBox()
    assert bb.xmin<5 and bb.xmax>30
    assert bb.zmin< -40 and bb.zmax>90
    manifest=json.loads((folder/'zemax_handoff.json').read_text())
    assert manifest['complete_assembly_step']=='Complete_Satellite_Assembly.step'
    assert len(manifest['cad_parts'])==2

def test_cad_audit_detects_modified_part_checksum(tmp_path):
    pytest.importorskip('cadquery')
    from optical_studio.cad_exchange import export_cad_package
    from optical_studio.zemax_plan import audit_package
    p=Project(components=[component('optical_bench','Frame')]);folder=tmp_path/'package'
    manifest=export_cad_package(p,folder)
    assert audit_package(folder)['cad_import_ready']
    step=folder/manifest['cad_parts'][0]['filename']
    step.write_bytes(step.read_bytes()+b'\nTEST MODIFICATION\n')
    review=audit_package(folder)
    assert not review['cad_import_ready']
    assert any('checksum' in message for message in review['blockers'])

def test_operating_cad_export_includes_unmodified_nominal_project(tmp_path):
    pytest.importorskip('cadquery')
    from optical_studio.cad_exchange import export_cad_package
    from optical_studio.operating_profiles import resolve_profile
    p=opir_template()
    resolved,_=resolve_profile(p,'Cryogenic detector concept')
    folder=tmp_path/'cold_export'
    manifest=export_cad_package(resolved,folder,nominal_project=p)
    original=json.loads((folder/'Nominal_Project.json').read_text())
    assert original==p.to_dict()
    assert manifest['source_project']['metadata']['active_operating_profile']=='Cryogenic detector concept'
    assert (folder/'Operating_Configurations.json').is_file()

def test_step_export_refuses_missing_component_without_partial_output(tmp_path):
    pytest.importorskip('cadquery')
    from optical_studio.cad_exchange import export_cad_package
    p=Project(components=[component('cad','Missing imported STEP')])
    dest=tmp_path/'incomplete'
    with pytest.raises((ValueError,FileNotFoundError)):
        export_cad_package(p,dest)
    assert not dest.exists()

def test_aperture_step_is_annular_not_opaque_disk():
    pytest.importorskip('cadquery')
    from optical_studio.cad_exchange import make_shape
    aperture=component('aperture','Entrance',diameter=100.)
    shape=make_shape(aperture)
    assert shape.isValid()
    # Centerline is an opening: the volume is far below a solid 100-mm disk.
    assert shape.Volume()<2000

def test_missing_step_warning():
    p=opir_template(); c=component('cad','Missing source');c.cad_file='/does/not/exist.step'
    p.components.append(c)
    assert any('missing STEP' in x for x in validate_project(p))

def test_structured_validation_component_links():
    from optical_studio.validation import validation_issues
    p=cassegrain_template()
    issues=validation_issues(p)
    primary=next(c for c in p.components if c.name=='Primary mirror')
    assert any(i['component_id']==primary.id and i['category']=='Material' for i in issues)
    assert all(i['severity'] in ('ERROR','WARNING','TOLERANCE') for i in issues)
    assert all(i['suggestion'] for i in issues)

def test_catalog_property_validation_and_name_only_warning():
    from optical_studio.catalog import candidate_record
    p=cassegrain_template()
    p.material_library['Glass']=candidate_record('Fused silica')
    assert any('name-only candidate' in s for s in validate_project(p))
    p.material_library['Glass']['density_kg_m3']=2200.0
    p.material_library['Glass']['thermal_conductivity_W_mK']=1.4
    p.validate()
    p.material_library['Glass']['density_kg_m3']=-2
    with pytest.raises(ValueError,match='density'):p.validate()

def test_coating_catalog_rejects_bad_emissivity():
    p=cassegrain_template()
    p.coating_library['Trial']={'source':'User test','emissivity':1.2,'spectral_rt':[]}
    with pytest.raises(ValueError,match='Emissivity'):p.validate()

def test_bundled_source_referenced_optical_data():
    from optical_studio.catalog import refractive_index,interpolate_nk
    p=cassegrain_template()
    assert {'Fused silica','Silicon','Zinc selenide','Calcium fluoride','Germanium'}<=set(p.material_library)
    assert refractive_index(p.material_library['Fused silica'],0.5876)==pytest.approx(1.45846,rel=2e-5)
    assert refractive_index(p.material_library['Silicon'],1.55)==pytest.approx(3.4777,rel=1e-4)
    assert refractive_index(p.material_library['Zinc selenide'],10.6)==pytest.approx(2.4,rel=0.03)
    assert refractive_index(p.material_library['Germanium'],4.258)==pytest.approx(4.0216)
    n,k=interpolate_nk(p.material_library['Germanium'],4.0)
    assert 4.02<n<4.04 and k is None
    assert all(r.get('source') and r.get('source_url') for r in p.material_library.values())
    for name,wl in [('Fused silica',4.0),('Silicon',12.0),('Germanium',14.0)]:
        with pytest.raises(ValueError,match='outside'):refractive_index(p.material_library[name],wl)


@pytest.mark.parametrize('kind',('vane_stack','spider','mirror_mount','optical_bench'))
def test_v08_optomechanical_components_generate_preview_and_step(kind,tmp_path):
    pytest.importorskip('cadquery')
    from optical_studio.geometry import preview_surfaces
    from optical_studio.cad_exchange import make_shape,export_cad_package
    c=component(kind,'Mechanical part')
    project=Project(components=[c]);project.validate()
    assert len(preview_surfaces(c))>=2
    shape=make_shape(c)
    assert shape.Volume()>0
    package=tmp_path/kind
    result=export_cad_package(project,package)
    assert len(result['cad_parts'])==1
    assert (package/f'{c.id}.step').is_file()


def test_v08_invalid_mechanical_params_rejected():
    p=Project(components=[component('spider','Support',arm_count=1)])
    with pytest.raises(ValueError,match='arm count'):p.validate()
    p=Project(components=[component('vane_stack','Vaned baffle',vane_count=3,spacing=1,
                                     vane_thickness=2)])
    with pytest.raises(ValueError,match='spacing'):p.validate()


def test_v08_detailed_cassegrain_template():
    from optical_studio.model import detailed_cassegrain_template
    p=detailed_cassegrain_template()
    assert len(p.components)==10
    assert {'spider','vane_stack','mirror_mount','optical_bench'} <= {c.kind for c in p.components}
    p.validate()


def test_v09_semantic_surface_ids_and_handoff(tmp_path):
    from optical_studio.surface_catalog import semantic_surfaces,semantic_key
    from optical_studio.export import export_manifest
    p=Project(components=[component('vane_stack','Five vanes',vane_count=5),
                          component('spider','Spider',arm_count=4)])
    vanes,spider=p.components
    assert len(semantic_surfaces(vanes))==20
    assert 'vane_001_front' in semantic_surfaces(vanes)
    assert 'arm_004_side' in semantic_surfaces(spider)
    vanes.surface_properties[semantic_key('vane_001_front')]={
        'interaction':'absorbing','scatter_model':'lambertian',
        'temperature_K':180.,'emissivity':0.93,'source':'User-supplied demonstrator'}
    target=tmp_path/'handoff.json';data=export_manifest(p,target)
    records=data['component_surface_assignments']
    matching=[r for r in records if r['component_id']==vanes.id and r['surface_id']=='vane_001_front']
    assert matching[0]['assignment']['temperature_K']==180
    assert matching[0]['is_explicitly_assigned']
    save=tmp_path/'project.json';p.save(save)
    assert Project.load(save).components[0].surface_properties==vanes.surface_properties


def test_v09_bad_surface_behavior_and_obsolete_geometry_detected():
    from optical_studio.surface_catalog import semantic_key,validate_surface_assignment
    p=Project(components=[component('vane_stack','Vaned shield',vane_count=3)])
    c=p.components[0]
    c.surface_properties[semantic_key('vane_003_front')]={
        'interaction':'absorbing','emissivity':0.8,'temperature_K':100}
    c.params['vane_count']=2
    assert any('obsolete semantic' in message for message in validate_project(p))
    with pytest.raises(ValueError,match='emissivity'):
        validate_surface_assignment({'interaction':'absorbing','emissivity':1.5})
    with pytest.raises(ValueError,match='BRDF'):
        validate_surface_assignment({'scatter_model':'brdf_file'})


def test_v09_cad_package_includes_surface_json(tmp_path):
    pytest.importorskip('cadquery')
    from optical_studio.cad_exchange import export_cad_package
    p=Project(components=[component('mirror_mount','Ring')])
    export_cad_package(p,tmp_path/'export')
    assert (tmp_path/'export'/'surface_assignments.json').is_file()


def test_v10_offline_zemax_package_audit_and_dry_run(tmp_path):
    pytest.importorskip('cadquery')
    from optical_studio.cad_exchange import export_cad_package
    from optical_studio.zemax_plan import audit_package
    from optical_studio.zemax_windows import build_model
    p=Project(components=[component('mirror_mount','Mount')])
    folder=tmp_path/'cad_package'
    export_cad_package(p,folder)
    report=audit_package(folder)
    assert report['status']=='cad_import_ready_optics_unmapped'
    assert len(report['nsc_objects'])==1
    assert len(report['nsc_objects'][0]['sha256'])==64
    assert report['optical_simulation_ready'] is False
    assert (folder/'zemax_cad_audit.json').is_file()
    result_template=json.loads((folder/'external_results_manifest_template.json').read_text())
    assert result_template['project_geometry_sha256']==__import__(
        'optical_studio.results_exchange',fromlist=['geometry_signature']).geometry_signature(p)
    direct=build_model(folder,tmp_path/'not_created.zos',dry_run=True,
                       report_file=tmp_path/'review.json')
    assert direct['cad_import_ready']
    assert not (tmp_path/'not_created.zos').exists()
    import subprocess,sys
    proc=subprocess.run([sys.executable,str(folder/'build_zemax.py'),str(folder),
                         str(tmp_path/'never_created.zos'),'--dry-run'],
                        check=True,capture_output=True,text=True)
    assert 'cad_import_ready_optics_unmapped' in proc.stdout


def test_v10_audit_rejects_unsafe_filename_and_unverified_rotation():
    from optical_studio.zemax_plan import audit_handoff
    matrix=[[0.,-1.,0.,0.],[1.,0.,0.,0.],[0.,0.,1.,0.],[0.,0.,0.,1.]]
    payload={'format':'optical-satellite-studio-zemax-handoff','geometry_units':'mm',
             'cad_parts':[{'component_id':'x','filename':'../outside.step',
                           'world_transform_row_major':matrix}]}
    result=audit_handoff(payload)
    assert not result['cad_import_ready']
    assert any('filename' in line for line in result['blockers'])
    payload['cad_parts'][0]['filename']='part.step'
    blocked=audit_handoff(payload)
    assert any('rotation' in line for line in blocked['blockers'])
    allowed=audit_handoff(payload,allow_rotations=True)
    assert allowed['cad_import_ready']


def test_v11_external_results_demo_and_geometry_invalidation(tmp_path):
    from optical_studio.results_exchange import load_results_package,geometry_signature
    from pathlib import Path
    p=cassegrain_template()
    folder=Path(__file__).resolve().parents[1]/'examples'/'external_results_example'
    data=load_results_package(folder,p)
    assert len(data['rays'])==2
    assert data['detector'].shape==(3,3)
    assert data['detector'][1,1]==pytest.approx(1e-6)
    assert any('unverified' in s for s in data['warnings'])
    signature=geometry_signature(p)
    p.components[0].position[2]=1.0
    assert geometry_signature(p)!=signature


def test_v11_external_results_matching_signature(tmp_path):
    from optical_studio.results_exchange import load_results_package,geometry_signature
    p=cassegrain_template()
    manifest={'format':'optical-satellite-studio-external-results','version':1,
              'coordinate_frame':'project_world_xyz_mm','rays_csv':'rays.csv',
              'project_geometry_sha256':geometry_signature(p)}
    (tmp_path/'results_manifest.json').write_text(json.dumps(manifest))
    (tmp_path/'rays.csv').write_text('ray_id,sequence,x_mm,y_mm,z_mm\nA,0,0,0,10\nA,1,0,0,0\n')
    assert len(load_results_package(tmp_path,p)['rays'])==1
    p.components[0].rotation[0]=1
    with pytest.raises(ValueError,match='fingerprint'):
        load_results_package(tmp_path,p)


def test_v11_external_results_rejects_malformed_files(tmp_path):
    from optical_studio.results_exchange import load_ray_csv,load_detector_csv,load_results_package
    p=cassegrain_template()
    (tmp_path/'rays.csv').write_text('ray_id,sequence,x_mm,y_mm,z_mm\nA,0,0,0,10\nA,0,0,0,2\n')
    with pytest.raises(ValueError,match='ascending'):load_ray_csv(tmp_path/'rays.csv')
    (tmp_path/'detector.csv').write_text('pixel_x,pixel_y,irradiance_W_mm2\n0,0,1\n1,1,2\n')
    with pytest.raises(ValueError,match='rectangular'):load_detector_csv(tmp_path/'detector.csv')
    bad={'format':'optical-satellite-studio-external-results','version':1,
         'coordinate_frame':'detector_local_mm','rays_csv':'rays.csv'}
    (tmp_path/'results_manifest.json').write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='project_world_xyz_mm'):
        load_results_package(tmp_path,p)

def test_legacy_project_material_data_is_preserved(tmp_path):
    from optical_studio.model import Project
    old={'name':'Legacy','units':'mm','schema_version':1,'components':[],
         'material_library':{'Silicon':{'category':'custom','spectral_constants':[
             {'wavelength_um':2.0,'n':3.5,'k':0.01}]}}}
    target=tmp_path/'old.json';target.write_text(json.dumps(old))
    loaded=Project.load(target)
    assert loaded.material_library['Silicon']['category']=='custom'
    assert 'Germanium' in loaded.material_library

def test_step_assembly_import_preserves_hierarchy(tmp_path):
    cq=pytest.importorskip('cadquery')
    from optical_studio.step_assembly import split_step_assembly
    assy=cq.Assembly(name='Bus')
    assy.add(cq.Workplane().box(4,3,2),name='Mount',loc=cq.Location(cq.Vector(12,0,8)))
    source=tmp_path/'assembly.step';assy.save(str(source))
    nodes=split_step_assembly(source,tmp_path/'parts')
    assert len(nodes)==2
    project=Project(components=nodes)
    project.validate()
    solid=next(c for c in nodes if c.kind=='cad')
    np.testing.assert_allclose(world_point(project,solid.id),[12,0,8],atol=1e-7)
    assert (tmp_path/'parts').exists()

def test_solid_interferences(tmp_path):
    cq=pytest.importorskip('cadquery')
    from optical_studio.cad_analysis import inspect_interferences,face_descriptors
    file=tmp_path/'box.step'
    cq.exporters.export(cq.Workplane().box(10,10,10).val(),str(file))
    a=component('cad','A');a.cad_file=str(file)
    b=component('cad','B',position=(5,0,0));b.cad_file=str(file)
    project=Project(components=[a,b])
    assert len(face_descriptors(a))==6
    results=inspect_interferences(project)
    assert len(results['collisions'])==1
    assert results['collisions'][0]['overlap_mm3']==pytest.approx(500,abs=1e-5)
    b.position=[20,0,0]
    assert not inspect_interferences(project)['collisions']

def test_alignment_tolerance(tmp_path):
    from optical_studio.assembly import world_matrix
    from optical_studio.cad_analysis import check_alignment_tolerances
    p=cassegrain_template();c=p.components[0]
    c.metadata['nominal_world_transform']=world_matrix(p,c.id).tolist()
    c.metadata['alignment_tolerance']={'position_mm':0.1,'orientation_deg':0.2}
    assert not check_alignment_tolerances(p)
    c.position[0]=0.5
    assert any('position error' in x for x in check_alignment_tolerances(p))

def test_face_signature_change_detected(tmp_path):
    cq=pytest.importorskip('cadquery')
    from optical_studio.cad_analysis import face_descriptors
    file=tmp_path/'face.step'
    cq.exporters.export(cq.Workplane().box(2,3,4).val(),str(file))
    c=component('cad','Face part');c.cad_file=str(file)
    face=face_descriptors(c)[0]
    c.surface_properties={'face_1':{'coating':'Test','face_signature':face['signature']}}
    p=Project(components=[c])
    assert not any('changed CAD face' in x for x in validate_project(p))
    c.surface_properties['face_1']['face_signature']='wrong'
    assert any('changed CAD face' in x for x in validate_project(p))

def test_zemax_builder_in_step_package(tmp_path):
    pytest.importorskip('cadquery')
    from optical_studio.cad_exchange import export_cad_package
    from optical_studio.zemax_windows import build_model
    p=cassegrain_template()
    package=tmp_path/'cad'
    export_cad_package(p,package)
    assert (package/'build_zemax.py').is_file()
    with pytest.raises(RuntimeError,match='Windows'):
        build_model(package,tmp_path/'new.zos')

def test_concentric_and_axial_placement_constraints():
    from optical_studio.constraints import apply_constraints
    p=Project()
    source=component('assembly','Optical bench',position=(10,20,30))
    destination=component('mirror','Secondary',position=(100,100,100))
    p.components=[source,destination]
    p.constraints=[{'type':'axial_gap','source_id':source.id,'target_id':destination.id,'gap_mm':140}]
    p.validate();apply_constraints(p)
    np.testing.assert_allclose(world_point(p,destination.id),[10,20,170])
    source.position[0]=25
    apply_constraints(p)
    np.testing.assert_allclose(world_point(p,destination.id),[25,20,170])

def test_overconstraint_rejected():
    p=cassegrain_template()
    a,b=p.components[:2]
    p.constraints=[{'type':'coincident','source_id':a.id,'target_id':b.id},
                   {'type':'coaxial','source_id':a.id,'target_id':b.id}]
    with pytest.raises(ValueError,match='Overconstraint'):p.validate()

def test_cyclic_geometric_constraint_rejected():
    p=cassegrain_template();a,b=p.components[:2]
    p.constraints=[{'type':'coincident','source_id':a.id,'target_id':b.id},
                   {'type':'coincident','source_id':b.id,'target_id':a.id}]
    with pytest.raises(ValueError,match='Cyclic'):p.validate()

def test_exact_clearance_between_cad_boxes(tmp_path):
    cq=pytest.importorskip('cadquery')
    from optical_studio.cad_analysis import measure_clearance,measure_axis_angle
    file=tmp_path/'box.step';cq.exporters.export(cq.Workplane().box(10,10,10).val(),str(file))
    a=component('cad','A');a.cad_file=str(file)
    b=component('cad','B',position=(17,0,0));b.cad_file=str(file)
    p=Project(components=[a,b]);p.validate()
    measured=measure_clearance(p,a.id,b.id)
    assert measured['distance_mm']==pytest.approx(7,abs=1e-6)
    assert measure_axis_angle(p,a.id,b.id)==pytest.approx(0)
    b.position=[5,0,0]
    overlapping=measure_clearance(p,a.id,b.id)
    assert overlapping['distance_mm']<1e-6
    assert overlapping['overlap_volume_mm3']==pytest.approx(500,abs=1e-4)

def test_relative_pose_constraint_and_serialization(tmp_path):
    from optical_studio.constraints import apply_constraints
    p=Project()
    a=component('assembly','Bench',position=(0,0,15))
    b=component('mirror','Mirror')
    p.components=[a,b]
    p.constraints=[dict(type='relative_pose',source_id=a.id,target_id=b.id,
                        offset_mm=[5,0,120],rotation_deg=[0,0,15])]
    p.validate();apply_constraints(p)
    np.testing.assert_allclose(world_point(p,b.id),[5,0,135])
    file=tmp_path/'constraints.json'
    p.save(file)
    loaded=Project.load(file)
    assert loaded.constraints==p.constraints
    a.position[0]=30;apply_constraints(p)
    np.testing.assert_allclose(world_point(p,b.id),[35,0,135])

def test_coaxial_constraint_preserves_axial_distance():
    from optical_studio.constraints import apply_constraints
    p=Project()
    a=component('assembly','Axis',position=(12,13,14))
    b=component('mirror','Secondary',position=(90,95,130))
    p.components=[a,b]
    original_distance=world_point(p,b.id)[2]-world_point(p,a.id)[2]
    p.constraints=[dict(type='coaxial',source_id=a.id,target_id=b.id)]
    apply_constraints(p)
    np.testing.assert_allclose(world_point(p,b.id),[12,13,14+original_distance])

def test_aspheric_mirror_coefficient_and_step_geometry():
    from optical_studio.optical_surfaces import component_sag
    c=component('mirror','Asphere')
    c.params['a4']=2e-8
    r=20
    base=component('mirror','Base')
    assert float(component_sag(c,r)-component_sag(base,r))==pytest.approx(2e-8*r**4)
    Project(components=[c]).validate()
    cq=pytest.importorskip('cadquery')
    from optical_studio.cad_exchange import make_shape
    assert make_shape(c).Volume()>0

def test_lens_front_back_prescription_and_validation():
    from optical_studio.optical_surfaces import component_sag
    c=component('lens','Infrared lens')
    c.params['front_a4']=1e-8
    p=Project(components=[c]);p.validate()
    assert component_sag(c,10,'front')!=component_sag(c,10,'back')
    cq=pytest.importorskip('cadquery')
    from optical_studio.cad_exchange import make_shape
    assert make_shape(c).Volume()>0
    c.params['center_thickness']=0.1
    with pytest.raises(ValueError):p.validate()

def test_csv_material_catalog_and_interpolation(tmp_path):
    from optical_studio.catalog import import_spectral_csv,interpolate_nk,candidate_record
    csv=tmp_path/'optics.csv'
    csv.write_text('wavelength_um,n,k\n3,4,0.01\n5,4.2,0.03\n',encoding='utf-8')
    record=import_spectral_csv(csv,'Test infrared material','laboratory data example')
    p=Project(material_library={'Test infrared material':record})
    p.validate()
    assert interpolate_nk(record,4)==pytest.approx((4.1,0.02))
    with pytest.raises(ValueError):interpolate_nk(record,6)
    p.material_library['Germanium']=candidate_record('Germanium')
    p.validate()  # Name-only catalog records remain clearly marked unverified.

def test_coating_spectral_energy_conservation():
    p=Project(coating_library={'Coating':{'spectral_rt':[
        {'wavelength_um':3.0,'reflectance':0.4,'transmittance':0.5}]}})
    p.validate()
    p.coating_library['Coating']['spectral_rt'][0]['transmittance']=0.7
    with pytest.raises(ValueError):p.validate()
