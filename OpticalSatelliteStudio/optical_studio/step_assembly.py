"""Import STEP assembly hierarchy via CadQuery's XCAF-backed Assembly loader."""
from pathlib import Path
import re
from .cad_exchange import require_cadquery
from .model import component


def split_step_assembly(source, destination):
    """Extract assembly nodes as per-part STEP files, preserving local placements.

    Returns a flat list of Component objects with parent_id links. Output is
    written only to a new/empty directory. Raw source topology and vendor IDs
    may not survive export; face mapping must be revalidated for new CAD files.
    """
    cq=require_cadquery()
    source=Path(source)
    if not source.is_file():raise FileNotFoundError(source)
    folder=Path(destination)
    if folder.exists() and any(folder.iterdir()):
        raise FileExistsError(f'Import destination must be empty: {folder}')
    assembly=cq.Assembly.load(str(source))
    folder.mkdir(parents=True,exist_ok=True)
    imported=[]

    def walk(node,parent_id,path):
        item=component('assembly',node.name or path)
        item.parent_id=parent_id
        xyz,angles=node.loc.toTuple()
        item.position=list(map(float,xyz))
        item.rotation=list(map(float,angles))
        item.metadata['source_step']=str(source)
        item.metadata['assembly_path']=path
        if node.obj is not None:
            shape=node.obj.val() if isinstance(node.obj,cq.Workplane) else node.obj
            safe=re.sub(r'[^A-Za-z0-9_-]+','_',node.name or 'part')[:32]
            filename=f'{len(imported):04d}_{safe}.step'
            cq.exporters.export(shape,str(folder/filename),exportType='STEP')
            item.kind='cad'
            item.cad_file=str((folder/filename).resolve())
        imported.append(item)
        for i,child in enumerate(node.children):
            walk(child,item.id,f'{path}/{child.name or i}')

    walk(assembly,None,assembly.name or source.stem)
    return imported
