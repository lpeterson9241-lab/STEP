"""Export a solver-neutral interchange manifest to later consume with ZOS-API.
No unsupported promise of producing a working .ZMX/.ZOS file without OpticStudio.
"""
from pathlib import Path
import json
from .model import Project
from .assembly import world_matrix
from .validation import validate_project
from .sequential import build_sequential_prescription
from .surface_catalog import export_surface_assignments
from .results_exchange import geometry_signature
from .thermo_optics import thermal_report


def export_manifest(project: Project, path: str):
    scene = project.to_dict()
    manifest = {
        "format": "optical-satellite-studio-zemax-handoff",
        "version": 1,
        "source_project": scene,
        "source_project_geometry_sha256":geometry_signature(project),
        "components_world_transforms": {
            c.id: world_matrix(project,c.id).tolist() for c in project.components
        },
        "preflight_issues": validate_project(project),
        "draft_sequential_prescription": build_sequential_prescription(project),
        "component_surface_assignments":export_surface_assignments(project),
        "thermo_optical_audit":thermal_report(project),
        "target_mode": "non_sequential",
        "geometry_units": "mm",
        "transform_convention": "local XYZ in mm; Euler rotation angles XYZ in degrees (must be explicitly converted by adapter)",
        "status": "preflight_only_not_an_opticstudio_file",
        "limitations": [
            "Surface prescriptions, coordinate transforms and face assignments require installed-version ZOS-API mapping.",
            "Semantic parametric surface IDs are design annotations; they are not verified OCCT face IDs or Zemax scatter-object faces.",
            "Material and coating labels are metadata; not validated Zemax catalogs or scatter models.",
            "Paraboloid and conic display meshes are geometric previews, not verified ray-trace surfaces.",
            "Draft sequential surface data is supplied but cannot be imported as a validated LDE without explicit coordinate and material mappings.",
            "Thermo-optical radiance values describe local emission only and are not detector backgrounds or stray-light results.",
        ],
    }
    Path(path).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
