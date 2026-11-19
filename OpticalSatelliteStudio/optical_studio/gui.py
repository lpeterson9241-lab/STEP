"""Optical Satellite Studio v0.1 — parametrically editable CAD-oriented optical scene editor."""
import sys
from pathlib import Path
import math
import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QFormLayout,
    QTreeWidget, QTreeWidgetItem, QDockWidget, QDoubleSpinBox, QSpinBox, QLineEdit, QComboBox,
    QCheckBox, QFileDialog, QMessageBox, QToolBar, QLabel, QScrollArea, QPushButton,
    QSplitter, QStatusBar, QButtonGroup, QListView, QInputDialog,
    QDialog, QListWidget, QListWidgetItem, QTextEdit, QDialogButtonBox, QSizePolicy,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d import proj3d
import numpy as np

from .model import Project, Component, cassegrain_template, detailed_cassegrain_template, opir_template, component, uid
from .geometry import surface_mesh,preview_surfaces
from .export import export_manifest
from .assembly import world_matrix, world_point, distance_between, reparent_preserving_world, set_world_pose
from .constraints import apply_constraints

COLORS = {'mirror':'#8bc4eb', 'baffle':'#6e8792', 'housing':'#7a8791',
          'detector':'#d38e54', 'aperture':'#59aa85', 'filter':'#a19ed5',
          'cad':'#b18bba', 'assembly':'#d3d9e2', 'lens':'#9de0d1',
          'vane_stack':'#b49c78','spider':'#e3b77e','mirror_mount':'#a7b2bd',
          'optical_bench':'#6c9cab'}


class PropertySection(QWidget):
    """Compact collapsible property group with full-width, untruncated labels."""
    def __init__(self, title, expanded=False, parent=None):
        super().__init__(parent)
        self.title = title
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        stack = QVBoxLayout(self)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)
        self.header = QPushButton()
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setStyleSheet(
            'QPushButton{text-align:left;background:#294254;color:#ffffff;'
            'border:0;border-radius:0;padding:8px 12px;font-weight:bold;}'
            'QPushButton:hover{background:#34596e;}'
            'QPushButton:checked{background:#206a9b;color:#ffffff;border:0;}')
        stack.addWidget(self.header)
        self.body = QWidget()
        self.form = QFormLayout(self.body)
        self.form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.form.setLabelAlignment(Qt.AlignLeft)
        self.form.setContentsMargins(12, 8, 12, 10)
        self.form.setHorizontalSpacing(3)
        self.form.setVerticalSpacing(4)
        stack.addWidget(self.body)
        self.header.toggled.connect(self._toggle)
        self._toggle(expanded)

    @property
    def expanded(self):
        return self.header.isChecked()

    def _toggle(self, expanded):
        self.body.setVisible(expanded)
        self.header.setText(('▾  ' if expanded else '▸  ') + self.title)
        self.updateGeometry()

class Viewer(FigureCanvasQTAgg):
    component_clicked=Signal(str)
    component_dragged=Signal(str,str,float)
    component_drag_finished=Signal(str)
    face_clicked=Signal(int)
    VIEWS = {
        'Isometric': (22, -65),
        'Front (−Y)': (0, -90),
        'Back (+Y)': (0, 90),
        'Left (−X)': (0, 180),
        'Right (+X)': (0, 0),
        'Top (+Z)': (90, -90),
        'Bottom (−Z)': (-90, -90),
    }

    def __init__(self):
        self.figure = Figure(figsize=(7, 6), facecolor='#101820')
        super().__init__(self.figure)
        self.ax = self.figure.add_subplot(111, projection='3d')
        self.view_elev, self.view_azim = 22, -65
        self.free_rotate = False
        self.section_cutaway=False
        self.external_rays={}
        self.mpl_connect('button_release_event', self.save_camera)
        self.ax.disable_mouse_rotation()
        self._cad_mesh_cache = {}
        self.highlight_face = None
        self.move_axis = None
        self.face_pick_component = None
        self._pick_points = {}
        self._press = None
        self.mpl_connect('button_press_event',self.mouse_press)
        self.mpl_connect('motion_notify_event',self.mouse_move)
        self.mpl_connect('button_release_event',self.mouse_release)

    def _screen(self, points):
        points=np.asarray(points).reshape((-1,3))
        px,py,pz=proj3d.proj_transform(points[:,0],points[:,1],points[:,2],self.ax.get_proj())
        return self.ax.transData.transform(np.column_stack([px,py]))

    def _pick(self,x,y):
        best=None;distance=22.0
        for component_id,points in self._pick_points.items():
            if len(points)==0:continue
            xy=self._screen(points)
            d=np.min(np.linalg.norm(xy-np.array([x,y]),axis=1))
            if d<distance:distance=d;best=component_id
        return best

    def mouse_press(self,event):
        if event.button!=1 or event.inaxes!=self.ax:return
        picked=self._pick(event.x,event.y)
        self._press=dict(x=event.x,y=event.y,last_x=event.x,last_y=event.y,
                         picked=picked,moved=False)

    def mouse_move(self,event):
        state=self._press
        if not state or event.inaxes!=self.ax or not self.move_axis or self.free_rotate:return
        if state['picked'] is None:return
        diff=np.array([event.x-state['last_x'],event.y-state['last_y']])
        if np.linalg.norm([event.x-state['x'],event.y-state['y']])<3:return
        state['moved']=True
        points=self._pick_points.get(state['picked'])
        if points is None or len(points)==0:return
        origin=np.mean(points,axis=0)
        unit=np.eye(3)[{'X':0,'Y':1,'Z':2}[self.move_axis]]
        px=self._screen(np.array([origin,origin+unit*100]))
        axis_px=px[1]-px[0]
        magnitude=float(axis_px@axis_px)
        if magnitude<1e-8:return
        delta=float(diff@axis_px/magnitude*100)
        state['last_x'],state['last_y']=event.x,event.y
        if abs(delta)>1e-9:self.component_dragged.emit(state['picked'],self.move_axis,delta)

    def mouse_release(self,event):
        state=self._press;self._press=None
        if not state or event.button!=1:return
        if state['moved'] and state['picked'] is not None:
            self.component_drag_finished.emit(state['picked'])
        if np.linalg.norm([event.x-state['x'],event.y-state['y']])<5 and not state['moved']:
            if self.face_pick_component is not None:
                face_index=self._pick_face(event.x,event.y)
                if face_index is not None:self.face_clicked.emit(face_index)
            elif state['picked'] is not None:
                self.component_clicked.emit(state['picked'])

    def _pick_face(self,x,y):
        # B-rep face geometry is sampled for screen-space picking; the assigned
        # face is the exact OCCT face, while the click tolerance is approximate.
        comp=getattr(self,'_face_pick_comp',None)
        if comp is None:return None
        try:
            from .cad_exchange import make_shape
            best=None;distance=18.0
            matrix=self._face_pick_transform
            for index,face in enumerate(make_shape(comp).Faces(),1):
                vertices,triangles=face.tessellate(1.2)
                points=np.array([[v.x,v.y,v.z] for v in vertices],dtype=float)
                if not len(points):continue
                points=points[::max(1,len(points)//100)]
                points=points@matrix[:3,:3].T+matrix[:3,3]
                delta=np.min(np.linalg.norm(self._screen(points)-[x,y],axis=1))
                if delta<distance:best=index;distance=delta
            return best
        except Exception:return None

    def save_camera(self, event):
        if self.free_rotate and event.inaxes is self.ax:
            self.view_elev, self.view_azim = self.ax.elev, self.ax.azim

    def set_view(self, name):
        self.view_elev, self.view_azim = self.VIEWS[name]
        self.ax.view_init(elev=self.view_elev, azim=self.view_azim)
        self.draw_idle()

    def set_free_rotate(self, enabled):
        self.free_rotate = bool(enabled)
        if self.free_rotate:
            self.ax.mouse_init()
        else:
            self.ax.disable_mouse_rotation()
        self.draw_idle()

    def redraw(self, project, selected_id=None):
        ax = self.ax
        ax.clear()
        ax.set_facecolor('#101820')
        samples = []
        self._pick_points={}
        self._face_pick_comp=next((c for c in project.components if c.id==self.face_pick_component),None)
        self._face_pick_transform=(world_matrix(project,self.face_pick_component)
                                   if self._face_pick_comp is not None else None)
        for comp in project.components:
            if not comp.enabled:
                continue
            if comp.kind == 'assembly': continue
            transform = world_matrix(project, comp.id)
            surfs = preview_surfaces(comp, world_transform=transform)
            if surfs is None:
                # CadQuery is optional; preserve an informative fallback marker.
                try:
                    from .cad_exchange import tessellate_component
                    file_path = Path(comp.cad_file)
                    cache_key = (str(file_path), file_path.stat().st_mtime_ns)
                    if cache_key not in self._cad_mesh_cache:
                        self._cad_mesh_cache[cache_key] = tessellate_component(comp)
                    verts, faces = self._cad_mesh_cache[cache_key]
                    pts = verts @ transform[:3, :3].T + transform[:3, 3]
                    if len(faces) > 75000:
                        faces=faces[::int(np.ceil(len(faces)/75000))]
                    if self.section_cutaway:
                        faces=np.asarray([triangle for triangle in faces
                                          if np.all(pts[triangle,1]<=0)],dtype=int).reshape((-1,3))
                    if len(faces):ax.plot_trisurf(pts[:,0], pts[:,1], pts[:,2], triangles=faces,
                                    color=COLORS['cad'], alpha=.85 if comp.id==selected_id else .35,
                                    linewidth=0)
                    samples.append(pts)
                    self._pick_points[comp.id]=pts[::max(1,len(pts)//300)]
                    continue
                except (ImportError, RuntimeError, FileNotFoundError, ValueError, AttributeError, OSError):
                    p = transform[:3,3]
                    ax.scatter([p[0]], [p[1]], [p[2]], s=70, color=COLORS['cad'], marker='D')
                    ax.text(*p, f' {comp.name} [STEP unavailable]', color='#d6c9eb', fontsize=8)
                    samples.append(p[None, :]);self._pick_points[comp.id]=p[None,:]; continue
            c = COLORS.get(comp.kind, '#a8bcc7')
            comp_points=[]
            for x,y,z in surfs:
                if self.section_cutaway:
                    mask=y>0
                    x,y,z=x.copy(),y.copy(),z.copy()
                    x[mask]=np.nan;y[mask]=np.nan;z[mask]=np.nan
                if not np.any(np.isfinite(x)):continue
                ax.plot_surface(x, y, z, color=c, alpha=0.83 if comp.id==selected_id else 0.32,
                                edgecolor='none', linewidth=0, shade=True, antialiased=False)
                if comp.kind in ('mirror','lens','detector','aperture','filter'):
                    ax.plot(x[:, -1],y[:, -1],z[:, -1],color='#ffffff' if comp.id==selected_id else c,
                            lw=1.2, alpha=.95)
                points=np.column_stack([x.ravel(),y.ravel(),z.ravel()])
                comp_points.append(points[np.isfinite(points).all(axis=1)])
            if comp_points:
                current=np.concatenate(comp_points,axis=0)
                samples.append(current)
                self._pick_points[comp.id]=current[::max(1,len(current)//300)]
        if self.highlight_face:
            face_comp_id,face_index=self.highlight_face
            comp=next((c for c in project.components if c.id==face_comp_id),None)
            if comp is not None:
                try:
                    from .cad_exchange import make_shape
                    face=make_shape(comp).Faces()[face_index-1]
                    points,triangles=face.tessellate(0.75)
                    xyz=np.array([[v.x,v.y,v.z] for v in points])
                    matrix=world_matrix(project,comp.id)
                    xyz=xyz @ matrix[:3,:3].T+matrix[:3,3]
                    ax.plot_trisurf(xyz[:,0],xyz[:,1],xyz[:,2],triangles=triangles,
                                    color='#ffd166',alpha=0.95,linewidth=0)
                except Exception:
                    pass
        samples = [s[np.isfinite(s).all(axis=1)] for s in samples]
        samples = [s for s in samples if len(s)]
        if self.external_rays:
            for i,(ray_id,xyz) in enumerate(self.external_rays.items()):
                if i>=250:break
                ax.plot(xyz[:,0],xyz[:,1],xyz[:,2],color='#ffcc66',
                        alpha=0.6,lw=1.1)
                samples.append(xyz)
        if samples:
            allpts = np.concatenate(samples,axis=0)
            mn, mx = allpts.min(axis=0), allpts.max(axis=0)
            center=(mn+mx)/2
            extent=max(float(np.max(mx-mn)),100.0)*.58
            ax.set_xlim(center[0]-extent,center[0]+extent)
            ax.set_ylim(center[1]-extent,center[1]+extent)
            ax.set_zlim(center[2]-extent,center[2]+extent)
        ax.set_box_aspect((1,1,1))
        for axis in (ax.xaxis,ax.yaxis,ax.zaxis):
            axis.pane.fill = False
            axis.line.set_color('#72818c')
            axis.set_tick_params(colors='#aebcc7',labelsize=7)
        ax.set_xlabel('X (mm)', color='#d6e4ed',fontsize=8)
        ax.set_ylabel('Y (mm)', color='#d6e4ed',fontsize=8)
        ax.set_zlabel('Z (mm)', color='#d6e4ed',fontsize=8)
        ax.view_init(elev=self.view_elev, azim=self.view_azim)
        if selected_id and self.move_axis and selected_id in self._pick_points:
            center=world_point(project,selected_id)
            span=max((np.max(mx-mn) if samples else 100)*0.12,15)
            for axis_index,color in enumerate(('#ff6b6b','#6ddd8d','#58acff')):
                direction=np.eye(3)[axis_index]*span
                ax.quiver(*center,*direction,color=color,linewidth=2.8,
                          arrow_length_ratio=.25,alpha=1.0 if 'XYZ'[axis_index]==self.move_axis else .4)
        self.figure.tight_layout()
        self.draw_idle()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.project=cassegrain_template()
        self.loaded_results=None
        self.results_geometry_signature=None
        self.active_profile_name=None
        self.current_path=None
        self.selected_id=self.project.components[0].id
        self.setWindowTitle('Optical Satellite Studio — v0.15.0 (Zemax front end)')
        self.resize(1420,860)
        self._build()
        self.refresh_tree()
        self.refresh_editor()
        self.refresh_view()

    def closeEvent(self,event):
        # Drop 3D move-guide artists before Qt destroys a modal-dialog stack.
        # This also avoids retaining Matplotlib projection callbacks at exit.
        self.viewer.move_axis=None
        self.viewer._press=None
        self.viewer.ax.clear()
        super().closeEvent(event)

    def _build(self):
        self.setStyleSheet('QMainWindow{background:#15212c;color:#e2eaf0;} '
            'QDockWidget{color:#e2eaf0;} QDockWidget::title{background:#203746;color:#f2f7fa;padding:5px;} '
            'QDockWidget QWidget{background:#1c2b38;color:#e2eaf0;} '
            'QWidget{font-size:12px;} '
            'QTreeWidget,QScrollArea,QLineEdit,QDoubleSpinBox{background:#1c2b38;color:#e2eaf0;border:1px solid #344b5a;} '
            'QTreeWidget::item:selected{background:#206a9b;color:#ffffff;} '
            'QHeaderView::section{background:#294254;color:#ffffff;padding:5px;} '
            'QPushButton{background:#294254;color:#ffffff;padding:7px;border:1px solid #456070;border-radius:3px;} '
            'QPushButton:checked{background:#206a9b;color:#ffffff;border:1px solid #92d4fb;font-weight:bold;} '
            'QPushButton:hover{background:#375b70;} '
            'QLabel,QCheckBox{color:#e2eaf0;} QToolBar{background:#213542;spacing:4px;} '
            'QMenuBar{background:#213542;color:#ffffff;} '
            'QMenuBar::item:selected{background:#34596e;color:#ffffff;} '
            'QMenu{background:#1c2b38;color:#ffffff;border:1px solid #456070;} '
            'QMenu::item{padding:6px 24px;} '
            'QMenu::item:selected{background:#206a9b;color:#ffffff;}')
        menu_bar=self.menuBar()
        # Keep the menu inside the window on macOS for a predictable layout.
        menu_bar.setNativeMenuBar(False)
        sections={
            'File': [('Open project',self.open),('Save project',self.save),
                     ('Export Zemax handoff JSON',self.export),('Export optical prescription JSON',self.export_sequential),
                     ('Export STEP package',self.export_cad)],
            'Templates': [('Cassegrain telescope',self.new),
                          ('Detailed Cassegrain + mechanics',self.new_detailed),
                          ('OPIR satellite',self.new_opir)],
            'Assembly': [('Add assembly group',lambda:self.add('assembly')),
                         ('Optical element separations',self.edit_optical_spacings),
                         ('Attach STEP model',self.attach_step),
                         ('Import STEP assembly',self.import_step_assembly),
                         ('Placement constraints',self.edit_constraints),
                         ('Remove selected component',self.remove)],
            'Components': [('Mirror',lambda:self.add('mirror')),
                           ('Lens',lambda:self.add('lens')),
                           ('Baffle',lambda:self.add('baffle')),
                           ('Baffle vane stack',lambda:self.add('vane_stack')),
                           ('Spider support',lambda:self.add('spider')),
                           ('Mirror mounting ring',lambda:self.add('mirror_mount')),
                           ('Optical bench',lambda:self.add('optical_bench')),
                           ('Detector',lambda:self.add('detector')),
                           ('Housing',lambda:self.add('housing')),
                           ('Aperture',lambda:self.add('aperture')),
                           ('Filter',lambda:self.add('filter'))],
            'Properties': [('Parameter dependencies',self.edit_links),
                           ('Optical path and wavelengths',self.edit_optical_path),
                           ('Material library (JSON)',self.edit_materials),
                           ('Material catalog manager',lambda:self.manage_catalog('material')),
                           ('Coating catalog manager',lambda:self.manage_catalog('coating'))],
            'Physics': [('Operating temperatures and alignment',self.edit_operating_profiles),
                        ('Material physics catalog',lambda:self.manage_catalog('material')),
                        ('Surface physics and scattering',self.edit_surface_behaviors),
                        ('Coating physics catalog',lambda:self.manage_catalog('coating')),
                        ('Thermo-optical and IR emission report',self.show_thermo_optical_report)],
            'Tools': [('Measure center distances',self.measure),
                      ('Thermo-optical and IR emission report',self.show_thermo_optical_report),
                      ('Measure exact CAD clearance',self.measure_clearance),
                      ('Inspect and assign CAD faces',lambda:self.inspect_faces()),
                      ('Assign optical behavior to surfaces',self.edit_surface_behaviors),
                      ('Audit exported Zemax package',self.audit_zemax_package),
                      ('Import external optical results',self.import_external_results),
                      ('View imported detector map',self.view_detector_map),
                      ('Check solid interferences',self.check_interferences),
                      ('Validate assembly',self.validate_scene)],
        }
        self.menus={}
        for title,entries in sections.items():
            menu=menu_bar.addMenu(title)
            self.menus[title]=menu
            for label,callback in entries:
                action=menu.addAction(label)
                action.triggered.connect(callback)
        toolbar=QToolBar('Quick actions')
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(toolbar)
        for label,callback in [('Open',self.open),('Save',self.save),
                               ('Validate',self.validate_scene),('Export STEP',self.export_cad)]:
            action=toolbar.addAction(label)
            action.triggered.connect(callback)
        self.viewer=Viewer()
        self.viewer.component_clicked.connect(self.select_from_viewport)
        self.viewer.component_dragged.connect(self.drag_component)
        self.viewer.component_drag_finished.connect(self.finish_drag)
        self.viewer.face_clicked.connect(self.inspect_faces)
        center=QWidget(); layout=QVBoxLayout(center); layout.setContentsMargins(4,4,4,4)
        view_bar=QHBoxLayout()
        view_bar.addWidget(QLabel('Camera view:'))
        # Always-visible buttons avoid the macOS native popup palette issue,
        # which rendered nearly white menu text on a white menu background.
        self.view_buttons=QButtonGroup(self)
        self.view_buttons.setExclusive(True)
        for label, short_label in [('Isometric','Isometric'), ('Front (−Y)','Front'),
                                   ('Back (+Y)','Back'), ('Left (−X)','Left'),
                                   ('Right (+X)','Right'), ('Top (+Z)','Top'),
                                   ('Bottom (−Z)','Bottom')]:
            btn=QPushButton(short_label)
            btn.setCheckable(True)
            btn.setToolTip(f'Camera: {label}')
            btn.setMinimumWidth(54)
            btn.clicked.connect(lambda checked, name=label:self.choose_view(name))
            self.view_buttons.addButton(btn)
            view_bar.addWidget(btn)
            if label == 'Isometric': btn.setChecked(True)
        self.free_rotate_toggle=QCheckBox('Free rotate (click and drag)')
        self.free_rotate_toggle.setChecked(False)
        self.free_rotate_toggle.setToolTip('Enable or disable mouse-driven camera rotation')
        self.free_rotate_toggle.toggled.connect(self.set_free_rotate)
        view_bar.addWidget(self.free_rotate_toggle)
        self.section_toggle=QCheckBox('Cutaway +Y')
        self.section_toggle.setToolTip('Preview-only cutaway at world Y=0. Does not cut or modify STEP solids.')
        self.section_toggle.toggled.connect(self.set_section_cutaway)
        view_bar.addWidget(self.section_toggle)
        self.rays_toggle=QCheckBox('External ray overlay')
        self.rays_toggle.setEnabled(False)
        self.rays_toggle.toggled.connect(self.set_external_ray_overlay)
        view_bar.addWidget(self.rays_toggle)
        view_bar.addStretch(1)
        layout.addLayout(view_bar)
        move_bar=QHBoxLayout()
        move_bar.addWidget(QLabel('Viewport tool:'))
        self.move_buttons=QButtonGroup(self)
        self.move_buttons.setExclusive(True)
        for label,axis in [('Select',None),('Move X','X'),('Move Y','Y'),('Move Z','Z'),('Pick face','FACE')]:
            button=QPushButton(label);button.setCheckable(True)
            button.clicked.connect(lambda checked,move_axis=axis:self.set_move_axis(move_axis))
            self.move_buttons.addButton(button);move_bar.addWidget(button)
            if axis is None:button.setChecked(True)
        move_bar.addStretch(1)
        move_bar.addWidget(QLabel('Operating profile:'))
        self.profile_combo=QComboBox();self.profile_combo.setMinimumWidth(190)
        self.profile_combo.currentIndexChanged.connect(self.choose_operating_profile)
        move_bar.addWidget(self.profile_combo)
        layout.addLayout(move_bar)
        layout.addWidget(self.viewer,1)
        note=QLabel('3D assembly • +Y cutaway is a preview, not a B-rep section • STEP requires optional CadQuery • optics not Zemax-validated')
        note.setStyleSheet('color:#91aaba; padding:6px;'); layout.addWidget(note)
        self.setCentralWidget(center)
        left=QDockWidget('Optical assembly',self); left.setAllowedAreas(Qt.LeftDockWidgetArea|Qt.RightDockWidgetArea)
        self.tree=QTreeWidget(); self.tree.setHeaderLabels(['Component','Type'])
        self.tree.currentItemChanged.connect(self.on_select)
        left.setWidget(self.tree); self.addDockWidget(Qt.LeftDockWidgetArea,left)
        right=QDockWidget('Component properties',self)
        self.properties_scroll=QScrollArea(); self.properties_scroll.setWidgetResizable(True)
        self.properties_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.property_container=QWidget()
        self.property_layout=QVBoxLayout(self.property_container)
        self.property_layout.setContentsMargins(0,0,0,0)
        self.property_layout.setSpacing(5)
        self.property_layout.setAlignment(Qt.AlignTop)
        self.sections={}
        self.properties_scroll.setWidget(self.property_container)
        right.setWidget(self.properties_scroll)
        self.addDockWidget(Qt.RightDockWidgetArea,right)
        self.resizeDocks([left,right],[280,330],Qt.Horizontal)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage('Ready • geometry in millimeters')
        self.refresh_profile_combo()

    def refresh_profile_combo(self):
        selected=self.active_profile_name
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear();self.profile_combo.addItem('Nominal design',None)
        for name in sorted(self.project.metadata.get('operating_profiles',{})):
            self.profile_combo.addItem(name,name)
        index=self.profile_combo.findData(selected)
        if selected is not None and index<0:self.active_profile_name=None
        self.profile_combo.setCurrentIndex(max(0,index))
        self.profile_combo.blockSignals(False)

    def choose_operating_profile(self,index):
        self.active_profile_name=self.profile_combo.itemData(index)
        self.refresh_editor();self.refresh_view()
        self.statusBar().showMessage('Profile: '+(self.active_profile_name or 'Nominal')+
                                     ' • property edits remain nominal')

    def active_project(self):
        if self.active_profile_name is None:return self.project
        from .operating_profiles import resolve_profile
        return resolve_profile(self.project,self.active_profile_name)[0]

    def resolve_active_report(self):
        if self.active_profile_name is None:return self.project,None
        from .operating_profiles import resolve_profile
        return resolve_profile(self.project,self.active_profile_name)

    def set_move_axis(self,axis):
        self.viewer.move_axis=axis if axis in ('X','Y','Z') else None
        self.viewer.face_pick_component=self.selected_id if axis=='FACE' else None
        if axis is not None and self.free_rotate_toggle.isChecked():
            self.free_rotate_toggle.setChecked(False)
        self.refresh_view()
        self.statusBar().showMessage('Viewport: '+('click to select' if axis is None else f'drag selected parts along world {axis}'))

    def select_from_viewport(self,component_id):
        if component_id!=self.selected_id:
            self.selected_id=component_id
            if self.viewer.face_pick_component is not None:
                self.viewer.face_pick_component=component_id
            self.refresh_tree();self.refresh_editor();self.refresh_view()

    def drag_component(self,component_id,axis,delta_mm):
        c=next((item for item in self.project.components if item.id==component_id),None)
        if c is None:return
        if any(rule['target_id']==component_id for rule in self.project.constraints):
            self.statusBar().showMessage('This part is controlled by a placement constraint; edit the constraint instead')
            return
        pose=world_matrix(self.project,component_id)
        pose['XYZ'.index(axis),3]+=delta_mm
        set_world_pose(self.project,component_id,pose)
        apply_constraints(self.project)
        self.selected_id=component_id
        self.refresh_view()

    def finish_drag(self,component_id):
        self.selected_id=component_id
        self.refresh_tree();self.refresh_editor();self.refresh_view()

    def choose_view(self, name):
        if name:
            self.viewer.set_view(name)
            # Preset selection restores the fixed-camera workflow.
            if self.free_rotate_toggle.isChecked():
                self.free_rotate_toggle.setChecked(False)
            self.statusBar().showMessage(f'Camera: {name} • rotation locked')

    def set_free_rotate(self, enabled):
        self.viewer.set_free_rotate(enabled)
        if enabled:
            self.statusBar().showMessage('Camera: free rotation enabled; drag in the viewport')
        else:
            self.statusBar().showMessage('Camera: rotation locked; choose a preset view')

    def set_section_cutaway(self,enabled):
        self.viewer.section_cutaway=bool(enabled)
        self.refresh_view()
        self.statusBar().showMessage('Cutaway +Y: preview only; original mechanical solids remain intact'
                                     if enabled else 'Full assembly preview restored')

    def set_external_ray_overlay(self,enabled):
        self.viewer.external_rays=(self.loaded_results['rays'] if enabled and self.loaded_results else {})
        self.refresh_view()

    def selected(self):
        return next((c for c in self.project.components if c.id==self.selected_id),None)

    def refresh_tree(self):
        self.tree.blockSignals(True); self.tree.clear()
        root=QTreeWidgetItem([self.project.name, 'Project'])
        self.tree.addTopLevelItem(root)
        items = {}
        for c in self.project.components:
            item=QTreeWidgetItem([c.name,c.kind]); item.setData(0,Qt.UserRole,c.id)
            items[c.id] = item
        for c in self.project.components:
            (items[c.parent_id] if c.parent_id in items else root).addChild(items[c.id])
        if self.selected_id in items: self.tree.setCurrentItem(items[self.selected_id])
        root.setExpanded(True); self.tree.blockSignals(False)
        for i in items.values(): i.setExpanded(True)

    def on_select(self, current, previous):
        if current is None: return
        key=current.data(0,Qt.UserRole)
        if key is None: return
        if self.viewer.face_pick_component is not None:
            self.viewer.face_pick_component=key
        self.selected_id=key;self.refresh_editor();self.refresh_view()

    def refresh_editor(self):
        expanded={title:section.expanded for title,section in self.sections.items()}
        while self.property_layout.count():
            item=self.property_layout.takeAt(0)
            if item.widget():item.widget().deleteLater()
        forms={}
        self.sections={}
        for title in ('Identity','Placement','Spacing','Dimensions','Optical prescription','Optical properties','Physics','Advanced'):
            section=PropertySection(title,expanded.get(title,title in ('Identity','Spacing','Physics')))
            self.sections[title]=section
            self.property_layout.addWidget(section)
            forms[title]=section.form
        c=self.selected()
        if not c: return
        def text_field(section,label,value,setter):
            w=QLineEdit(str(value)); w.editingFinished.connect(lambda:self._edit(setter,w.text()))
            forms[section].addRow(label,w)
        text_field('Identity','Name',c.name,lambda v:setattr(c,'name',v))
        parent_box=QComboBox()
        parent_box.setEditable(True)
        parent_box.lineEdit().setReadOnly(True)
        parent_box.setInsertPolicy(QComboBox.NoInsert)
        parent_box.setView(QListView())
        parent_box.setStyleSheet('QComboBox,QComboBox QAbstractItemView,QComboBox QLineEdit{'
                                 'background:#1c2b38;color:#ffffff;selection-background-color:#206a9b;}')
        parent_box.addItem('Project root',None)
        for parent in self.project.components:
            if parent.id != c.id:
                parent_box.addItem(f'{parent.name} [{parent.id[:6]}]',parent.id)
        idx=parent_box.findData(c.parent_id)
        parent_box.setCurrentIndex(max(0,idx))
        parent_box.currentIndexChanged.connect(lambda index,w=parent_box: self.set_parent(w.itemData(index)))
        forms['Identity'].addRow('Parent component',parent_box)
        cb=QCheckBox();cb.setChecked(c.enabled)
        cb.toggled.connect(lambda v:self._edit(lambda x:setattr(c,'enabled',x),v))
        forms['Identity'].addRow('Visible',cb)
        for label,vec in [('Position XYZ (mm)',c.position),('Rotation XYZ (°)',c.rotation)]:
            for i,axis in enumerate('XYZ'):
                w=QDoubleSpinBox();w.setRange(-1_000_000,1_000_000);w.setDecimals(3)
                w.setSingleStep(5 if label.startswith('Position') else 1)
                w.setValue(vec[i]);w.valueChanged.connect(lambda v,a=vec,j=i:self._edit(lambda x:a.__setitem__(j,x),v))
                if any(rule['target_id']==c.id for rule in self.project.constraints):
                    w.setEnabled(False)
                    w.setToolTip('Placement controlled by an assembly constraint. Edit the constraint in Assembly menu.')
                forms['Placement'].addRow(f'{label} · {axis}',w)
        global_location=world_point(self.project,c.id)
        forms['Placement'].addRow('World XYZ (mm)',QLabel(', '.join(f'{v:.3f}' for v in global_location)))
        from .spacing import optical_order,mechanical_order,spacing_measure
        optical_ids=optical_order(self.project)
        order=optical_ids if c.id in optical_ids else mechanical_order(self.project)
        if c.id in order and len(order)>1:
            pos=order.index(c.id)
            neighbor_id=order[pos+1] if pos+1<len(order) else order[pos-1]
            source_id,target_id=(c.id,neighbor_id) if pos+1<len(order) else (neighbor_id,c.id)
            other=next(x for x in self.project.components if x.id==neighbor_id)
            measure=spacing_measure(self.project,source_id,target_id)
            forms['Spacing'].addRow('Adjacent '+('optical' if c.id in optical_ids else 'mechanical')+' element',QLabel(other.name))
            forms['Spacing'].addRow('Center distance (mm)',QLabel(f'{measure["center_distance_mm"]:.4f}'))
            forms['Spacing'].addRow('Transverse offset (mm)',QLabel(f'{measure["transverse_mm"]:.4f}'))
            spacer=QDoubleSpinBox();spacer.setRange(-1e6,1e6);spacer.setDecimals(4)
            spacer.setSuffix(' mm');spacer.setValue(measure['signed_axial_mm'])
            spacer.setToolTip('Signed separation along the first element’s local +Z. '
                              'Preserves transverse offset; not a closest-surface clearance.')
            spacer.editingFinished.connect(lambda widget=spacer,a=source_id,b=target_id:
                                           self.apply_spacing(a,b,widget.value()))
            forms['Spacing'].addRow('Signed axial separation',spacer)
        else:
            forms['Spacing'].addRow(QLabel('Select an optical element to inspect its neighbor.'))
        all_spacings=QPushButton('View / edit all separations…')
        all_spacings.clicked.connect(self.edit_optical_spacings)
        forms['Spacing'].addRow(all_spacings)
        for k,v in c.params.items():
            if not isinstance(v,(int,float)):continue
            optical_key=(k in ('radius','conic','front_radius','back_radius','front_conic','back_conic')
                         or k.startswith('a') and k[1:].isdigit()
                         or k.startswith('front_a') or k.startswith('back_a'))
            section='Optical prescription' if optical_key else 'Dimensions'
            if k.startswith('a') and k[1:].isdigit() or k.startswith(('front_a','back_a')):
                w=QLineEdit(format(float(v),'.12g'))
                validator=QDoubleValidator(w);validator.setNotation(QDoubleValidator.ScientificNotation)
                w.setValidator(validator)
                w.editingFinished.connect(lambda widget=w,key=k:self._edit(
                    lambda value:c.params.__setitem__(key,value),float(widget.text() or 0)))
                w.setToolTip('Even-asphere coefficient; scientific notation accepted, in mm^(1−order)')
                label=k.replace('_',' ').upper()+' (mm^(1−order))'
                forms[section].addRow(label,w)
                continue
            if k in ('vane_count','arm_count'):
                w=QSpinBox();w.setRange(1 if k=='vane_count' else 2,100 if k=='vane_count' else 12)
            else:
                w=QDoubleSpinBox();w.setDecimals(5);w.setRange(-1_000_000,1_000_000)
            if k in ('diameter','outer_diameter','inner_diameter','length','hole_diameter',
                     'hub_diameter','arm_width','thickness','width','vane_thickness','spacing'):
                w.setMinimum(0)
            w.setValue(float(v));w.valueChanged.connect(lambda x,key=k:self._edit(lambda value:c.params.__setitem__(key,value),x))
            forms[section].addRow(k.replace('_',' ').title(),w)
        for field,label in [('material','Material'),('coating','Coating / scatter tag')]:
            text_field('Optical properties',label,getattr(c,field),lambda v,k=field:setattr(c,k,v))
        operating_temperature=c.metadata.get('temperature_K')
        nominal_temperature=QLineEdit('' if operating_temperature is None else str(operating_temperature))
        nominal_temperature.setPlaceholderText('Unspecified; set positive kelvin')
        nominal_temperature.setToolTip('Component default temperature in kelvin; operating-profile overrides take precedence.')
        nominal_temperature.editingFinished.connect(lambda widget=nominal_temperature,part=c:
            self.set_nominal_component_temperature(part,widget.text()))
        forms['Physics'].addRow('Nominal temperature (K)',nominal_temperature)
        if self.active_profile_name:
            resolved=self.active_project()
            active_comp=next((part for part in resolved.components if part.id==c.id),None)
            if active_comp:
                forms['Physics'].addRow('Active profile temperature (K)',QLabel(
                    str(active_comp.metadata.get('operating_temperature_K','Unspecified'))))
        from .surface_catalog import semantic_surfaces,semantic_key
        faces=semantic_surfaces(c)
        if faces:
            surface_box=QComboBox()
            for face in faces:surface_box.addItem(face,semantic_key(face))
            forms['Physics'].addRow('Surface for emissivity',surface_box)
            surface_emissivity=QLineEdit();surface_emissivity.setPlaceholderText('Unspecified (0 to 1)')
            def update_face_emissivity():
                value=c.surface_properties.get(surface_box.currentData(),{}).get('emissivity')
                surface_emissivity.setText('' if value is None else str(value))
            surface_box.currentIndexChanged.connect(update_face_emissivity)
            surface_emissivity.editingFinished.connect(lambda part=c,box=surface_box,widget=surface_emissivity:
                self.set_surface_emissivity(part,box.currentData(),widget.text()))
            forms['Physics'].addRow('Surface emissivity (0–1)',surface_emissivity)
            update_face_emissivity()
        for label,callback in [('Operating profiles…',self.edit_operating_profiles),
                               ('Material physics…',lambda:self.manage_catalog('material')),
                               ('Surface / scatter physics…',self.edit_surface_behaviors),
                               ('IR emission report…',self.show_thermo_optical_report)]:
            button=QPushButton(label);button.clicked.connect(callback)
            forms['Physics'].addRow(button)
        text_field('Optical properties','Face properties (JSON)',json.dumps(c.surface_properties,ensure_ascii=False),
                   lambda v:self.set_surface_properties(c,v))
        text_field('Advanced','Component metadata (JSON)',json.dumps(c.metadata,ensure_ascii=False),
                   lambda v:self.set_component_metadata(c,v))
        if c.kind=='cad':
            label=QLabel(c.cad_file);label.setWordWrap(True);forms['Advanced'].addRow('STEP file',label)
        forms['Advanced'].addRow(QLabel('Optical mappings require Zemax verification.'))
        if self.active_profile_name:
            try:
                resolved,_=self.resolve_active_report()
                part=next(x for x in resolved.components if x.id==c.id)
                forms['Advanced'].addRow('Operating configuration',QLabel(
                    f'{self.active_profile_name}: {part.metadata.get("operating_temperature_K",0):g} K '
                    '(inputs above are nominal)'))
            except (ValueError,KeyError,StopIteration):pass

    def set_parent(self, parent_id):
        c=self.selected()
        if c is None: return
        try:
            reparent_preserving_world(self.project,c.id,parent_id)
        except (ValueError,KeyError) as ex:
            QMessageBox.warning(self,'Invalid parent',str(ex))
        self.refresh_tree(); self.refresh_editor(); self.refresh_view()

    def set_surface_properties(self, c, text):
        try:
            value=json.loads(text)
            if not isinstance(value,dict): raise ValueError('Must be a JSON object')
        except (ValueError,TypeError) as ex:
            QMessageBox.warning(self,'Invalid surface mapping',str(ex));return
        c.surface_properties=value

    def set_component_metadata(self,c,text):
        try:
            value=json.loads(text)
            if not isinstance(value,dict): raise ValueError('Must be a JSON object')
        except (ValueError,TypeError) as ex:
            QMessageBox.warning(self,'Invalid metadata',str(ex));return
        c.metadata=value

    def set_nominal_component_temperature(self,component,text):
        previous=component.metadata.get('temperature_K')
        try:
            value=float(text) if text.strip() else None
            if value is not None and (not math.isfinite(value) or value<=0):
                raise ValueError('Temperature must be a positive number of kelvin')
            if value is None:component.metadata.pop('temperature_K',None)
            else:component.metadata['temperature_K']=value
            self.project.validate()
        except (ValueError,TypeError) as exc:
            if previous is None:component.metadata.pop('temperature_K',None)
            else:component.metadata['temperature_K']=previous
            self.statusBar().showMessage('Temperature not changed: '+str(exc));return
        self.refresh_view()
        self.statusBar().showMessage(f'{component.name}: nominal temperature updated')

    def set_surface_emissivity(self,component,face_key,text):
        from .surface_catalog import validate_surface_assignment
        old=component.surface_properties.get(face_key)
        try:
            assignment=dict(old or {})
            if text.strip():
                value=float(text)
                if not math.isfinite(value):raise ValueError('Emissivity must be finite')
                assignment['emissivity']=value
            else:assignment.pop('emissivity',None)
            if assignment:validate_surface_assignment(assignment)
            if assignment:component.surface_properties[face_key]=assignment
            else:component.surface_properties.pop(face_key,None)
            self.project.validate()
        except (ValueError,TypeError) as exc:
            if old is None:component.surface_properties.pop(face_key,None)
            else:component.surface_properties[face_key]=old
            self.statusBar().showMessage('Emissivity not changed: '+str(exc));return
        self.refresh_view()
        self.statusBar().showMessage(f'{component.name}: surface emissivity updated')

    def apply_spacing(self,source_id,target_id,gap_mm):
        from .spacing import set_axial_spacing
        try:
            result=set_axial_spacing(self.project,source_id,target_id,float(gap_mm))
        except (ValueError,KeyError) as exc:
            self.statusBar().showMessage('Spacing unchanged: '+str(exc))
            self.refresh_editor();return False
        self.refresh_editor();self.refresh_view();self.refresh_tree()
        self.statusBar().showMessage(f'Set axial separation to {result["signed_axial_mm"]:.4f} mm; nominal design updated')
        return True

    def edit_optical_spacings(self):
        """Expose the optical sequence's complete chain of editable, signed gaps."""
        from .spacing import optical_order,mechanical_order,spacing_measure
        order=optical_order(self.project)
        if len(order)<2 and len(mechanical_order(self.project))<2:
            self.show_readable_result('Element separations','At least two components are required.')
            return
        lookup={c.id:c for c in self.project.components}
        dialog=QDialog(self);dialog.setWindowTitle('Optical element separations — nominal geometry')
        dialog.resize(1100,560)
        dialog.setStyleSheet('QDialog{background:#1c2b38;color:#ffffff;} '
                             'QLabel{color:#ffffff;} QTableWidget{background:#122532;color:#ffffff;} '
                             'QPushButton{background:#294254;color:#ffffff;padding:8px;}')
        layout=QVBoxLayout(dialog)
        note=QLabel('Optical scope follows the optical path (or descending world Z if no path exists); '
                    'physical scope includes non-assembly components ordered by world Z. '
                    'Axial distance is signed along the first element’s local +Z; '
                    'the 3D center distance is always nonnegative. These are coordinate-frame separations, '
                    'not lens surface gaps or post-reflection Zemax thicknesses.')
        note.setWordWrap(True);layout.addWidget(note)
        columns=['From element','To element','Axial ΔZ (mm) — editable',
                 'Transverse (mm)','3D center (mm)','Constraint']
        scope=QComboBox()
        scope.addItem('Optical path elements', 'optical')
        scope.addItem('All physical components (world Z order)', 'mechanical')
        layout.addWidget(scope)
        table=QTableWidget(len(order)-1,len(columns));table.setHorizontalHeaderLabels(columns)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(table,1)
        pairs=list(zip(order,order[1:]))
        def populate():
            nonlocal pairs
            current_order=(mechanical_order(self.project) if scope.currentData()=='mechanical'
                           else optical_order(self.project))
            pairs=list(zip(current_order,current_order[1:]))
            table.setRowCount(len(pairs))
            for row,(source_id,target_id) in enumerate(pairs):
                measure=spacing_measure(self.project,source_id,target_id)
                rule=next((c for c in self.project.constraints if c['target_id']==target_id),None)
                editable=rule is None or (rule['type']=='axial_gap' and rule['source_id']==source_id)
                data=[lookup[source_id].name,lookup[target_id].name,
                      f'{measure["signed_axial_mm"]:.5f}',f'{measure["transverse_mm"]:.5f}',
                      f'{measure["center_distance_mm"]:.5f}',
                      (rule['type'] if rule else 'Free placement')]
                for col,value in enumerate(data):
                    item=QTableWidgetItem(value)
                    if col!=2 or not editable:item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    if col==2 and not editable:item.setToolTip('Target controlled by another constraint')
                    table.setItem(row,col,item)
        def save_row():
            row=table.currentRow()
            if row<0:
                self.statusBar().showMessage('Select a spacing row first');return
            source_id,target_id=pairs[row]
            try:
                value=float(table.item(row,2).text())
                if not self.apply_spacing(source_id,target_id,value):return
            except (ValueError,TypeError) as exc:
                self.show_readable_result('Invalid spacing',str(exc))
            populate()
        def save_all():
            # Apply in displayed chain order. Each operation is individually transactional.
            for row,(source_id,target_id) in enumerate(pairs):
                try:
                    value=float(table.item(row,2).text())
                    if not self.apply_spacing(source_id,target_id,value):break
                except (ValueError,TypeError) as exc:
                    self.show_readable_result('Invalid spacing',f'Row {row+1}: {exc}');break
            populate()
        buttons=QHBoxLayout()
        update_one=QPushButton('Apply selected spacing');update_one.clicked.connect(save_row)
        update_all=QPushButton('Apply all edited spacings');update_all.clicked.connect(save_all)
        refresh=QPushButton('Refresh measurements');refresh.clicked.connect(populate)
        close=QPushButton('Close');close.clicked.connect(dialog.accept)
        for button in (update_one,update_all,refresh):buttons.addWidget(button)
        buttons.addStretch();buttons.addWidget(close);layout.addLayout(buttons)
        scope.currentIndexChanged.connect(populate)
        populate();dialog.exec()

    def _edit(self,setter,value):
        setter(value)
        try:
            self.project.apply_links()
            apply_constraints(self.project)
            self.project.validate()
        except ValueError as ex:
            self.statusBar().showMessage('Warning: '+str(ex))
        else:self.statusBar().showMessage('Edited • project has unsaved changes')
        self.refresh_view()
        self.refresh_tree()

    def refresh_view(self):
        try:display_project=self.active_project()
        except (ValueError,KeyError) as ex:
            self.statusBar().showMessage('Operating profile invalid: '+str(ex));return
        if self.loaded_results is not None:
            from .results_exchange import geometry_signature
            if geometry_signature(display_project)!=self.results_geometry_signature:
                self.loaded_results=None
                self.results_geometry_signature=None
                self.viewer.external_rays={}
                self.rays_toggle.blockSignals(True)
                self.rays_toggle.setChecked(False);self.rays_toggle.setEnabled(False)
                self.rays_toggle.blockSignals(False)
                self.statusBar().showMessage('Geometry changed; imported results invalidated to prevent a misaligned overlay')
        try:self.viewer.redraw(display_project,self.selected_id)
        except Exception as ex:self.statusBar().showMessage(f'Preview error: {ex}')

    def add(self,kind):
        parent=self.selected()
        c=component(kind,f'New {kind}')
        if parent is not None and parent.kind=='assembly': c.parent_id=parent.id
        self.project.components.append(c);self.selected_id=c.id
        self.refresh_tree();self.refresh_editor();self.refresh_view()

    def attach_step(self):
        path,_=QFileDialog.getOpenFileName(self,'Attach a STEP reference','','STEP files (*.step *.stp)')
        if not path:return
        try:
            from .cad_exchange import make_shape
            trial=component('cad','STEP import check'); trial.cad_file=path
            shape=make_shape(trial)
            if shape is None or shape.isNull():raise ValueError('STEP contained no importable geometry')
        except RuntimeError as ex:
            QMessageBox.warning(self,'CAD engine not installed',str(ex)+'\nThe STEP file will be saved as a reference only.')
        except Exception as ex:
            QMessageBox.warning(self,'STEP geometry unavailable',str(ex)+'\nThe STEP file will be saved as a reference only.')
        c=component('cad',Path(path).stem);c.cad_file=str(Path(path).resolve())
        self.project.components.append(c);self.selected_id=c.id
        self.refresh_tree();self.refresh_editor();self.refresh_view()
        self.statusBar().showMessage('STEP file attached; OCCT geometry is displayed when CadQuery is installed')

    def import_step_assembly(self):
        source,_=QFileDialog.getOpenFileName(self,'Import STEP assembly','','STEP files (*.step *.stp)')
        if not source:return
        directory=QFileDialog.getExistingDirectory(self,'Choose destination parent for extracted part STEP files')
        if not directory:return
        from .step_assembly import split_step_assembly
        target=Path(directory)/(Path(source).stem+'_extracted_parts')
        try:
            imported=split_step_assembly(source,target)
            ancestor=self.selected()
            if ancestor is not None and ancestor.kind=='assembly':
                imported[0].parent_id=ancestor.id
            self.project.components.extend(imported)
            self.project.validate()
        except Exception as ex:
            QMessageBox.critical(self,'STEP assembly import failed',str(ex));return
        self.selected_id=imported[0].id
        self.refresh_tree();self.refresh_editor();self.refresh_view()
        self.statusBar().showMessage(f'Imported {len(imported)} STEP assembly nodes')

    def inspect_faces(self,initial_index=None):
        from .cad_analysis import face_descriptors
        c=self.selected()
        if c is None or c.kind=='assembly':return
        try: descriptors=face_descriptors(c)
        except Exception as ex:
            QMessageBox.warning(self,'Face inspection unavailable',str(ex));return
        if not descriptors:return
        dialog=QDialog(self)
        dialog.setWindowTitle(f'CAD face assignments — {c.name}')
        dialog.resize(650,460)
        dialog.setStyleSheet('QDialog{background:#1c2b38;color:#ffffff;} '
                             'QLabel{color:#ffffff;} QListWidget,QTextEdit{background:#15212c;color:#ffffff;} '
                             'QPushButton{background:#294254;color:#ffffff;padding:6px;}')
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('Select a B-rep face. Its geometry is highlighted in yellow. '
                                'Face numbers can change after CAD edits.'))
        face_list=QListWidget()
        for d in descriptors:
            face_list.addItem(f'Face {d["index"]:03d} • {d["surface_type"]} • '
                              f'Area {d["area_mm2"]:.4f} mm² • {d["signature"]}')
        layout.addWidget(face_list,2)
        layout.addWidget(QLabel('Face optical properties (JSON object; coating, scatter, BRDF file, etc.)'))
        editor=QTextEdit();editor.setMinimumHeight(100)
        layout.addWidget(editor,1)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Close)
        layout.addWidget(buttons)

        def change_face(index):
            if index<0:return
            d=descriptors[index]
            self.viewer.highlight_face=(c.id,d['index'])
            self.refresh_view()
            mapping=c.surface_properties.get(f'face_{d["index"]}',{})
            editor.setPlainText(json.dumps(mapping,indent=2))

        def save_face():
            idx=face_list.currentRow()
            if idx<0:return
            try:
                data=json.loads(editor.toPlainText())
                if not isinstance(data,dict):raise ValueError('Face assignment must be a JSON object')
                from .surface_catalog import validate_surface_assignment
                validate_surface_assignment(data)
            except (ValueError,TypeError) as ex:
                QMessageBox.warning(dialog,'Invalid JSON',str(ex));return
            descriptor=descriptors[idx]
            data['face_signature']=descriptor['signature']
            c.surface_properties[f'face_{descriptor["index"]}']=data
            self.statusBar().showMessage(f'Saved face {descriptor["index"]} assignment for {c.name}')

        face_list.currentRowChanged.connect(change_face)
        buttons.button(QDialogButtonBox.Save).clicked.connect(save_face)
        buttons.rejected.connect(dialog.reject)
        face_list.setCurrentRow(max(0,min(len(descriptors)-1,(initial_index or 1)-1)))
        dialog.exec()
        self.viewer.highlight_face=None
        self.refresh_view();self.refresh_editor()

    def edit_surface_behaviors(self):
        from .surface_catalog import semantic_surfaces,semantic_key,INTERACTIONS,SCATTER_MODELS,validate_surface_assignment
        comp=self.selected()
        if comp is None:return
        surfaces=semantic_surfaces(comp)
        if not surfaces:
            self.show_readable_result('No semantic surfaces',
                'This component has no generated semantic surface names. For imported STEP parts, '
                'use Tools → Inspect and assign CAD faces instead.')
            return
        dlg=QDialog(self);dlg.setWindowTitle('Surface optical/thermal behaviors — '+comp.name)
        dlg.resize(820,690)
        dlg.setStyleSheet('QDialog{background:#1c2b38;color:#ffffff;} QLabel{color:#ffffff;} '
            'QListWidget,QLineEdit,QComboBox{background:#15212c;color:#ffffff;border:1px solid #456070;} '
            'QPushButton{background:#294254;color:#ffffff;padding:7px;}')
        outer=QVBoxLayout(dlg)
        guide=QLabel('Assign behavior to named parametric surfaces. These stable labels are not yet mapped to '
                     'Zemax/OCCT face indices. Every unassigned surface inherits the component label only.')
        guide.setWordWrap(True);outer.addWidget(guide)
        body=QHBoxLayout();outer.addLayout(body,1)
        listing=QListWidget();listing.setMinimumWidth(240);body.addWidget(listing,1)
        editor=QWidget();form=QFormLayout(editor);form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        body.addWidget(editor,2)
        name_label=QLabel();form.addRow('Named surface ID',name_label)
        interaction=QComboBox();interaction.addItems(INTERACTIONS);form.addRow('Optical interaction',interaction)
        coating=QComboBox();coating.setEditable(True)
        coating.addItem('')
        coating.addItems(sorted(self.project.coating_library))
        form.addRow('Coating catalog name',coating)
        scatter=QComboBox();scatter.addItems(SCATTER_MODELS);form.addRow('Scattering model',scatter)
        brdf=QLineEdit();brdf.setPlaceholderText('Absolute or portable BRDF dataset path')
        form.addRow('BRDF data file',brdf)
        numbers={}
        for key,label in [('temperature_K','Surface temperature (K)'),
                          ('emissivity','Spectral/gray emissivity (0–1)'),
                          ('roughness_rms_nm','RMS roughness (nm)'),
                          ('A','ABg coefficient A'),('B','ABg coefficient B'),('g','ABg exponent g')]:
            w=QLineEdit();w.setPlaceholderText('Not assigned')
            numbers[key]=w;form.addRow(label,w)
        source=QLineEdit();source.setPlaceholderText('Reference, measurement or dataset citation')
        form.addRow('Data provenance',source)
        footer=QLabel('Uncalibrated scatter models are annotations only; Zemax-specific BSDF/ABg '
                      'mapping and energy normalization remain unverified.')
        footer.setWordWrap(True);outer.addWidget(footer)
        controls=QHBoxLayout();save=QPushButton('Save this surface');close=QPushButton('Close')
        controls.addWidget(save);controls.addStretch(1);controls.addWidget(close)
        outer.addLayout(controls)
        def refresh_list(selected=None):
            listing.blockSignals(True);listing.clear()
            for item in surfaces:
                assigned=semantic_key(item) in comp.surface_properties
                label=('● ' if assigned else '○ ')+item
                row=QListWidgetItem(label);row.setData(Qt.UserRole,item)
                listing.addItem(row)
            listing.blockSignals(False)
            index=surfaces.index(selected) if selected in surfaces else 0
            listing.setCurrentRow(index)
        def show_surface():
            item=listing.currentItem()
            if item is None:return
            sid=item.data(Qt.UserRole);name_label.setText(sid)
            data=comp.surface_properties.get(semantic_key(sid),{})
            interaction.setCurrentText(data.get('interaction','unspecified'))
            coating.setCurrentText(data.get('coating',''))
            scatter.setCurrentText(data.get('scatter_model','none'))
            brdf.setText(data.get('brdf_file',''))
            source.setText(data.get('source',''))
            for key in ('temperature_K','emissivity','roughness_rms_nm'):
                numbers[key].setText(str(data[key]) if key in data else '')
            params=data.get('scatter_parameters',{})
            for key in ('A','B','g'):
                numbers[key].setText(str(params[key]) if key in params else '')
        def save_surface():
            item=listing.currentItem()
            if item is None:return
            sid=item.data(Qt.UserRole)
            try:
                data={'interaction':interaction.currentText(),
                      'scatter_model':scatter.currentText()}
                if coating.currentText().strip():data['coating']=coating.currentText().strip()
                if brdf.text().strip():data['brdf_file']=brdf.text().strip()
                if source.text().strip():data['source']=source.text().strip()
                for key in ('temperature_K','emissivity','roughness_rms_nm'):
                    if numbers[key].text().strip():data[key]=float(numbers[key].text())
                if data['scatter_model']=='abg':
                    data['scatter_parameters']={key:float(numbers[key].text()) for key in ('A','B','g')}
                validate_surface_assignment(data)
            except (ValueError,TypeError) as ex:
                self.show_readable_result('Invalid surface assignment',str(ex));return
            comp.surface_properties[semantic_key(sid)]=data
            refresh_list(sid)
            self.statusBar().showMessage(f'Saved {sid} behavior on {comp.name}')
        listing.currentItemChanged.connect(lambda *_:show_surface())
        save.clicked.connect(save_surface);close.clicked.connect(dlg.accept)
        refresh_list();dlg.exec()
        self.refresh_editor()

    def check_interferences(self):
        from .cad_analysis import inspect_interferences
        try: result=inspect_interferences(self.project)
        except Exception as ex:
            QMessageBox.warning(self,'Interference check unavailable',str(ex));return
        lines=[f'Evaluated {result["evaluated_solids"]} mechanical solids.',
               f'Volumetric interferences: {len(result["collisions"])}']
        lines.extend(f'{c["first_name"]} ↔ {c["second_name"]}: '
                     f'{c["overlap_mm3"]:.6g} mm³' for c in result['collisions'][:30])
        lines.extend('Could not inspect '+x for x in result['unavailable'])
        QMessageBox.information(self,'B-rep interference report','\n'.join(lines))

    def show_thermo_optical_report(self):
        from .thermo_optics import thermal_report
        try:result=thermal_report(self.active_project())
        except (ValueError,TypeError) as exc:
            self.show_readable_result('Thermo-optical report failed',str(exc));return
        lines=[f'Operating profile: {result["active_operating_profile"] or "Nominal"}',
               'Sampled wavelengths (µm): '+', '.join(f'{w:g}' for w in result['wavelengths_um']),
               f'Temperature-conditioned index samples: {len(result["conditioned_refractive_indices"])}',
               f'Temperature-conditioned coating samples: {len(result["conditioned_coating_rt"])}',
               f'Surfaces with emissivity and temperature: {len(result["surface_thermal_radiance"])}',
               '', 'Temperature-conditioned refractive indices:']
        for sample in result['conditioned_refractive_indices']:
            lines.append(f'{sample["component_name"]}, {sample["wavelength_um"]:g} µm, '
                         f'{sample["temperature_K"]:g} K: n={sample["n"]:.8g}'
                         +(f', k={sample["k"]:.5g}' if sample['k_known'] else ', k unknown'))
        lines.extend(['','Temperature-conditioned coatings (R/T/absorption):'])
        for sample in result['conditioned_coating_rt']:
            lines.append(f'{sample["component_name"]}/{sample["surface"]}, '
                         f'{sample["wavelength_um"]:g} µm: R={sample["reflectance"]:.5g}, '
                         f'T={sample["transmittance"]:.5g}, A={sample["absorptance"]:.5g}')
        lines.extend(['','Local surface spectral radiance (W/m²/sr/µm):'])
        for surface in result['surface_thermal_radiance']:
            emission=', '.join(f'{w:g} µm: {v:.5g}' for w,v in zip(
                result['wavelengths_um'],surface['spectral_exit_radiance_W_m2_sr_um']))
            lines.append(f'{surface["component_name"]}/{surface["surface"]} '
                         f'({surface["temperature_K"]:g} K, ε={surface["emissivity_assumed_gray"]:g}): {emission}')
        lines.extend(['','Missing data / warnings:',*(result['warnings'] or ['None']),
                      '', 'Important limitations:',*result['limitations']])
        self.show_readable_result('Thermo-optical and infrared emission audit','\n'.join(lines))

    def remove(self):
        if any(c.parent_id==self.selected_id for c in self.project.components):
            QMessageBox.warning(self,'Assembly has children','Reparent or remove child components first.')
            return
        self.project.links=[link for link in self.project.links
                            if self.selected_id not in (link['source_id'],link['target_id'])]
        self.project.constraints=[rule for rule in self.project.constraints
                                  if self.selected_id not in (rule['source_id'],rule['target_id'])]
        for profile in self.project.metadata.get('operating_profiles',{}).values():
            profile.get('component_overrides',{}).pop(self.selected_id,None)
        optical=self.project.metadata.get('optical_sequence')
        if optical and self.selected_id in optical['component_ids']:
            optical['component_ids']=[cid for cid in optical['component_ids'] if cid!=self.selected_id]
            if optical.get('stop_component_id')==self.selected_id:
                optical['stop_component_id']=None
            if not optical['component_ids']:
                self.project.metadata.pop('optical_sequence',None)
        self.project.components=[c for c in self.project.components if c.id!=self.selected_id]
        self.selected_id=self.project.components[0].id if self.project.components else None
        self.refresh_tree();self.refresh_editor();self.refresh_view()

    def new(self):
        self.project=cassegrain_template();self.current_path=None
        self.active_profile_name=None;self.refresh_profile_combo()
        self.selected_id=self.project.components[0].id
        self.refresh_tree();self.refresh_editor();self.refresh_view()

    def new_detailed(self):
        self.project=detailed_cassegrain_template();self.current_path=None
        self.active_profile_name=None;self.refresh_profile_combo()
        self.selected_id=self.project.components[0].id
        self.refresh_tree();self.refresh_editor();self.refresh_view()

    def new_opir(self):
        self.project=opir_template();self.current_path=None
        self.active_profile_name=None;self.refresh_profile_combo()
        self.selected_id=self.project.components[0].id
        self.refresh_tree();self.refresh_editor();self.refresh_view()

    def edit_operating_profiles(self):
        """Configure thermal what-if scenarios without modifying nominal geometry."""
        from .operating_profiles import resolve_profile
        dialog=QDialog(self)
        dialog.setWindowTitle('Operating configurations — first-order geometry preview')
        dialog.resize(1050,650)
        dialog.setStyleSheet('QDialog{background:#192c39;color:#eef6fa;} '
                             'QLabel{color:#eef6fa;} QTableWidget,QComboBox,QDoubleSpinBox{'
                             'background:#102330;color:#eef6fa;gridline-color:#3e5c70;} '
                             'QPushButton{background:#29516a;color:white;padding:7px;}')
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('Preview temperatures, linear material expansion and explicit local pose offsets. '
                                'The saved nominal design is never overwritten.'))
        header=QHBoxLayout();chooser=QComboBox();header.addWidget(QLabel('Configuration:'))
        header.addWidget(chooser,1);new_button=QPushButton('New configuration')
        header.addWidget(new_button);layout.addLayout(header)
        reference=QDoubleSpinBox();default=QDoubleSpinBox()
        for widget in (reference,default):
            widget.setRange(1.0,3000.0);widget.setDecimals(3);widget.setSuffix(' K')
        temperatures=QHBoxLayout();temperatures.addWidget(QLabel('Reference temperature:'))
        temperatures.addWidget(reference);temperatures.addWidget(QLabel('Default component temperature:'))
        temperatures.addWidget(default);temperatures.addStretch();layout.addLayout(temperatures)
        layout.addWidget(QLabel('Leave temperature empty to use the default. Offsets are in the component’s local frame.'))
        columns=['Component','Temperature (K)','ΔX (mm)','ΔY (mm)','ΔZ (mm)',
                 'ΔRx (°)','ΔRy (°)','ΔRz (°)']
        table=QTableWidget(len(self.project.components),len(columns));table.setHorizontalHeaderLabels(columns)
        table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        for col in range(1,len(columns)):table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeToContents)
        for row,comp in enumerate(self.project.components):
            item=QTableWidgetItem(comp.name);item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row,0,item)
            for col in range(1,len(columns)):table.setItem(row,col,QTableWidgetItem(''))
        layout.addWidget(table,1)
        buttons=QHBoxLayout();save_button=QPushButton('Save configuration')
        preview_button=QPushButton('Preview and report');close_button=QPushButton('Close')
        buttons.addWidget(save_button);buttons.addWidget(preview_button)
        buttons.addStretch();buttons.addWidget(close_button);layout.addLayout(buttons)

        def refill():
            selected=chooser.currentData()
            chooser.blockSignals(True);chooser.clear()
            for name in sorted(self.project.metadata.get('operating_profiles',{})):
                chooser.addItem(name,name)
            idx=chooser.findData(selected)
            chooser.setCurrentIndex(max(0,idx));chooser.blockSignals(False)

        def load_profile():
            name=chooser.currentData()
            if not name:return
            record=self.project.metadata['operating_profiles'][name]
            reference.setValue(record['reference_temperature_K'])
            default.setValue(record['default_temperature_K'])
            for row,comp in enumerate(self.project.components):
                override=record.get('component_overrides',{}).get(comp.id,{})
                position=override.get('position_offset_mm',[0,0,0])
                rotation=override.get('rotation_offset_deg',[0,0,0])
                values=[override.get('temperature_K',''),*position,*rotation]
                for col,value in enumerate(values,1):
                    table.item(row,col).setText('' if value=='' else str(value))

        def add_profile():
            name,ok=QInputDialog.getText(dialog,'New operating configuration','Name:')
            if not ok:return
            name=name.strip()
            profiles=self.project.metadata.setdefault('operating_profiles',{})
            if not name or name in profiles:
                self.show_readable_result('Invalid name','Choose a nonempty, unique configuration name.')
                return
            profiles[name]={'reference_temperature_K':293.15,'default_temperature_K':293.15,
                            'component_overrides':{}}
            refill();chooser.setCurrentIndex(chooser.findData(name))

        def save_profile():
            name=chooser.currentData()
            if not name:return False
            try:
                overrides={}
                for row,comp in enumerate(self.project.components):
                    cells=[table.item(row,col).text().strip() for col in range(1,8)]
                    record={}
                    if cells[0]:record['temperature_K']=float(cells[0])
                    pos=[float(value) if value else 0.0 for value in cells[1:4]]
                    rot=[float(value) if value else 0.0 for value in cells[4:7]]
                    if any(pos):record['position_offset_mm']=pos
                    if any(rot):record['rotation_offset_deg']=rot
                    if record:overrides[comp.id]=record
                new_record={'reference_temperature_K':reference.value(),
                            'default_temperature_K':default.value(),
                            'component_overrides':overrides}
                previous=self.project.metadata['operating_profiles'][name]
                self.project.metadata['operating_profiles'][name]=new_record
                try:self.project.validate()
                except Exception:
                    self.project.metadata['operating_profiles'][name]=previous
                    raise
                self.active_profile_name=name;self.refresh_profile_combo()
                self.refresh_editor();self.refresh_view()
                self.statusBar().showMessage(f'Saved configuration: {name} (nominal model unchanged)')
                return True
            except (ValueError,TypeError,KeyError) as exc:
                self.show_readable_result('Operating configuration invalid',str(exc))
                return False

        def preview():
            if not save_profile():return
            try:_,report=resolve_profile(self.project,self.active_profile_name)
            except (ValueError,TypeError) as exc:
                self.show_readable_result('Preview unavailable',str(exc));return
            detail=[f'Configuration: {report["profile_name"]}',
                    f'Components with modified dimensions or overrides: {len(report["components_changed"])}',
                    '','Component changes:']
            detail.extend(f'{c["component_name"]}: {c["temperature_K"]:.2f} K, '
                          f'linear size ×{c["linear_scale"]:.8f}'
                          for c in report['components_changed'])
            detail.extend(['','Limitations and warnings:',*report['warnings']])
            self.show_readable_result('Operating configuration preview','\n'.join(detail))

        chooser.currentIndexChanged.connect(load_profile)
        new_button.clicked.connect(add_profile)
        save_button.clicked.connect(save_profile)
        preview_button.clicked.connect(preview)
        close_button.clicked.connect(dialog.accept)
        refill()
        if chooser.count():load_profile()
        dialog.exec()

    def edit_links(self):
        description=('JSON list of parameter links. Each item needs source_id, source_param, '
                     'target_id, target_param, scale, offset.\n'
                     'Use component IDs from the saved project. Linked targets update when any value changes.')
        entry,ok=QInputDialog.getMultiLineText(self,'Edit parameter dependencies',description,
                                                json.dumps(self.project.links,indent=2))
        if not ok:return
        before=self.project.links
        try:
            self.project.links=json.loads(entry)
            if not isinstance(self.project.links,list): raise ValueError('Expected a JSON list')
            self.project.validate()
            self.project.apply_links()
            self.project.validate()
        except (ValueError,KeyError,TypeError) as ex:
            self.project.links=before
            QMessageBox.warning(self,'Invalid parameter links',str(ex))
            return
        self.refresh_editor();self.refresh_view()

    def edit_constraints(self):
        dialog=QDialog(self)
        dialog.setWindowTitle('Assembly placement constraints')
        dialog.setStyleSheet('QDialog{background:#1c2b38;color:#ffffff;} '
                             'QLabel{color:#ffffff;} QListWidget,QComboBox,QDoubleSpinBox{background:#15212c;color:#ffffff;} '
                             'QPushButton{background:#294254;color:#ffffff;padding:7px;}')
        dialog.resize(570,500)
        main=QVBoxLayout(dialog)
        main.addWidget(QLabel('One driving relationship per target. Source and target use their world coordinate frames.'))
        existing=QListWidget()
        main.addWidget(existing)
        form=QFormLayout()
        source=QComboBox();target=QComboBox();type_box=QComboBox()
        for c in self.project.components:
            source.addItem(c.name,c.id);target.addItem(c.name,c.id)
        for typ in ('coincident','coaxial','axial_gap','relative_pose'):type_box.addItem(typ)
        form.addRow('Source',source);form.addRow('Target',target);form.addRow('Constraint',type_box)
        gap=QDoubleSpinBox();gap.setRange(-1e6,1e6);gap.setDecimals(4);gap.setValue(100)
        form.addRow('Axial gap (mm)',gap)
        offset=[];rot=[]
        for axis in 'XYZ':
            w=QDoubleSpinBox();w.setRange(-1e6,1e6);w.setDecimals(4)
            form.addRow(f'Offset {axis} (mm)',w);offset.append(w)
        for axis in 'XYZ':
            w=QDoubleSpinBox();w.setRange(-360,360);w.setDecimals(3)
            form.addRow(f'Rotate {axis} (°)',w);rot.append(w)
        main.addLayout(form)
        controls=QHBoxLayout()
        add_button=QPushButton('Add constraint');remove_button=QPushButton('Remove selected');close_button=QPushButton('Close')
        for button in (add_button,remove_button,close_button):controls.addWidget(button)
        main.addLayout(controls)

        def repaint_list():
            existing.clear()
            names={c.id:c.name for c in self.project.components}
            for item in self.project.constraints:
                existing.addItem(f'{names.get(item["source_id"],"?")} → '
                                 f'{names.get(item["target_id"],"?")}: {item["type"]}')

        def commit(candidate):
            original=self.project.constraints
            state={c.id:(list(c.position),list(c.rotation)) for c in self.project.components}
            try:
                self.project.constraints=candidate
                self.project.validate()
                apply_constraints(self.project)
            except (ValueError,TypeError,KeyError) as ex:
                self.project.constraints=original
                for c in self.project.components:
                    c.position,c.rotation=state[c.id]
                QMessageBox.warning(dialog,'Invalid constraint',str(ex));return
            repaint_list();self.refresh_view();self.refresh_editor()

        def add_rule():
            kind=type_box.currentText()
            rule=dict(type=kind,source_id=source.currentData(),target_id=target.currentData())
            if kind=='axial_gap':rule['gap_mm']=gap.value()
            if kind=='relative_pose':
                rule['offset_mm']=[w.value() for w in offset]
                rule['rotation_deg']=[w.value() for w in rot]
            commit(self.project.constraints+[rule])

        def remove_rule():
            idx=existing.currentRow()
            if idx>=0:commit([item for i,item in enumerate(self.project.constraints) if i!=idx])

        add_button.clicked.connect(add_rule)
        remove_button.clicked.connect(remove_rule)
        close_button.clicked.connect(dialog.accept)
        repaint_list();dialog.exec()

    def edit_materials(self):
        guide=('Material library JSON: map each material name to density_kg_m3, cte_per_K, '
               'spectral_constants ([{wavelength_um,n,k}]), coating data source, etc.\n'
               'Values must come from measurements or authoritative references.')
        data,ok=QInputDialog.getMultiLineText(self,'Edit material-property library',guide,
                                              json.dumps(self.project.material_library,indent=2))
        if not ok:return
        previous=self.project.material_library
        try:
            self.project.material_library=json.loads(data)
            if not isinstance(self.project.material_library,dict): raise ValueError('Expected a JSON object')
            self.project.validate()
        except (ValueError,TypeError,KeyError) as ex:
            self.project.material_library=previous
            QMessageBox.warning(self,'Invalid material library',str(ex));return
        self.statusBar().showMessage('Material-property library updated')

    def edit_optical_path(self):
        from .sequential import default_sequence,validate_sequence,OPTICAL_KINDS
        dlg=QDialog(self)
        dlg.setWindowTitle('Optical path — explicit authoring order')
        dlg.resize(610,580)
        dlg.setStyleSheet('QDialog{background:#1c2b38;color:#ffffff;} '
            'QLabel{color:#ffffff;} QListWidget,QLineEdit,QComboBox{background:#15212c;color:#ffffff;} '
            'QPushButton{background:#294254;color:#ffffff;padding:7px;}')
        outer=QVBoxLayout(dlg)
        intro=QLabel('Select the elements traversed by light, in order. This is a draft prescription; '
            'reflective folds and coordinate breaks must be established in Zemax.')
        intro.setWordWrap(True);outer.addWidget(intro)
        saved=self.project.metadata.get('optical_sequence',{})
        original=list(saved.get('component_ids',default_sequence(self.project)))
        candidates=[c for c in self.project.components if c.kind in OPTICAL_KINDS]
        ordered=original+[c.id for c in candidates if c.id not in original]
        lookup={c.id:c for c in candidates}
        listing=QListWidget();outer.addWidget(listing,1)
        for cid in ordered:
            if cid not in lookup:continue
            c=lookup[cid]
            item=QListWidgetItem(f'{c.name}  [{c.kind}]')
            item.setData(Qt.UserRole,cid)
            item.setFlags(item.flags()|Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if cid in original else Qt.Unchecked)
            listing.addItem(item)
        buttons=QHBoxLayout()
        up=QPushButton('Move earlier ↑');down=QPushButton('Move later ↓')
        buttons.addWidget(up);buttons.addWidget(down);outer.addLayout(buttons)
        def move(delta):
            row=listing.currentRow();other=row+delta
            if row<0 or other<0 or other>=listing.count():return
            item=listing.takeItem(row);listing.insertItem(other,item);listing.setCurrentRow(other)
        up.clicked.connect(lambda:move(-1));down.clicked.connect(lambda:move(1))
        form=QFormLayout()
        wavelengths=QLineEdit(', '.join(str(v) for v in saved.get('wavelengths_um',[4.0])))
        wavelengths.setPlaceholderText('3.0, 4.0, 5.0')
        form.addRow('Wavelengths (µm; ascending)',wavelengths)
        fields=QLineEdit('; '.join(f'{x}, {y}' for x,y in saved.get('field_angles_deg',[[0.,0.]])))
        fields.setPlaceholderText('0, 0; 0, 2')
        form.addRow('Field angle pairs X,Y (deg; semicolon-separated)',fields)
        stop=QComboBox();stop.addItem('Unspecified — choose in OpticStudio',None)
        for c in candidates:
            if c.kind=='aperture':stop.addItem(c.name,c.id)
        index=stop.findData(saved.get('stop_component_id'))
        if index>=0:stop.setCurrentIndex(index)
        form.addRow('Stop aperture',stop)
        outer.addLayout(form)
        info=QLabel('A lens contributes two surfaces (front and back). Distances between '
            'surface origins are geometric distances, not signed LDE thicknesses.')
        info.setWordWrap(True);outer.addWidget(info)
        controls=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        outer.addWidget(controls)
        controls.rejected.connect(dlg.reject)
        def commit():
            try:
                values={
                    'component_ids':[listing.item(i).data(Qt.UserRole) for i in range(listing.count())
                                      if listing.item(i).checkState()==Qt.Checked],
                    'wavelengths_um':[float(v.strip()) for v in wavelengths.text().split(',')],
                    'field_angles_deg':[[float(v.strip()) for v in pair.split(',')]
                                        for pair in fields.text().split(';')],
                    'stop_component_id':stop.currentData(),
                }
                validate_sequence(self.project,values)
            except (ValueError,TypeError) as ex:
                self.show_readable_result('Optical path requires correction',str(ex));return
            self.project.metadata['optical_sequence']=values
            self.statusBar().showMessage('Optical path and wavelengths saved in project')
            dlg.accept()
        controls.accepted.connect(commit)
        dlg.exec()

    def browse_material_catalog(self):
        from .catalog import MATERIAL_CANDIDATES,candidate_record,import_spectral_csv,import_library_json
        dialog=QDialog(self)
        dialog.setWindowTitle('Optical and structural material catalog')
        dialog.resize(680,540)
        dialog.setStyleSheet('QDialog{background:#1c2b38;color:#ffffff;} '
                             'QLabel{color:#ffffff;} QListWidget,QLineEdit,QTextEdit{background:#15212c;color:#ffffff;} '
                             'QPushButton{background:#294254;color:#ffffff;padding:7px;}')
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('Names are reference candidates only until measured or authoritative optical data are imported.'))
        query=QLineEdit();query.setPlaceholderText('Search materials…');layout.addWidget(query)
        entries=QListWidget();layout.addWidget(entries,2)
        detail=QTextEdit();detail.setReadOnly(True);detail.setMinimumHeight(110)
        layout.addWidget(detail,1)
        row=QHBoxLayout()
        for title in ('Add candidate','Import n/k CSV','Import JSON catalog','Assign to component','Close'):
            btn=QPushButton(title);row.addWidget(btn)
            if title=='Add candidate':add_btn=btn
            elif title=='Import n/k CSV':csv_btn=btn
            elif title=='Import JSON catalog':json_btn=btn
            elif title=='Assign to component':assign_btn=btn
            else:btn.clicked.connect(dialog.accept)
        layout.addLayout(row)

        def selected_name():
            item=entries.currentItem()
            return item.text() if item else None

        def reload():
            needle=query.text().strip().lower()
            current=selected_name()
            entries.clear()
            for name in sorted(set(MATERIAL_CANDIDATES)|set(self.project.material_library)):
                if needle in name.lower():entries.addItem(name)
            if current:
                matching=entries.findItems(current,Qt.MatchExactly)
                if matching:entries.setCurrentItem(matching[0])

        def show_details():
            name=selected_name()
            if name is None:detail.clear();return
            record=self.project.material_library.get(name,candidate_record(name) if name in MATERIAL_CANDIDATES else {})
            detail.setPlainText(json.dumps(record,indent=2,ensure_ascii=False))

        def add_candidate():
            name=selected_name()
            if name not in MATERIAL_CANDIDATES:return
            self.project.material_library.setdefault(name,candidate_record(name))
            show_details()

        def csv_import():
            path,_=QFileDialog.getOpenFileName(dialog,'Import optical constants CSV','','CSV (*.csv)')
            if not path:return
            name,ok=QInputDialog.getText(dialog,'Material name','Catalog material identifier:',text=selected_name() or '')
            if not ok or not name.strip():return
            source,ok=QInputDialog.getText(dialog,'Source citation','Measurement/dataset source:')
            if not ok:return
            try:
                record=import_spectral_csv(path,name,source)
                if name in self.project.material_library and self.project.material_library[name].get('spectral_constants'):
                    raise ValueError('Existing spectral data would be replaced; rename the imported material')
                self.project.material_library[name]=record
                self.project.validate()
            except Exception as ex:
                QMessageBox.warning(dialog,'Catalog import failed',str(ex));return
            reload();show_details()

        def json_import():
            path,_=QFileDialog.getOpenFileName(dialog,'Import material catalog JSON','','JSON (*.json)')
            if not path:return
            try:
                data=import_library_json(path)
                overlapping=set(data)&set(self.project.material_library)
                if overlapping:raise ValueError('Duplicate materials must be renamed: '+', '.join(sorted(overlapping)))
                self.project.material_library.update(data)
                self.project.validate()
            except Exception as ex:
                QMessageBox.warning(dialog,'Catalog import failed',str(ex));return
            reload();show_details()

        def assign():
            name=selected_name();part=self.selected()
            if not name or part is None:return
            self.project.material_library.setdefault(name,candidate_record(name) if name in MATERIAL_CANDIDATES else {})
            part.material=name
            self.refresh_editor()
            self.statusBar().showMessage('Assigned '+name+' to '+part.name+'; verify optical data before analysis')

        query.textChanged.connect(reload)
        entries.currentItemChanged.connect(lambda current,previous:show_details())
        add_btn.clicked.connect(add_candidate)
        csv_btn.clicked.connect(csv_import)
        json_btn.clicked.connect(json_import)
        assign_btn.clicked.connect(assign)
        reload();dialog.exec()

    def manage_catalog(self,kind):
        """Sourced record editor with schema validation and explicit assignment."""
        from .catalog import MATERIAL_CANDIDATES,candidate_record,import_spectral_csv,import_library_json
        library=self.project.material_library if kind=='material' else self.project.coating_library
        dialog=QDialog(self)
        dialog.setWindowTitle(kind.capitalize()+' catalog manager')
        dialog.resize(920,650)
        dialog.setStyleSheet('QDialog{background:#1c2b38;color:#f2f7fa;} '
            'QLabel{color:#f2f7fa;} QListWidget,QLineEdit,QTextEdit,QComboBox{background:#15212c;color:#ffffff;'
            'border:1px solid #456070;} QPushButton{background:#294254;color:#ffffff;padding:7px;}')
        outer=QVBoxLayout(dialog)
        notice=QLabel('Records are user-supplied and unverified until independently checked against an authoritative source. '
                      'No optical constants are fabricated.')
        notice.setWordWrap(True);outer.addWidget(notice)
        search=QLineEdit();search.setPlaceholderText('Filter catalog names…');outer.addWidget(search)
        body=QHBoxLayout();outer.addLayout(body,1)
        items=QListWidget();items.setMinimumWidth(220);body.addWidget(items,1)
        editor=QWidget();form=QFormLayout(editor);form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        body.addWidget(editor,2)
        name=QLineEdit();source=QLineEdit();category=QLineEdit();status=QLineEdit()
        for label,widget in [('Record name',name),('Source / citation',source),
                             ('Category / purpose',category),('Verification status',status)]:
            form.addRow(label,widget)
        physical={}
        if kind=='material':
            for key,label in [('density_kg_m3','Density (kg/m³)'),('cte_per_K','CTE (1/K)'),
                              ('thermal_conductivity_W_mK','Thermal conductivity (W/m·K)'),
                              ('youngs_modulus_Pa','Young’s modulus (Pa)'),
                              ('dn_dT_per_K','Sourced dn/dT (1/K)'),
                              ('thermo_optic_reference_temperature_K','Index reference temperature (K)')]:
                w=QLineEdit();w.setPlaceholderText('Not supplied')
                physical[key]=w;form.addRow(label,w)
            thermo_source=QLineEdit();thermo_source.setPlaceholderText('Citation for coefficient and valid range')
            thermo_range=QLineEdit();thermo_range.setPlaceholderText('Example: 200, 320 (kelvin)')
            form.addRow('dn/dT provenance',thermo_source)
            form.addRow('dn/dT validity range (K)',thermo_range)
        else:
            for key,label in [('emissivity','Integrated emissivity (0–1)'),
                              ('roughness_rms_nm','RMS roughness (nm)')]:
                w=QLineEdit();w.setPlaceholderText('Not supplied')
                physical[key]=w;form.addRow(label,w)
        spectral_key='spectral_constants' if kind=='material' else 'spectral_rt'
        spectra=QTextEdit();spectra.setMinimumHeight(120)
        form.addRow('Spectral data JSON (µm, '+('n/k' if kind=='material' else 'R/T')+')',spectra)
        if kind=='material':
            model_info=QTextEdit();model_info.setReadOnly(True);model_info.setMaximumHeight(90)
            form.addRow('Bundled dispersion / n-only reference (read-only)',model_info)
            eval_row=QHBoxLayout();eval_wavelength=QLineEdit('4.0');eval_wavelength.setMaximumWidth(100)
            eval_btn=QPushButton('Evaluate refractive index');eval_output=QLabel('')
            eval_row.addWidget(eval_wavelength);eval_row.addWidget(eval_btn);eval_row.addWidget(eval_output,1)
            form.addRow('Wavelength (µm)',eval_row)
        details=QLabel('Material CSV columns: wavelength_um,n,k. Coating JSON samples: '
                       'wavelength_um,reflectance,transmittance.')
        details.setWordWrap(True);form.addRow(details)
        controls=QHBoxLayout();outer.addLayout(controls)
        buttons={}
        for label in (['New record','Save record','Assign selected','Import JSON','Import n/k CSV',
                       'Export JSON','Close'] if kind=='material' else
                      ['New record','Save record','Assign selected','Import JSON','Export JSON','Close']):
            button=QPushButton(label);controls.addWidget(button);buttons[label]=button
        current=[None]
        def repaint(selected=None):
            needle=search.text().strip().casefold()
            items.blockSignals(True);items.clear()
            candidates=set(MATERIAL_CANDIDATES) if kind=='material' else set()
            for title in sorted(set(library)|candidates):
                if needle in title.casefold():items.addItem(title)
            items.blockSignals(False)
            if selected:
                matches=items.findItems(selected,Qt.MatchExactly)
                if matches:items.setCurrentItem(matches[0])
        def display():
            title=items.currentItem().text() if items.currentItem() else None
            current[0]=title
            record=library.get(title,{}) if title else {}
            if title and not record and title in MATERIAL_CANDIDATES:record=candidate_record(title)
            name.setText(title or '')
            source.setText(str(record.get('source','')))
            category.setText(str(record.get('category','')))
            status.setText(str(record.get('status','user_supplied_unverified')))
            for key,widget in physical.items():
                widget.setText(str(record[key]) if key in record else '')
            spectra.setPlainText(json.dumps(record.get(spectral_key,[]),indent=2))
            if kind=='material':
                thermo_source.setText(str(record.get('thermo_optic_source','')))
                thermo_range.setText(', '.join(str(v) for v in record['thermo_optic_temperature_range_K'])
                                     if 'thermo_optic_temperature_range_K' in record else '')
                relevant={k:record[k] for k in ('dispersion','refractive_index_samples','source_url','note') if k in record}
                model_info.setPlainText(json.dumps(relevant,indent=2))
                eval_output.setText('')
        if kind=='material':
            def evaluate():
                from .catalog import interpolate_nk
                title=name.text().strip()
                record=library.get(title)
                if record is None:
                    eval_output.setText('Save/select a catalog record first');return
                try:
                    n,k=interpolate_nk(record,float(eval_wavelength.text()))
                    eval_output.setText(f'n = {n:.8f}; '+(f'k = {k:.4g}' if k is not None else 'k not supplied'))
                except (ValueError,TypeError,KeyError) as ex:
                    eval_output.setText(str(ex))
            eval_btn.clicked.connect(evaluate)
        def save_record():
            title=name.text().strip()
            if not title:
                self.show_readable_result('Catalog record incomplete','A record name is required.');return
            if title!=current[0] and title in library:
                self.show_readable_result('Catalog name in use','Choose a different name to avoid overwriting an existing record.');return
            try:
                series=json.loads(spectra.toPlainText() or '[]')
                if not isinstance(series,list):raise ValueError('Spectral samples must be a JSON array')
                record=dict(library.get(current[0],{}))
                record.update(source=source.text().strip(),category=category.text().strip(),
                              status=status.text().strip() or 'user_supplied_unverified')
                record[spectral_key]=series
                for key,widget in physical.items():
                    if widget.text().strip():record[key]=float(widget.text())
                    else:record.pop(key,None)
                if kind=='material':
                    if thermo_source.text().strip():record['thermo_optic_source']=thermo_source.text().strip()
                    else:record.pop('thermo_optic_source',None)
                    if thermo_range.text().strip():
                        bounds=[float(x.strip()) for x in thermo_range.text().split(',')]
                        record['thermo_optic_temperature_range_K']=bounds
                    else:record.pop('thermo_optic_temperature_range_K',None)
                from .catalog import validate_physical_record
                validate_physical_record(record,kind)
                old=dict(library)
                library[title]=record
                if current[0] and title!=current[0] and current[0] in library:
                    # Renaming a record is blocked when it is assigned anywhere.
                    in_use=any((c.material if kind=='material' else c.coating)==current[0]
                               for c in self.project.components)
                    if in_use:raise ValueError('Cannot rename an assigned record; create a separate record instead')
                    library.pop(current[0])
                self.project.validate()
            except (ValueError,TypeError,KeyError) as exc:
                if 'old' in locals():library.clear();library.update(old)
                self.show_readable_result('Catalog validation failed',str(exc));return
            repaint(title);self.statusBar().showMessage(f'Saved {kind} record: {title}')
        def new_record():
            items.clearSelection();items.setCurrentRow(-1);current[0]=None
            name.clear();source.clear();category.clear();status.setText('user_supplied_unverified')
            for widget in physical.values():widget.clear()
            spectra.setPlainText('[]')
            if kind=='material':
                model_info.clear();eval_output.clear();thermo_source.clear();thermo_range.clear()
        def assign():
            part=self.selected();title=name.text().strip()
            if part is None or title not in library:
                self.show_readable_result('Assign record','Save a catalog record and select a component first.');return
            if kind=='material':part.material=title
            else:part.coating=title
            self.refresh_editor();self.statusBar().showMessage(f'Assigned {title} to {part.name}')
        def import_json():
            path,_=QFileDialog.getOpenFileName(dialog,'Import '+kind+' catalog','','JSON (*.json)')
            if not path:return
            try:
                data=import_library_json(path) if kind=='material' else json.loads(Path(path).read_text())
                if not isinstance(data,dict) or not all(isinstance(x,dict) for x in data.values()):
                    raise ValueError('Catalog must be a JSON object of named records')
                from .catalog import validate_physical_record
                for record in data.values():validate_physical_record(record,kind)
                dup=set(data)&set(library)
                if dup:raise ValueError('Existing names cannot be overwritten: '+', '.join(sorted(dup)))
                before=dict(library);library.update(data);self.project.validate()
            except (ValueError,TypeError,KeyError) as ex:
                if 'before' in locals():library.clear();library.update(before)
                self.show_readable_result('Catalog import failed',str(ex));return
            repaint();self.statusBar().showMessage(f'Imported {len(data)} {kind} records')
        def import_csv():
            path,_=QFileDialog.getOpenFileName(dialog,'Import measured n/k spectra','','CSV (*.csv)')
            if not path:return
            title,ok=QInputDialog.getText(dialog,'Material name','Unique material name:')
            if not ok or not title.strip():return
            citation,ok=QInputDialog.getText(dialog,'Source','Dataset source / citation:')
            if not ok:return
            inserted=False
            try:
                if title in library:raise ValueError('Material already exists')
                record=import_spectral_csv(path,title,citation)
                library[title]=record;inserted=True;self.project.validate()
            except (ValueError,TypeError,KeyError) as ex:
                if inserted:library.pop(title,None)
                self.show_readable_result('CSV import failed',str(ex));return
            repaint(title)
        def export_json():
            path,_=QFileDialog.getSaveFileName(dialog,'Export '+kind+' catalog',kind+'_catalog.json','JSON (*.json)')
            if path:Path(path).write_text(json.dumps(library,indent=2),encoding='utf-8')
        search.textChanged.connect(lambda _:repaint())
        items.currentItemChanged.connect(lambda *_:display())
        buttons['New record'].clicked.connect(new_record)
        buttons['Save record'].clicked.connect(save_record)
        buttons['Assign selected'].clicked.connect(assign)
        buttons['Import JSON'].clicked.connect(import_json)
        if kind=='material':buttons['Import n/k CSV'].clicked.connect(import_csv)
        buttons['Export JSON'].clicked.connect(export_json)
        buttons['Close'].clicked.connect(dialog.accept)
        repaint();dialog.exec()

    def edit_coatings(self):
        guide=('Coating catalog JSON: map coating names to source plus spectral_rt records. '
               'Each record requires wavelength_um, reflectance, transmittance. '
               'Reflectance + transmittance must not exceed 1.')
        data,ok=QInputDialog.getMultiLineText(self,'Coating spectral catalog',guide,
                                              json.dumps(self.project.coating_library,indent=2))
        if not ok:return
        previous=self.project.coating_library
        try:
            self.project.coating_library=json.loads(data)
            if not isinstance(self.project.coating_library,dict):raise ValueError('Expected a JSON object')
            self.project.validate()
        except (ValueError,TypeError,KeyError) as ex:
            self.project.coating_library=previous
            QMessageBox.warning(self,'Invalid coating catalog',str(ex));return
        self.statusBar().showMessage('Coating catalog updated')

    def open(self):
        path,_=QFileDialog.getOpenFileName(self,'Open project','','Optical projects (*.json)')
        if not path:return
        try:p=Project.load(path)
        except Exception as ex:QMessageBox.critical(self,'Load failed',str(ex));return
        self.project=p;self.current_path=path
        self.active_profile_name=None;self.refresh_profile_combo()
        self.selected_id=p.components[0].id if p.components else None
        self.refresh_tree();self.refresh_editor();self.refresh_view()

    def save(self):
        path=self.current_path
        if not path:path,_=QFileDialog.getSaveFileName(self,'Save project','optical_project.json','JSON (*.json)')
        if not path:return
        try:self.project.save(path)
        except Exception as ex:QMessageBox.critical(self,'Save failed',str(ex));return
        self.current_path=path;self.statusBar().showMessage(f'Saved {path}')

    def export(self):
        path,_=QFileDialog.getSaveFileName(self,'Export Zemax handoff manifest','zemax_handoff.json','JSON (*.json)')
        if not path:return
        try:export_manifest(self.active_project(),path)
        except Exception as ex:QMessageBox.critical(self,'Export failed',str(ex));return
        QMessageBox.information(self,'Export complete',
            'Wrote the versioned handoff manifest. This is not a .ZMX/.ZOS file and does not yet execute a Zemax simulation.')

    def export_sequential(self):
        from .sequential import export_sequential_prescription
        if 'optical_sequence' not in self.project.metadata:
            self.edit_optical_path()
        if 'optical_sequence' not in self.project.metadata:return
        path,_=QFileDialog.getSaveFileName(self,'Export draft optical prescription',
                                            'optical_prescription_draft.json','JSON (*.json)')
        if not path:return
        try:
            result=export_sequential_prescription(self.active_project(),path)
        except Exception as ex:
            self.show_readable_result('Prescription export failed',str(ex));return
        self.show_readable_result('Draft optical prescription exported',
            f'Wrote {len(result["surfaces"])} surface records to {path}.\n\n'
            'This is a solver-neutral draft. Coordinate breaks and material mappings '
            'must be verified before a Zemax simulation.')

    def export_cad(self):
        target=QFileDialog.getExistingDirectory(self,'Choose an EMPTY export directory parent')
        if not target:return
        path=Path(target)/'OpticalSatelliteStudio_STEP_Export'
        try:
            from .cad_exchange import export_cad_package
            manifest=export_cad_package(self.active_project(),path,nominal_project=self.project)
        except Exception as ex:
            QMessageBox.critical(self,'CAD export failed',str(ex));return
        from .zemax_plan import audit_package
        report=audit_package(path)
        self.show_readable_result('CAD package exported',
            f'Wrote {len(manifest["cad_parts"])} individual STEP parts and one complete assembly STEP to {path}.\n'
            f'CAD audit: {report["status"]}; {len(report["blockers"])} blockers; '
            f'{len(report["surface_assignments_review"])} surface assignments need mapping.\n'
            'A full audit is included as zemax_cad_audit.json. This is not an optical simulation.')

    def audit_zemax_package(self):
        folder=QFileDialog.getExistingDirectory(self,'Select an exported STEP/Zemax package folder')
        if not folder:return
        try:
            from .zemax_plan import audit_package
            report=audit_package(folder)
        except (ValueError,OSError,KeyError,TypeError) as exc:
            self.show_readable_result('Zemax package audit failed',str(exc));return
        lines=[f'Status: {report["status"]}',
               f'Planned CAD objects: {len(report["nsc_objects"])}',
               f'Unmapped face assignments: {len(report["surface_assignments_review"])}',
               f'Blockers: {len(report["blockers"])}',
               *report['blockers'],'', 'Important limitations:',*report['warnings']]
        self.show_readable_result('Zemax handoff audit','\n'.join(lines))

    def import_external_results(self):
        folder=QFileDialog.getExistingDirectory(self,'Select external results package folder')
        if not folder:return
        try:
            from .results_exchange import load_results_package,geometry_signature
            results=load_results_package(folder,self.active_project())
        except (ValueError,TypeError,KeyError,OSError) as ex:
            self.show_readable_result('Result import failed',str(ex));return
        self.loaded_results=results
        self.results_geometry_signature=geometry_signature(self.active_project())
        self.rays_toggle.setEnabled(bool(results['rays']))
        self.rays_toggle.setChecked(bool(results['rays']))
        self.refresh_view()
        summary=(f'Imported {len(results["rays"])} ray tracks and '
                 f'{"a detector map" if results["detector"] is not None else "no detector map"}.\n'
                 'These are externally supplied results; the editor has not performed ray tracing.\n'
                 + '\n'.join(results['warnings']))
        self.show_readable_result('External optical results imported',summary)

    def view_detector_map(self):
        if self.loaded_results is None or self.loaded_results['detector'] is None:
            self.show_readable_result('No detector map imported',
                'Use Tools → Import external optical results with a detector CSV first.')
            return
        data=self.loaded_results['detector']
        dialog=QDialog(self);dialog.setWindowTitle('External detector irradiance — W/mm²')
        dialog.resize(820,620)
        dialog.setStyleSheet('QDialog{background:#1c2b38;color:#ffffff;} '
                             'QLabel{color:#ffffff;} QPushButton{background:#294254;color:#ffffff;padding:7px;}')
        layout=QVBoxLayout(dialog)
        figure=Figure(figsize=(7,5),facecolor='#101820')
        ax=figure.add_subplot(111);ax.set_facecolor('#101820')
        plot=ax.imshow(data,origin='lower',interpolation='nearest',cmap='inferno')
        ax.set_xlabel('Pixel X');ax.set_ylabel('Pixel Y')
        ax.tick_params(colors='white');ax.xaxis.label.set_color('white');ax.yaxis.label.set_color('white')
        cb=figure.colorbar(plot,ax=ax);cb.set_label('W/mm²',color='white');cb.ax.tick_params(colors='white')
        figure.tight_layout()
        layout.addWidget(FigureCanvasQTAgg(figure),1)
        layout.addWidget(QLabel(f'Pixels: {data.shape[1]} × {data.shape[0]} · '
                                f'peak {float(data.max()):.6g} W/mm² · sum {float(data.sum()):.6g} W/mm² (pixel sum)'))
        close=QPushButton('Close');close.clicked.connect(dialog.accept)
        layout.addWidget(close);dialog.exec()

    def measure(self):
        c=self.selected()
        if c is None:return
        choices=[other for other in self.project.components if other.id!=c.id]
        if not choices:return
        labels=[f'{o.name} [{o.id[:6]}]' for o in choices]
        choice,ok=QInputDialog.getItem(self,'Measure center distance',
                                       f'From {c.name} to:',labels,0,False)
        if not ok:return
        other=choices[labels.index(choice)]
        self.show_readable_result('Assembly measurement',
            f'{c.name} → {other.name}: {distance_between(self.project,c.id,other.id):.6f} mm\n'
            'Measured between component coordinate-frame origins, not closest surfaces.')

    def show_readable_result(self,title,message):
        dialog=QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(480)
        dialog.setStyleSheet('QDialog{background:#1c2b38;color:#ffffff;} '
                             'QLabel{color:#ffffff;font-size:13px;} '
                             'QPushButton{background:#294254;color:#ffffff;padding:8px 20px;border:1px solid #456070;}')
        layout=QVBoxLayout(dialog)
        label=QLabel(message);label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def measure_clearance(self):
        from .cad_analysis import measure_clearance,measure_axis_angle,face_descriptors
        c=self.selected()
        if c is None or c.kind=='assembly':return
        choices=[other for other in self.project.components if other.id!=c.id and other.kind!='assembly']
        if not choices:return
        labels=[f'{other.name} [{other.id[:6]}]' for other in choices]
        selection,ok=QInputDialog.getItem(self,'Measure exact CAD separation',
                                           f'From {c.name} to:',labels,0,False)
        if not ok:return
        other=choices[labels.index(selection)]
        scope,ok=QInputDialog.getItem(self,'Select measurement mode','Geometry to measure:',
                                      ['Whole solids','Specific face pair'],0,False)
        if not ok:return
        first_face=second_face=None
        if scope=='Specific face pair':
            try:
                count1=len(face_descriptors(c));count2=len(face_descriptors(other))
                if count1==0 or count2==0:raise ValueError('Both components need actual B-rep faces')
                first_face,ok=QInputDialog.getInt(self,'First face',f'{c.name} face (1–{count1})',1,1,count1)
                if not ok:return
                second_face,ok=QInputDialog.getInt(self,'Second face',f'{other.name} face (1–{count2})',1,1,count2)
                if not ok:return
            except Exception as ex:
                QMessageBox.warning(self,'Face measurement error',str(ex));return
        try:
            result=measure_clearance(self.project,c.id,other.id,first_face,second_face)
            angle=measure_axis_angle(self.project,c.id,other.id)
        except Exception as ex:
            QMessageBox.warning(self,'CAD measurement error',str(ex));return
        lines=[f'Shortest B-rep separation: {result["distance_mm"]:.6f} mm',
               f'Optical/local Z axes angle: {angle:.6f}°',
               f'Point 1: {result["first_point_world_mm"]}',
               f'Point 2: {result["second_point_world_mm"]}']
        if 'overlap_volume_mm3' in result:
            lines.append(f'Penetrating volume: {result["overlap_volume_mm3"]:.6f} mm³')
        self.show_readable_result('Exact CAD measurement','\n'.join(lines))

    def validate_scene(self):
        from .validation import validation_issues
        issues=validation_issues(self.project)
        dialog=QDialog(self)
        dialog.setWindowTitle('Project validation — issue inspector')
        dialog.resize(1000,600)
        dialog.setStyleSheet('QDialog{background:#1c2b38;color:#f2f7fa;} '
            'QLabel{color:#ffffff;} QTableWidget,QLineEdit,QComboBox,QTextEdit{background:#15212c;color:#ffffff;'
            'gridline-color:#456070;} QHeaderView::section{background:#294254;color:#ffffff;padding:7px;} '
            'QPushButton{background:#294254;color:#ffffff;padding:7px;}')
        outer=QVBoxLayout(dialog)
        counts={s:sum(i['severity']==s for i in issues) for s in ('ERROR','TOLERANCE','WARNING')}
        header=QLabel(f'{counts["ERROR"]} errors · {counts["TOLERANCE"]} tolerances · '
                      f'{counts["WARNING"]} warnings · {len(issues)} total')
        outer.addWidget(header)
        filters=QHBoxLayout();outer.addLayout(filters)
        severity=QComboBox();severity.addItems(['All severities','ERROR','TOLERANCE','WARNING'])
        category=QComboBox();category.addItems(['All categories']+sorted({i['category'] for i in issues}))
        query=QLineEdit();query.setPlaceholderText('Search component, property, or issue text…')
        filters.addWidget(severity);filters.addWidget(category);filters.addWidget(query,1)
        table=QTableWidget();table.setColumnCount(4)
        table.setHorizontalHeaderLabels(['Severity','Category','Component','Issue'])
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch)
        outer.addWidget(table,1)
        detail=QTextEdit();detail.setReadOnly(True);detail.setMaximumHeight(110);outer.addWidget(detail)
        row=QHBoxLayout();outer.addLayout(row)
        goto=QPushButton('Select affected component');export_btn=QPushButton('Export report JSON')
        close=QPushButton('Close');row.addWidget(goto);row.addWidget(export_btn)
        row.addStretch(1);row.addWidget(close)
        displayed=[]
        def selected_issue():
            r=table.currentRow()
            return displayed[r] if 0<=r<len(displayed) else None
        def show_details():
            issue=selected_issue()
            detail.setPlainText((issue['message']+'\nSuggested action: '+issue['suggestion'])
                                if issue else 'No issue selected. Optical performance remains unverified.')
        def refresh():
            text=query.text().strip().casefold();displayed.clear()
            for issue in issues:
                if severity.currentIndex()>0 and issue['severity']!=severity.currentText():continue
                if category.currentIndex()>0 and issue['category']!=category.currentText():continue
                if text and text not in ' '.join(str(v) for v in issue.values()).casefold():continue
                displayed.append(issue)
            table.setRowCount(len(displayed))
            for r,issue in enumerate(displayed):
                for col,value in enumerate((issue['severity'],issue['category'],
                                            issue['component_name'],issue['message'])):
                    table.setItem(r,col,QTableWidgetItem(value))
            if displayed:table.selectRow(0)
            show_details()
        def go_to_component():
            issue=selected_issue()
            if not issue or not issue['component_id']:
                self.statusBar().showMessage('Issue is project-wide; no individual component to select')
                return
            self.selected_id=issue['component_id']
            self.refresh_tree();self.refresh_editor();self.refresh_view()
            dialog.accept()
            self.statusBar().showMessage('Selected '+issue['component_name']+' — '+issue['property'])
        def export_report():
            path,_=QFileDialog.getSaveFileName(dialog,'Save validation report',
                                               'validation_report.json','JSON (*.json)')
            if path:Path(path).write_text(json.dumps({'project_name':self.project.name,
                              'issue_count':len(issues),'issues':issues},indent=2),encoding='utf-8')
        severity.currentIndexChanged.connect(refresh)
        category.currentIndexChanged.connect(refresh)
        query.textChanged.connect(refresh)
        table.itemSelectionChanged.connect(show_details)
        goto.clicked.connect(go_to_component)
        export_btn.clicked.connect(export_report)
        close.clicked.connect(dialog.accept)
        refresh();dialog.exec()


def main():
    app=QApplication(sys.argv)
    app.setApplicationName('Optical Satellite Studio')
    w=MainWindow();w.show()
    return app.exec()

if __name__=='__main__':
    sys.exit(main())
