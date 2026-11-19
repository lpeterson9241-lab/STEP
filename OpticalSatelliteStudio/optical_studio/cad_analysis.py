"""B-rep inspection, stable-ish face descriptors, and mechanical checks.

Face indices are stable only for an unchanged imported part. Descriptors help
detect changed topology but cannot guarantee semantic identity after CAD edits.
"""
from __future__ import annotations
import hashlib
import numpy as np
from .assembly import world_matrix
from .cad_exchange import make_shape, require_cadquery


def located_shape(project, component):
    from OCP.gp import gp_Trsf
    cq = require_cadquery()
    shape = make_shape(component)
    if shape is None:
        return None
    m = world_matrix(project, component.id)
    t = gp_Trsf()
    t.SetValues(float(m[0,0]),float(m[0,1]),float(m[0,2]),float(m[0,3]),
                float(m[1,0]),float(m[1,1]),float(m[1,2]),float(m[1,3]),
                float(m[2,0]),float(m[2,1]),float(m[2,2]),float(m[2,3]))
    return shape.located(cq.Location(t))


def face_descriptors(component):
    shape = make_shape(component)
    if shape is None:
        return []
    result=[]
    for i,face in enumerate(shape.Faces(), start=1):
        center=face.Center()
        area=float(face.Area())
        key=f'{face.geomType()}:{area:.6f}:{center.x:.5f}:{center.y:.5f}:{center.z:.5f}'
        signature=hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]
        result.append(dict(index=i, signature=signature, surface_type=face.geomType(),
                           area_mm2=area, center_local_mm=[center.x,center.y,center.z]))
    return result


def _bbox_overlap(a,b):
    return (a.xmin <= b.xmax and b.xmin <= a.xmax and
            a.ymin <= b.ymax and b.ymin <= a.ymax and
            a.zmin <= b.zmax and b.zmin <= a.zmax)


def inspect_interferences(project, volume_tolerance_mm3=1e-6):
    """Pairwise volume overlap; contacts without penetrating volume are not collisions.

    Excludes abstract aperture/detector geometry, which are placeholders.
    Designed for modest assembly sizes; large satellites need broadphase caching.
    """
    solids=[]
    failures=[]
    for comp in project.components:
        if comp.kind in ('assembly','aperture','detector') or not comp.enabled:
            continue
        try:
            shape=located_shape(project,comp)
            if shape is not None:
                solids.append((comp,shape,shape.BoundingBox()))
        except Exception as exc:
            failures.append(f'{comp.name}: {exc}')
    collisions=[]
    for i,(a,sa,ba) in enumerate(solids):
        for b,sb,bb in solids[i+1:]:
            if not _bbox_overlap(ba,bb):continue
            common=sa.intersect(sb)
            volume=float(common.Volume())
            if volume > volume_tolerance_mm3:
                collisions.append(dict(first_id=a.id,first_name=a.name,
                                       second_id=b.id,second_name=b.name,
                                       overlap_mm3=volume))
    return dict(collisions=collisions, unavailable=failures, evaluated_solids=len(solids))


def measure_clearance(project, first_id, second_id, first_face=None, second_face=None):
    """OCCT closest-distance query between true B-reps or specified faces (mm)."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    lookup={c.id:c for c in project.components}
    shapes=[]
    for component_id,face_index in ((first_id,first_face),(second_id,second_face)):
        shape=located_shape(project,lookup[component_id])
        if shape is None:raise ValueError('An assembly group has no measurable solid')
        if face_index is not None:
            faces=shape.Faces()
            if not 1<=face_index<=len(faces):raise ValueError('Face index out of range')
            shape=faces[face_index-1]
        shapes.append(shape)
    query=BRepExtrema_DistShapeShape(shapes[0].wrapped,shapes[1].wrapped)
    query.Perform()
    if not query.IsDone() or query.NbSolution()==0:raise RuntimeError('No distance solution for CAD shapes')
    point_a=query.PointOnShape1(1)
    point_b=query.PointOnShape2(1)
    result=dict(distance_mm=float(query.Value()),
                first_point_world_mm=[point_a.X(),point_a.Y(),point_a.Z()],
                second_point_world_mm=[point_b.X(),point_b.Y(),point_b.Z()])
    if first_face is None and second_face is None and result['distance_mm']<1e-7:
        result['overlap_volume_mm3']=float(shapes[0].intersect(shapes[1]).Volume())
    return result


def measure_axis_angle(project,first_id,second_id):
    a=world_matrix(project,first_id)[:3,2]
    b=world_matrix(project,second_id)[:3,2]
    return float(np.degrees(np.arccos(np.clip(a@b,-1,1))))


def check_alignment_tolerances(project):
    """Compare component pose to a stored reference matrix if tolerance metadata is supplied."""
    issues=[]
    for comp in project.components:
        reference=comp.metadata.get('nominal_world_transform')
        limits=comp.metadata.get('alignment_tolerance',{})
        if reference is None or not limits:continue
        reference=np.asarray(reference,dtype=float)
        if reference.shape != (4,4):
            issues.append(f'{comp.name}: nominal_world_transform must be 4x4')
            continue
        actual=world_matrix(project,comp.id)
        error_mm=float(np.linalg.norm(actual[:3,3]-reference[:3,3]))
        cosine=float(np.clip((np.trace(reference[:3,:3].T @ actual[:3,:3])-1)/2,-1,1))
        error_deg=float(np.degrees(np.arccos(cosine)))
        if error_mm > float(limits.get('position_mm',float('inf'))):
            issues.append(f'{comp.name}: position error {error_mm:.6g} mm exceeds tolerance')
        if error_deg > float(limits.get('orientation_deg',float('inf'))):
            issues.append(f'{comp.name}: orientation error {error_deg:.6g} deg exceeds tolerance')
    return issues
