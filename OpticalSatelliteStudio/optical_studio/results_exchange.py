"""Versioned, solver-neutral *external* ray and detector exchange.

This is not a native Zemax ZRD or detector file parser. Results must first be
converted to the explicitly documented CSV files in the accompanying manifest.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np

MAX_SAMPLES=200000
MAX_DETECTOR_PIXELS=2048*2048


def geometry_signature(project):
    """A model fingerprint covering geometry, optical data, and surface behaviors."""
    rows=[dict(id=c.id,kind=c.kind,enabled=c.enabled,parent_id=c.parent_id,
               position=c.position,rotation=c.rotation,params=c.params,
               material=c.material,coating=c.coating,surface_properties=c.surface_properties)
          for c in sorted(project.components,key=lambda c:c.id)]
    scope={'components':rows,'material_library':project.material_library,
           'coating_library':project.coating_library,
           'optical_sequence':project.metadata.get('optical_sequence')}
    encoded=json.dumps(scope,sort_keys=True,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _file_in_package(folder,filename):
    if not isinstance(filename,str) or not filename or Path(filename).name!=filename or '\\' in filename or ':' in filename:
        raise ValueError('Result CSV filename must be a simple relative filename')
    path=folder/filename
    if not path.is_file():raise FileNotFoundError(path)
    return path


def load_ray_csv(path):
    tracks={}
    count=0
    with Path(path).open(newline='',encoding='utf-8-sig') as file:
        reader=csv.DictReader(file)
        needed={'ray_id','sequence','x_mm','y_mm','z_mm'}
        if not needed <= set(reader.fieldnames or []):
            raise ValueError('Ray CSV needs ray_id,sequence,x_mm,y_mm,z_mm columns')
        for row in reader:
            count+=1
            if count>MAX_SAMPLES:raise ValueError('Ray CSV sample limit exceeded')
            identifier=row['ray_id'].strip()
            if not identifier:raise ValueError('Ray ID cannot be empty')
            try:
                sequence=int(row['sequence'])
                xyz=[float(row[key]) for key in ('x_mm','y_mm','z_mm')]
            except (ValueError,TypeError) as exc:raise ValueError('Invalid ray sequence or XYZ') from exc
            if sequence<0 or not all(math.isfinite(v) for v in xyz):
                raise ValueError('Ray sequence must be nonnegative and XYZ finite')
            items=tracks.setdefault(identifier,[])
            if items and sequence<=items[-1][0]:
                raise ValueError('Each ray sequence must be strictly ascending')
            items.append((sequence,xyz))
    if not tracks:raise ValueError('Ray CSV is empty')
    return {identifier:np.asarray([xyz for _,xyz in entries],dtype=float)
            for identifier,entries in tracks.items() if len(entries)>=2}


def load_detector_csv(path):
    entries={};highest_x=highest_y=-1
    with Path(path).open(newline='',encoding='utf-8-sig') as file:
        reader=csv.DictReader(file)
        if not {'pixel_x','pixel_y','irradiance_W_mm2'} <= set(reader.fieldnames or []):
            raise ValueError('Detector CSV needs pixel_x,pixel_y,irradiance_W_mm2 columns')
        for row in reader:
            try:
                x=int(row['pixel_x']);y=int(row['pixel_y']);value=float(row['irradiance_W_mm2'])
            except (ValueError,TypeError) as exc:raise ValueError('Invalid detector sample') from exc
            if x<0 or y<0 or not math.isfinite(value) or value<0:
                raise ValueError('Detector coordinates must be nonnegative and irradiance finite/nonnegative')
            if (x,y) in entries:raise ValueError('Duplicate detector pixel')
            entries[(x,y)]=value
            highest_x=max(highest_x,x);highest_y=max(highest_y,y)
            if len(entries)>MAX_DETECTOR_PIXELS or (highest_x+1)*(highest_y+1)>MAX_DETECTOR_PIXELS:
                raise ValueError('Detector pixel limit exceeded')
    if not entries:raise ValueError('Detector CSV is empty')
    width,height=highest_x+1,highest_y+1
    if len(entries)!=width*height:raise ValueError('Detector map must cover a complete rectangular grid')
    data=np.zeros((height,width),dtype=float)
    for (x,y),value in entries.items():data[y,x]=value
    return data


def load_results_package(folder,project):
    folder=Path(folder)
    manifest=json.loads((folder/'results_manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format')!='optical-satellite-studio-external-results' or manifest.get('version')!=1:
        raise ValueError('Unsupported external-results manifest')
    if manifest.get('coordinate_frame')!='project_world_xyz_mm':
        raise ValueError('Ray coordinates must explicitly use project_world_xyz_mm')
    results={'manifest':manifest,'rays':{},'detector':None,'warnings':[]}
    expected=manifest.get('project_geometry_sha256')
    actual=geometry_signature(project)
    if expected and expected!=actual:
        raise ValueError('Geometry fingerprint does not match the current telescope assembly')
    if not expected:results['warnings'].append('No project fingerprint supplied; ray/world alignment is unverified')
    if manifest.get('rays_csv'):
        results['rays']=load_ray_csv(_file_in_package(folder,manifest['rays_csv']))
    if manifest.get('detector_csv'):
        results['detector']=load_detector_csv(_file_in_package(folder,manifest['detector_csv']))
        target=manifest.get('detector_component_id')
        if target and not any(c.id==target and c.kind=='detector' for c in project.components):
            raise ValueError('Detector component ID does not match the project')
    if not results['rays'] and results['detector'] is None:
        raise ValueError('No usable ray paths or detector data')
    return results
