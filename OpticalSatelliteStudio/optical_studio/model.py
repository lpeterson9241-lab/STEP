"""Independent canonical optical scene and simple Cassegrain template (units: mm)."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
import math
import uuid
from .reference_materials import reference_materials

SCHEMA_VERSION = 1

def uid() -> str:
    return uuid.uuid4().hex[:12]

@dataclass
class Component:
    id: str
    name: str
    kind: str
    enabled: bool = True
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    params: dict = field(default_factory=dict)
    material: str = "Aluminum"
    coating: str = "Unspecified"
    cad_file: str = ""
    parent_id: str | None = None
    surface_properties: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

@dataclass
class Project:
    name: str = "Untitled Cassegrain"
    units: str = "mm"
    schema_version: int = SCHEMA_VERSION
    components: list[Component] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    constraints: list[dict] = field(default_factory=list)
    material_library: dict = field(default_factory=reference_materials)
    coating_library: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=lambda: {"purpose": "optomechanical authoring; no optical performance verified"})

    def validate(self):
        if self.units != "mm":
            raise ValueError("Only millimeter projects are currently supported")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported schema version {self.schema_version}")
        ids = [c.id for c in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate component IDs")
        for c in self.components:
            if c.kind not in ("assembly", "mirror", "lens", "baffle", "detector", "aperture", "housing", "filter", "cad", "vane_stack", "spider", "mirror_mount", "optical_bench"):
                raise ValueError(f"Unknown component type: {c.kind}")
            if c.parent_id is not None and (c.parent_id not in ids or c.parent_id == c.id):
                raise ValueError(f"Invalid parent for {c.name}")
            for seq in (c.position, c.rotation):
                if len(seq) != 3 or not all(isinstance(x, (float, int)) and math.isfinite(x) for x in seq):
                    raise ValueError(f"Invalid transform for {c.name}")
            for key, value in c.params.items():
                if isinstance(value, (float, int)) and not isinstance(value, bool) and not math.isfinite(value):
                    raise ValueError(f"Non-finite {key} for {c.name}")
            if c.kind == "mirror":
                if c.params.get("diameter", 0) <= 0 or c.params.get("radius", 0) == 0:
                    raise ValueError(f"Mirror requires positive diameter and nonzero signed radius: {c.name}")
                if c.params.get("hole_diameter", 0) < 0 or c.params.get("hole_diameter", 0) >= c.params["diameter"]:
                    raise ValueError(f"Invalid mirror central hole: {c.name}")
            if c.kind in ('mirror','lens'):
                from .optical_surfaces import validate_prescription
                try:validate_prescription(c)
                except ValueError as exc:raise ValueError(f'{c.name}: {exc}') from exc
            if c.kind in ("baffle", "housing"):
                if not (0 <= c.params.get("inner_diameter", -1) < c.params.get("outer_diameter", 0)):
                    raise ValueError(f"Invalid tube diameters: {c.name}")
                if c.params.get("length", 0) <= 0:
                    raise ValueError(f"Invalid tube length: {c.name}")
            if c.kind in ('vane_stack','mirror_mount'):
                if not (0 < c.params.get('inner_diameter',0) < c.params.get('outer_diameter',0)):
                    raise ValueError(f'Invalid annular diameters: {c.name}')
                thickness=c.params.get('vane_thickness',0) if c.kind=='vane_stack' else c.params.get('length',0)
                if thickness<=0:raise ValueError(f'Positive thickness/length required: {c.name}')
                if c.kind=='vane_stack':
                    count=c.params.get('vane_count',0)
                    if isinstance(count,bool) or not isinstance(count,int) or not 1<=count<=100:
                        raise ValueError(f'Vane count must be an integer from 1 to 100: {c.name}')
                    if c.params.get('spacing',0)<thickness:
                        raise ValueError(f'Vane spacing must equal/exceed thickness: {c.name}')
            if c.kind=='spider':
                if not 0<c.params.get('hub_diameter',0)<c.params.get('outer_diameter',0):
                    raise ValueError(f'Invalid spider hub/outer diameters: {c.name}')
                count=c.params.get('arm_count',0)
                if isinstance(count,bool) or not isinstance(count,int) or not 2<=count<=12:
                    raise ValueError(f'Spider arm count must be an integer from 2 to 12: {c.name}')
                if c.params.get('arm_width',0)<=0 or c.params.get('thickness',0)<=0:
                    raise ValueError(f'Spider arm width and thickness must be positive: {c.name}')
            if c.kind=='optical_bench':
                if any(c.params.get(key,0)<=0 for key in ('width','length','thickness')):
                    raise ValueError(f'Optical bench dimensions must be positive: {c.name}')
            if c.kind in ("detector", "aperture", "filter", "lens"):
                if c.params.get("diameter", 0) <= 0:
                    raise ValueError(f"Invalid diameter: {c.name}")
            if not isinstance(c.surface_properties, dict):
                raise ValueError(f"Invalid surface mapping for {c.name}")
        lookup = {c.id: c for c in self.components}
        for c in self.components:
            visited = {c.id}
            parent = c.parent_id
            while parent is not None:
                if parent in visited:
                    raise ValueError(f"Cyclic assembly hierarchy at {c.name}")
                visited.add(parent)
                parent = lookup[parent].parent_id
        self._link_order()
        from .constraints import order_constraints
        order_constraints(self)
        from .operating_profiles import validate_profiles
        validate_profiles(self)
        if 'optical_sequence' in self.metadata:
            from .sequential import validate_sequence
            validate_sequence(self,self.metadata['optical_sequence'])
        from .catalog import validate_physical_record
        for name, record in self.material_library.items():
            if not isinstance(name,str) or not isinstance(record,dict):
                raise ValueError('Material library entries must be named objects')
            validate_physical_record(record,'material')
            samples=record.get('spectral_constants')
            if samples is not None:
                if not isinstance(samples,list):
                    raise ValueError(f'Invalid spectral constants for {name}')
                if not samples:
                    continue
                wavelengths=[]
                for sample in samples:
                    try:
                        wl=float(sample['wavelength_um']); n=float(sample['n']); k=float(sample['k'])
                    except (KeyError,TypeError,ValueError) as exc:
                        raise ValueError(f'Invalid optical constants for {name}') from exc
                    if not all(math.isfinite(v) for v in (wl,n,k)) or wl<=0 or k<0 or n<=0:
                        raise ValueError(f'Invalid wavelength/n/k values for {name}')
                    wavelengths.append(wl)
                if any(a>=b for a,b in zip(wavelengths,wavelengths[1:])):
                    raise ValueError(f'Wavelength samples must increase for {name}')
        for name,record in self.coating_library.items():
            if not isinstance(name,str) or not isinstance(record,dict):
                raise ValueError('Coating catalog entries must be named objects')
            validate_physical_record(record,'coating')
            samples=record.get('spectral_rt',[])
            previous=0
            for sample in samples:
                wavelength=float(sample['wavelength_um'])
                r,t=float(sample['reflectance']),float(sample['transmittance'])
                if not all(math.isfinite(x) for x in (wavelength,r,t)) or wavelength<=previous or not 0<=r<=1 or not 0<=t<=1 or r+t>1+1e-8:
                    raise ValueError(f'Invalid energy or wavelength data in coating {name}')
                previous=wavelength

    def _link_order(self):
        lookup = {c.id: c for c in self.components}
        targets = {}
        for link in self.links:
            source=(link['source_id'],link['source_param'])
            target=(link['target_id'],link['target_param'])
            if source[0] not in lookup or target[0] not in lookup:
                raise ValueError('Parameter link references an unknown component')
            if source[1] not in lookup[source[0]].params or target[1] not in lookup[target[0]].params:
                raise ValueError('Parameter link references an unknown parameter')
            if target in targets: raise ValueError('More than one link controls the same parameter')
            targets[target] = source
            for k in ('scale','offset'):
                if not isinstance(link.get(k),(int,float)) or not math.isfinite(link[k]):
                    raise ValueError(f'Invalid link {k}')
        ordered=[]
        pending=list(self.links)
        while pending:
            ready=[]
            for link in pending:
                source=(link['source_id'],link['source_param'])
                if source not in {(p['target_id'],p['target_param']) for p in pending}:
                    ready.append(link)
            if not ready: raise ValueError('Cyclic parameter dependencies')
            ordered.extend(ready)
            pending=[p for p in pending if p not in ready]
        return ordered

    def apply_links(self):
        lookup={c.id:c for c in self.components}
        for link in self._link_order():
            value=lookup[link['source_id']].params[link['source_param']]
            lookup[link['target_id']].params[link['target_param']] = (
                value*link['scale']+link['offset'])

    def to_dict(self):
        self.validate()
        return asdict(self)

    def save(self, path):
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        materials=reference_materials()
        materials.update(data.get('material_library',{}))
        obj = cls(name=data.get("name", "Untitled"), units=data.get("units", "mm"),
                  schema_version=data.get("schema_version", 0),
                  components=[Component.from_dict(x) for x in data.get("components", [])],
                  links=data.get('links',[]),
                  constraints=data.get('constraints',[]),
                  material_library=materials,
                  coating_library=data.get('coating_library',{}),
                  metadata=data.get("metadata", {}))
        obj.validate()
        return obj


def component(kind: str, name: str, position=(0, 0, 0), **kwargs) -> Component:
    specs = {
        "mirror": (dict(diameter=150.0, radius=-600.0, conic=-1.0, hole_diameter=0.0,
                        substrate_thickness=5.0, **{f'a{n}':0.0 for n in (4,6,8,10,12)}),
                   "Glass", "Aluminum reflective"),
        "lens": (dict(diameter=60.0,center_thickness=8.0,front_radius=120.0,back_radius=-120.0,
                      front_conic=0.0,back_conic=0.0,
                      **{f'{side}_a{n}':0.0 for side in ('front','back') for n in (4,6,8,10,12)}),
                 "Germanium (unverified)","Unspecified"),
        "baffle": (dict(outer_diameter=80.0, inner_diameter=70.0, length=200.0), "Aluminum", "Black absorbing (placeholder)"),
        "housing": (dict(outer_diameter=210.0, inner_diameter=202.0, length=380.0), "Aluminum", "Black absorbing (placeholder)"),
        "detector": (dict(diameter=20.0), "Silicon", "Unspecified"),
        "aperture": (dict(diameter=50.0), "Unspecified", "Unspecified"),
        "cad": ({}, "Unspecified", "Unspecified"),
        "assembly": ({}, "Unspecified", "Unspecified"),
        "filter": (dict(diameter=30.0, thickness=3.0), "Germanium", "IR bandpass (placeholder)"),
        "vane_stack": (dict(inner_diameter=65.0,outer_diameter=130.0,vane_count=5,
                            vane_thickness=2.0,spacing=30.0),"Aluminum","Black absorbing (placeholder)"),
        "spider": (dict(hub_diameter=42.0,outer_diameter=210.0,arm_count=4,
                        arm_width=4.0,thickness=3.0),"Invar","Black absorbing (placeholder)"),
        "mirror_mount": (dict(inner_diameter=180.0,outer_diameter=224.0,length=12.0),
                         "Aluminum","Black absorbing (placeholder)"),
        "optical_bench": (dict(width=280.0,length=360.0,thickness=12.0),
                          "Aluminum","Black absorbing (placeholder)"),
    }
    params, material, coating = specs[kind]
    params.update(kwargs)
    return Component(id=uid(), name=name, kind=kind, position=list(map(float, position)),
                     params=params, material=material, coating=coating)


def cassegrain_template() -> Project:
    """Illustrative coaxial layout, NOT a solved or optimized telescope prescription.

    Coordinates: primary vertex z=0, secondary z=+260; rays approach from +z.
    Signed curvature conventions need translation/verification in target Zemax mode.
    """
    p = Project(name="Cassegrain concept — illustrative")
    p.components = [
        component("mirror", "Primary mirror", (0, 0, 0), diameter=200, radius=-650, conic=-1.0, hole_diameter=40),
        component("mirror", "Secondary mirror", (0, 0, 260), diameter=68, radius=210, conic=-2.0, hole_diameter=0),
        component("baffle", "Central baffle", (0, 0, 42), outer_diameter=48, inner_diameter=42, length=180),
        component("housing", "Outer telescope tube", (0, 0, 190), outer_diameter=224, inner_diameter=216, length=390),
        component("detector", "Image-plane placeholder", (0, 0, -110), diameter=22),
        component("aperture", "Entrance aperture", (0, 0, 390), diameter=200),
    ]
    p.metadata["warning"] = "Conceptual geometry only; focal plane and conic prescription not optimized."
    p.validate()
    return p


def detailed_cassegrain_template() -> Project:
    """Demonstration mechanical build-out; NOT optically or structurally qualified."""
    p=cassegrain_template()
    p.name='Cassegrain optomechanical concept — illustrative'
    p.components.extend([
        component('vane_stack','Internal stray-light vane stack',(0,0,152),
                  inner_diameter=176,outer_diameter=214,vane_count=5,
                  vane_thickness=1.5,spacing=34),
        component('spider','Secondary mirror spider',(0,0,260),
                  hub_diameter=68,outer_diameter=216,arm_count=4,
                  arm_width=3,thickness=2),
        component('mirror_mount','Primary mirror retaining ring',(0,0,-7),
                  inner_diameter=198,outer_diameter=224,length=12),
        component('optical_bench','Instrument mounting bench',(0,0,-165),
                  width=260,length=280,thickness=14),
    ])
    p.metadata['warning']='Concept geometry only. Vanes, mounts, obscuration and mechanical collisions require sizing and validation.'
    p.validate()
    return p


def opir_template() -> Project:
    """Generic illustrative IR payload assembly; not a mission design or solved optical system."""
    p=Project(name='OPIR infrared payload — conceptual')
    bus=component('assembly','Spacecraft bus')
    payload=component('assembly','Infrared optical payload',(0,0,450))
    payload.parent_id=bus.id
    bench=component('assembly','Optical bench')
    bench.parent_id=payload.id
    primary=component('mirror','Primary IR mirror',(0,0,0),diameter=300,radius=-1000,conic=-1,hole_diameter=60)
    secondary=component('mirror','Secondary IR mirror',(0,0,390),diameter=95,radius=310,conic=-2,hole_diameter=0)
    tube=component('housing','Optical enclosure',(0,0,270),outer_diameter=332,inner_diameter=320,length=550)
    baffle=component('baffle','Primary baffle',(0,0,95),outer_diameter=70,inner_diameter=63,length=215)
    shield=component('housing','Cold shield',(0,0,-130),outer_diameter=52,inner_diameter=42,length=90)
    detector=component('detector','Infrared focal plane',(0,0,-175),diameter=27.3,thickness=2)
    detector.params.update(pixel_pitch_um=15.0,pixels_x=1280,pixels_y=1024)
    detector.metadata.update(temperature_K=80.0, spectral_band_um=[3.0,5.0], detector_response='user_data_required')
    entrance=component('aperture','Entrance aperture',(0,0,550),diameter=300)
    chassis=component('housing','Spacecraft bus envelope',(0,0,-250),outer_diameter=550,inner_diameter=535,length=420)
    for c in [primary,secondary,tube,baffle,shield,detector,entrance]:c.parent_id=bench.id
    chassis.parent_id=bus.id
    tube.material='Aluminum 6061 (placeholder)'
    tube.coating='Low reflectance IR coating (data required)'
    shield.coating='Cold shield IR black (BRDF required)'
    p.components=[bus,payload,bench,primary,secondary,tube,baffle,shield,detector,entrance,chassis]
    p.links=[
        dict(source_id=primary.id,source_param='diameter',target_id=tube.id,target_param='inner_diameter',scale=1,offset=20),
        dict(source_id=primary.id,source_param='diameter',target_id=tube.id,target_param='outer_diameter',scale=1,offset=32),
        dict(source_id=primary.id,source_param='diameter',target_id=entrance.id,target_param='diameter',scale=1,offset=0),
    ]
    p.constraints=[
        dict(type='axial_gap',source_id=primary.id,target_id=secondary.id,gap_mm=390),
        dict(type='axial_gap',source_id=shield.id,target_id=detector.id,gap_mm=-45),
    ]
    p.metadata['operating_profiles']={
        'Cryogenic detector concept':{
            'reference_temperature_K':293.15,'default_temperature_K':293.15,
            'component_overrides':{
                detector.id:{'temperature_K':80.0},
                shield.id:{'temperature_K':80.0},
            },
        },
    }
    p.metadata['note']='Illustrative geometry: focal lengths, spectral response, thermal background and coatings need engineering verification.'
    p.apply_links()
    from .constraints import apply_constraints
    apply_constraints(p)
    p.validate()
    return p
