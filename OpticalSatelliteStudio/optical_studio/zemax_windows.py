"""Windows-only ZOS-API CAD assembly builder (requires installed OpticStudio).

Based on the documented ZOS-API standalone connection and NSC CAD object API.
This does not create sources, detectors or a validated optical prescription.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
try:
    from .zemax_plan import audit_package
except ImportError:
    from zemax_plan import audit_package


def initialize_zosapi():
    if os.name != 'nt':
        raise RuntimeError('ZOS-API standalone model generation requires Windows and OpticStudio')
    import clr
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Zemax') as key:
        zemax_root=winreg.QueryValueEx(key,'ZemaxRoot')[0]
    net_helper=Path(zemax_root)/'ZOS-API'/'Libraries'/'ZOSAPI_NetHelper.dll'
    clr.AddReference(str(net_helper))
    import ZOSAPI_NetHelper
    if not ZOSAPI_NetHelper.ZOSAPI_Initializer.Initialize():
        raise RuntimeError('Unable to initialize OpticStudio ZOS-API')
    install_dir=Path(ZOSAPI_NetHelper.ZOSAPI_Initializer.GetZemaxDirectory())
    clr.AddReference(str(install_dir/'ZOSAPI.dll'))
    clr.AddReference(str(install_dir/'ZOSAPI_Interfaces.dll'))
    import ZOSAPI
    connection=ZOSAPI.ZOSAPI_Connection()
    app=connection.CreateNewApplication()
    if app is None or not app.IsValidLicenseForAPI:
        raise RuntimeError('Unable to create licensed ZOS-API application')
    return ZOSAPI,app


def _unique_cad_file(source, destination):
    digest=hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    target=destination/f'OSS_{digest}.step'
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=hashlib.sha256(source.read_bytes()).hexdigest():
        raise FileExistsError(f'Existing Zemax CAD file collision: {target}')
    if not target.exists():shutil.copy2(source,target)
    return target.name


def build_model(package_path,output_file,allow_rotations=False,dry_run=False,report_file=None):
    package=Path(package_path).resolve()
    output=Path(output_file).resolve()
    report=audit_package(package,allow_rotations)
    if report_file:
        target=Path(report_file)
        if target.exists():raise FileExistsError(f'Will not overwrite existing audit: {target}')
        target.write_text(json.dumps(report,indent=2),encoding='utf-8')
    if dry_run:
        print(json.dumps({'status':report['status'],'objects':len(report['nsc_objects']),
                          'blockers':report['blockers'],'warnings':report['warnings']},indent=2))
        return report
    if report['blockers']:
        raise ValueError('CAD handoff blocked:\n'+'\n'.join(report['blockers']))
    if output.exists():raise FileExistsError(f'Will not overwrite existing Zemax model: {output}')
    manifest=json.loads((package/'zemax_handoff.json').read_text(encoding='utf-8'))
    parts=manifest.get('cad_parts',[])
    if not parts:raise ValueError('Manifest has no CAD parts')
    for part in parts:
        matrix=part['world_transform_row_major']
        if not allow_rotations:
            # Non-zero rotations require confirmation of axis and tilt order in
            # the actual installed Zemax version before using this adapter.
            if any(abs(matrix[i][j]-(1 if i==j else 0))>1e-8 for i in range(3) for j in range(3)):
                raise ValueError('Rotated objects require --allow-rotations and installed-version verification')
        if not (package/part['filename']).is_file():
            raise FileNotFoundError(package/part['filename'])
    ZOSAPI,app=initialize_zosapi()
    try:
        system=app.CreateNewSystem(ZOSAPI.SystemType.NonSequential)
        dest=Path(app.ObjectsDir)/'CAD Files'
        if not dest.is_dir():raise FileNotFoundError(f'OpticStudio CAD Files folder: {dest}')
        editor=system.NCE
        from math import asin,atan2,degrees,cos
        for i,part in enumerate(parts,1):
            row=editor.GetObjectAt(1) if i==1 else editor.InsertNewObjectAt(i)
            settings=row.GetObjectTypeSettings(ZOSAPI.Editors.NCE.ObjectType.CADPartSTEPIGESSAT)
            settings.FileName1=_unique_cad_file(package/part['filename'],dest)
            row.ChangeType(settings)
            # Stable component identifier for cross-application manual mapping.
            try:
                row.Comment='OSS:'+str(part['component_id'])
            except AttributeError:
                print('WARNING: Installed NSCE row does not expose Comment; use the audit object-index map.')
            matrix=part['world_transform_row_major']
            row.XPosition=float(matrix[0][3]);row.YPosition=float(matrix[1][3]);row.ZPosition=float(matrix[2][3])
            if allow_rotations:
                ry=asin(max(-1.0,min(1.0,-matrix[2][0])))
                rx=atan2(matrix[2][1],matrix[2][2]) if abs(cos(ry))>1e-8 else 0
                rz=atan2(matrix[1][0],matrix[0][0]) if abs(cos(ry))>1e-8 else atan2(-matrix[0][1],matrix[1][1])
                row.TiltAboutX=float(degrees(rx));row.TiltAboutY=float(degrees(ry));row.TiltAboutZ=float(degrees(rz))
        system.SaveAs(str(output))
        print(f'Created {output} with {len(parts)} imported CAD objects')
        print('IMPORTANT: Verify all coordinate frames, optical prescriptions, materials and CAD faces in OpticStudio.')
        return output
    finally:
        app.CloseApplication()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Build a non-sequential CAD model in installed Windows OpticStudio')
    parser.add_argument('package',help='Directory containing zemax_handoff.json and STEP parts')
    parser.add_argument('output',help='New .zos output path (must not already exist)')
    parser.add_argument('--allow-rotations',action='store_true',help='Unverified NSCE tilt conversion; inspect output carefully')
    parser.add_argument('--dry-run',action='store_true',help='Offline manifest/STEP audit; no Windows, license or OpticStudio required')
    parser.add_argument('--report',help='Write a new structured audit JSON; refuses to overwrite')
    args=parser.parse_args()
    build_model(args.package,args.output,args.allow_rotations,args.dry_run,args.report)
