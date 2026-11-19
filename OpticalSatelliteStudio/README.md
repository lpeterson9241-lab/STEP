# Optical Satellite Studio — Python prototype v0.15.0

A Python desktop optomechanical front end for **Zemax OpticStudio**. Zemax is not required to run the editor. STEP import/export requires optional CadQuery/Open CASCADE.

## Run on macOS

In the extracted project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m optical_studio
```

Alternatively run `bash run_macos.command`. To enable STEP solid import, preview, and exchange, additionally run `python -m pip install -r requirements-cad.txt`. CadQuery wheels may vary by Python version and Mac architecture; use Python 3.10–3.12 where possible.

## Run on Windows

1. Install Python 3.10–3.12 (64-bit), including the Python launcher (`py`).
2. Extract the ZIP, open the extracted `OpticalSatelliteStudio` folder.
3. Double-click `run_windows.bat`, or use Windows Command Prompt:

```bat
cd C:\path\to\OpticalSatelliteStudio
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m optical_studio
```

For tests:

```bat
.venv\Scripts\python -m pip install pytest
.venv\Scripts\python -m pytest -q
```

## Implemented

- **v0.15.0 final CAD/OpticStudio handoff:** File → Export STEP package now produces **Complete_Satellite_Assembly.step** (CAD inspection) alongside a STEP solid per component (for separate OpticStudio nonsequential objects). A clear entrance aperture exports as an *annular mechanical rim*, not an opaque disk. Files include authoritative draft mathematical optical prescription (`Optical_Prescription.json`), local/world transforms, materials, coatings, face assignments, operating scenarios, unmodified nominal project, thermal audit and a validation report. Invalid/missing CAD solids abort export before a partial package is written. SHA-256 checksums let the OpticStudio dry-run audit detect modified STEP parts. A bundled Windows `build_zemax.py` performs CAD-only ZOS-API placement with a dry-run option; OpticStudio precision optical surfaces, verified CAD face mapping, sources, detectors and ray-tracing still require the specific installed version and a qualified optical validation. Never import complete assembly and per-part STEP files into the *same* optical scene, as that duplicates geometry.
- **v0.14.0 physics and assembly-spacing UI:** A dedicated **Physics** menu groups temperature/alignment operating configurations, material catalog, surface-scattering/coating assignments, and the thermal-emission audit. The selected part's right panel has a **Physics** section with nominal temperature (K), per-face emissivity (0–1), active-profile temperature, and shortcuts to the other controls. A new **Spacing** section shows the selected component's adjacent optical or mechanical element, signed axial separation (editable), transverse offset, and 3D center distance. **Assembly → Optical element separations** lists the complete optical chain or all non-assembly physical parts, with directly editable signed axial offsets. The axial value is measured along the first part's *local* +Z, not from optical surface to surface. Editing a spacing moves the target along that axis while preserving lateral displacement, or updates a matching axial-gap assembly constraint. Other constraints explicitly block conflicting edits. Profiles still only apply geometric first-order temperature effects; there is no heat-flow, mechanics, radiative transfer, or Zemax ray solver in this app.
- **v0.13.0 temperature-conditioned optical-property and IR emission audit:** Tools → Thermo-optical and IR emission report evaluates sourced, temperature-bounded material `dn_dT_per_K` and coating R/T corrections where supplied, plus graybody Planck spectral radiance for surfaces with known emissivities and temperatures. Draft optical-prescription surfaces include temperature-conditioned index samples; the Zemax handoff includes a thermo-optical audit. The calculations do **not** trace rays, account for telescope view factors, calculate radiance at the detector, or predict noise/SNR. Material dn/dT requires validated `thermo_optic_source`, `thermo_optic_temperature_range_K`, and reference temperature. Optional coating correction records require `temperature_coefficients_per_K` (`reflectance`/`transmittance`), `thermal_reference_temperature_K`, `thermal_valid_temperature_range_K`, and `thermal_source`; coefficients must be provided by the user and energy bounds are enforced. Unprovided cryogenic coefficients are never guessed.
- **v0.12.0 operating configurations:** Properties → Operating configurations provides named temperature and local alignment scenarios. Select a profile above the viewport to preview temperature-dependent linear isotropic expansion using material CTE and explicit XYZ/rotation offsets without changing the nominal assembly. Handoff, sequential draft and STEP export use the selected preview geometry; project saves preserve the nominal design and named profiles. The OPIR template provides a conceptual cryogenic detector scenario. These first-order previews are not thermal, structural, STOP, or ray-trace solutions; material coefficients may be unknown or invalid at cryogenic temperatures.
- **v0.11.0 external simulation-results viewer:** Tools → Import external optical results loads a user-provided folder with `results_manifest.json`, normalized ray-track CSV (`ray_id,sequence,x_mm,y_mm,z_mm`) and/or a rectangular detector map CSV (`pixel_x,pixel_y,irradiance_W_mm2`). Rays are drawn as gold polylines over the CAD assembly using the declared project-world millimeter frame. Tools → View imported detector map shows an indexed irradiance heatmap. The viewport has an External ray overlay switch and limits rendering to the first 250 tracks to remain interactive.
- **Result integrity:** Import validates schema, coordinate-frame declaration, finite physical values, strictly increasing ray sample indices, detector grid completeness, and detector-component references. A project optical-model SHA-256 fingerprint can be supplied and must match, preventing wrong-assignment imports. Any subsequent geometry, optical material, coating or face-setting edit clears the imported results. If the fingerprint is omitted, the app explicitly warns that alignment is unverified.
- **Portable exchange template:** Every STEP package includes `external_results_manifest_template.json` with the project fingerprint. The accompanying `examples/external_results_example/` includes a **synthetic visualization-only** ray CSV, detector CSV and manifest to demonstrate the import. These are not Zemax results or an optical simulation. Actual results must be converted from the installed solver's outputs to this documented interchange format; native `.ZRD` and Zemax detector-file parsing are not yet implemented.
- **v0.10.0 audited Zemax CAD handoff:** STEP exports include `zemax_cad_audit.json` plus portable `build_zemax.py`/`zemax_plan.py`. The audit checks CAD part existence and SHA-256 hashes, mm units, unique stable IDs and safe filenames, 4×4 rigid transforms, and explicit permission for rotation conventions. It produces a deterministic intended NSCE object-index map and lists which semantic/CAD-face assignments still require manual Zemax mapping. It explicitly reports `optical_simulation_ready: false`.
- **Cross-platform dry run:** The exported package can be audited without installing Zemax by running `python build_zemax.py . output.zos --dry-run`, which does not create a `.zos` file. `--report review.json` optionally saves a new audit report without overwriting an existing one. Actual CAD import is still Windows-only and requires a licensed installed ZOS-API.
- **Desktop export feedback:** The editor summarizes the audit after exporting STEP, and Tools → Audit exported Zemax package can inspect any previously exported folder, including missing parts, invalid transforms and face-mapping gaps.
- **v0.9.0 named optical/thermal surfaces:** Tools → Assign optical behavior to surfaces exposes stable, human-readable semantic surface IDs (for example `inner_wall`, `optical_front`, `substrate_back`, `vane_001_front`, `arm_004_side`, `top`, `active_face`). Each can store an optical interaction (reflective/transmissive/absorbing/scattering/detector/structural), a coating-catalog reference, scatter model, temperature in K, emissivity, roughness in nm, and a reference citation. Options include Lambertian, ABg with entered A/B/g parameters, and a user-supplied BRDF file path.
- **Per-face preflight and handoff:** Validation detects missing assignments as an aggregate per component, obsolete semantic IDs after changes to counts/geometry, invalid temperatures/emissivities, missing BRDF files, and unsourced scattering models. The versioned handoff manifest now contains `component_surface_assignments`, while STEP packages include `surface_assignments.json` for downstream processing. Unassigned faces are explicitly marked, never silently assumed calibrated.
- **Existing imported CAD face workflow preserved:** Tools → Inspect and assign CAD faces still uses face indices and B-rep signatures for STEP objects. New schema checks are applied to saved CAD face optical behavior too.
- **v0.8.0 optomechanical solids:** Components now includes **Baffle vane stack** (inner/outer diameters, integer vane count, axial spacing and thickness), **Spider support** (hub and outer diameters, integer arm count, arm width and thickness), **Mirror mounting ring**, and **Optical bench** (rectangular platform). These have independent adjustable local transforms, 3D preview sheets, validation and mechanical CadQuery STEP solids. Spider beams are fused to the hub; vane stacks export as a multiple-solid compound.
- **Detailed Cassegrain + mechanics template:** Templates now offers a demonstrative telescope with primary/secondary optics, housing, baffles, vanes, secondary spider, primary retaining ring, and instrument mounting bench. It is neither optically solved nor structurally approved.
- **Cutaway +Y viewport control:** Hides rendered geometry on the positive side of the global world-Y=0 plane to make inside arrangements easier to inspect. This is a **preview cutaway**, not a precision B-rep section and **does not modify exported STEP geometry**. Imported CAD tessellations use whole-triangle approximation for the cutaway.
- **Integer dimension controls:** Vane and spider arm counts use integer spin controls rather than floating-point dimensional fields. Geometry validation checks non-overlapping vane spacing and positive dimensions.
- **v0.7.1 source-referenced catalog data:** Five bundled optical material references are available immediately in new projects and safely merged into older saved projects (user records take precedence): **fused silica** (Malitson 1965; 0.21–3.71 µm), **silicon** (Salzberg & Villa 1957; 1.36–11 µm), **zinc selenide** (Connolly et al. 1979; 0.54–18.2 µm), **calcium fluoride** (Malitson 1963; 0.23–9.7 µm), and **germanium** (Crystran handbook 2023; sampled n from 2.058–13.02 µm). These are literature/manufacturer reference values, **not lot-certified optical constants**.
- The first four data records store the original bounded Sellmeier equations, not discretized approximations. Germanium stores the handbook's refractive-index samples and selected temperature/structural properties. In the material manager, **Evaluate refractive index** computes n at a requested wavelength and reports when k is unavailable. Out-of-band queries fail without extrapolation, and the saved record retains source URL, source temperature, and status.
- **Absorption is not invented:** The cited Sellmeier and germanium n-only records do not supply a co-registered extinction coefficient. They do not silently set k=0. Explicit spectral n/k CSV import remains supported. OPIR optical-path preflight warns when a lens lacks k or any requested wavelength lies outside its reference data.
- **v0.7.0 engineering catalog managers:** Properties → Material catalog manager and Coating catalog manager provide searchable records, individual editable fields, source/provenance and verification status, optional engineering properties, spectral JSON editors, catalog JSON import/export, and direct component assignment. Material n/k CSV import remains available. Candidate entries do not contain invented material constants.
- **Physical-property validation:** Density, thermal conductivity, elastic modulus, CTE, coating emissivity and surface roughness are checked for finite and physically appropriate ranges. Coating spectral R/T samples continue to enforce energy conservation. Uncalibrated material-name candidates and coatings with missing spectra remain visible as warnings.
- **v0.7.0 issue inspector:** Tools → Validate assembly opens a searchable/filterable table grouped by severity, category, and component, with correction guidance, component navigation, and exportable JSON issue reports. This replaces the long single-text popup.
- **v0.6.0 ordered optical path editor:** Properties → Optical path and wavelengths opens a selectable, reorderable element list, comma-separated wavelengths in µm, XY field-angle pairs in degrees, and a selectable stop aperture. The project JSON retains this optical path across sessions. The initial world-Z ordering is only a suggestion and must be reviewed.
- **Draft optical prescription export:** File → Export optical prescription JSON writes ordered mirror reflective faces, both faces of each lens, apertures, filters, and detectors. Each surface carries curvature, conic constant, A4–A12 asphere terms, clear aperture, material/coating labels, world frame and normal. Hand-off JSON and STEP packages also include the prescription; STEP packages include `optical_prescription_draft.json`.
- **Path-aware preflight:** Duplicate or missing component IDs, invalid wavelength samples, malformed field angles, and stop assignments are rejected before saving or export. Reflective folds and uncalibrated lens materials generate warnings. Listed surface-origin distances are geometrical—not signed Zemax LDE thicknesses.
- **v0.5.0 optical prescriptions:** Mirrors accept substrate thickness and even-asphere coefficients A4, A6, A8, A10, A12 alongside curvature and conic constant. New parametric lenses have front and back radii, conics, independent even-asphere terms, center thickness, and diameter. Sag is used in previews and sampled mechanical STEP exports.
- **Searchable material candidates:** Properties → Browse material catalog offers optical and structural material names. Imported CSV wavelength-dependent refractive index (`n`) and extinction (`k`) data require an identified source; JSON material catalogs can also be imported. Sampled data are linearly interpolated within their wavelength range only.
- **Coating library and preflight:** Properties → Coating catalog (JSON) edits spectral reflection/transmission records; validation checks optical prescription geometry, spectral records, and conservation of reflection/transmission fractions. Candidate names alone are explicitly uncalibrated.
- **Readable measurement results:** Center-distance, CAD-clearance, and validation results appear in high-contrast, selectable-text dialogs instead of macOS system message boxes with illegible inherited colors.
- **v0.4.1 right-panel layout:** Replaced the vertically stretched property pages with natural-height collapsible sections. Full-width labels sit above their input controls, avoiding text clipping in narrow panels; inactive sections collapse to compact headers.
- **Categorized component inspector:** Identity, Placement, Dimensions, Optical properties, and Advanced panels replace one long list of fields.
- **Click selection in the 3D viewport:** left-click a visible component to select it (screen-projected mesh sampling; approximate for overlapping objects). The tree remains the exact selection fallback.
- **Axis-constrained viewport translation:** choose Move X/Y/Z above the viewport, then drag a component along the specified global direction. Colored axis guides display at the selected component. This is a 2D-projected manipulation aid, not a CAD-grade 3-axis gizmo.
- **Direct face picking:** choose Pick face for the currently selected CAD component; clicking near a rendered B-rep face opens the existing face inspector on that numbered face. Clicking is approximate, while stored assignments refer to precise OCCT face indices.
- **Geometric placement constraints:** Assembly → Placement constraints supports coincident origins, coaxial local Z axes, axial separations, and relative rigid poses (offset XYZ and rotations). Constraints propagate when source positions change and reject cycles or multiple controllers of one target.
- **Exact B-rep clearance tools:** Tools → Measure exact CAD clearance offers nearest solid-to-solid or selected face-to-face distance, closest-point coordinates, local-Z angular difference, and overlap volume when intersecting. Requires CadQuery/Open CASCADE.
- **Regeneration:** modifying linked dimensions or constraint-source placement updates dependent components; CAD STEP export regenerates affected solids from parameters.
- **STEP assembly import:** use Assembly → Import STEP assembly, select a STEP source and an extraction destination. CadQuery/OCCT reads a hierarchy of assembly nodes with local placements; individual part STEP files are exported and attached to the tree. Nested compound solids and cross-CAD persistent semantic IDs are not guaranteed.
- **CAD face inspector:** Tools → Inspect and assign CAD faces. Select a B-rep face from the numbered list; it highlights in the viewport. Save a JSON coating/scatter assignment and a geometric face signature. A changed geometry signature is flagged during validation.
- **Geometric collision checking:** Tools → Check solid interferences. Uses OCCT solid-solid Boolean intersection volume, not approximate viewport triangle intersections. It excludes abstract detector/aperture disks and reports unavailable parts separately.
- **Alignment tolerance checking:** assign component metadata `nominal_world_transform` (4×4 matrix) and `alignment_tolerance` such as `{"position_mm":0.05,"orientation_deg":0.01}`. Validation compares actual world pose with the nominal transform.
- **Windows OpticStudio adapter:** STEP export packages include `build_zemax.py`. On a Windows machine with OpticStudio, pythonnet and a valid ZOS-API license, it attempts to create a **CAD-only non-sequential .ZOS** with one CAD object per STEP part. The adapter is not yet tested with your installed OpticStudio version; material/face mappings and optical ray tracing remain unverified.
- Compact in-window **File, Templates, Assembly, Components, Properties, and Tools** menus; a single-row quick-access toolbar for Open, Save, Validate, and STEP export.
- Editable Cassegrain **concept** template: two conic mirrors, central baffle, exterior tube, detector placeholder, entrance aperture.
- Generic conceptual **OPIR satellite template**, with spacecraft bus, optical payload, bench, baffles, cold shield, detector, and entrance aperture.
- Parent-child assembly hierarchy with composed 4×4 world transforms, safe reparenting that preserves world pose, and cycle detection.
- Editable linear parameter links (`target = source × scale + offset`) and OPIR template dimension dependencies.
- Project-level JSON material-property library, component-level face-property mappings, and OPIR metadata for detector band/temperature.
- Optional CAD STEP import and tessellated preview, plus mechanical STEP solid generation via CadQuery.
- **Export STEP package** outputs individual STEP solids with a Zemax handoff manifest of global poses, optical prescriptions, and intended material/coating assignments.
- Center-origin distance measurement and preflight validation, including missing CAD and unverified material records.
- Hierarchical scene tree and live 3D parametric preview (rotate with the mouse).
- Always-visible camera buttons above the viewport: **Isometric, Front (−Y), Back (+Y), Left (−X), Right (+X), Top (+Z), Bottom (−Z)**. These replace the poorly contrasted macOS dropdown from v0.1.1.
- Dark styling is applied to the component inspector and its labels for legibility on macOS.
- Camera rotation is **locked by default**. Check **Free rotate (click and drag)** to rotate manually. Selecting a preset automatically disables free rotation; the last angle stays in place after ordinary component edits.
- Add/remove optical and mechanical components; edit XYZ transforms, dimensions, material and coating labels.
- Save/open independent JSON projects.
- Attach a STEP reference and optionally display its tessellated solid if CadQuery is installed.
- Export a versioned *preflight handoff JSON* for a future installed-version Zemax ZOS-API adapter.
- Geometry, project serialization, rotation and manifest unit tests.

## Important limitations

- The template is illustrative, **not an optically solved Cassegrain telescope**. Mirror curvature signs, exact focal plane, obscuration, and mechanical collisions have not been validated in Zemax.
- Rendering is a Matplotlib preview. CadQuery enables actual STEP B-rep solids, but not a full interactive precision CAD viewport. Tubes show outer walls in the preview; their inner bores are retained and exported as solids.
- New optomechanical parts are simplified annuli, straight rectangular arms, and a rectangular bench. They do not yet include fillets, fasteners, kinematic mount pads, physical vane edge finishing, tolerance stacks, thermal deformation, or CAD-grade clipping. The +Y cutaway is visual only.
- Five named optical materials now contain **sourced reference refractive-index data**, while other candidates and all default coating labels remain uncalibrated. These references are not verified against a Zemax glass/coating catalog or the particular vendor lot; temperature dependence, absorption, crystal grades, stress and cryogenic properties require further data. No proprietary or speculative constants are bundled.
- Imported STEP assemblies can now be decomposed into named child components with local transforms. **Stable semantic face matching across CAD revisions remains unsolved**; face signatures help detect some changes.
- Mirror and lens STEP solids use sampled even-asphere sag approximations. Exported prescriptions must be used to construct native Zemax optical surfaces; STEP solids are not authoritative for high-precision optical analysis.
- The center-distance tool remains available, and the new CAD clearance tool measures B-rep surface distances. Manufacturing tolerance stack-up, compliant deformation, and minimum-clearance constraints across parameter sweeps remain future work.
- Parameter links support linear relationships; placement constraints support four common rigid-body relationships but do not constitute a fully general nonlinear CAD mate solver.
- Material and face data allow user-supplied values, but their provenance, optical performance, and compatibility with an installed Zemax catalog still require engineering verification.
- CAD package export requires CadQuery and an empty export destination; individual STEP solids are kept in their local frames, with world transforms recorded in the manifest.
- `.ZOS` generation is prepared as a Windows-only **untested** script inside exported CAD packages; the macOS editor itself does not generate native OpticStudio files. Optical elements, materials, coatings, and ray-tracing configuration must still be verified and completed in OpticStudio.
- **v0.10 is a preflight milestone, not full OpticStudio integration.** The installed-version CAD import call, tilt convention, face mapping, coating/scatter compatibility, source and detector setup, and ray tracing have not been tested against your particular Zemax installation. An audit marked CAD-import-ready does not mean optical-simulation-ready.
- Imported result rays are displayed exactly as supplied: the app does not recompute propagation, ensure valid physical source directions, or calibrate detector power. Detector CSV units are W/mm²; summing raw pixel values does **not** yield total watts without pixel area. Frame correctness and Zemax coordinate transformation must be verified at the export source. Existing mismatched model results are refused or invalidated.
- The draft sequential path does **not** construct a validated OpticStudio Lens Data Editor model. Reflective paths require deliberate ray propagation direction, curvature-sign and normal conventions, local coordinate breaks, refractive indexes and coating mapping, and a physically validated stop/entrance pupil. The JSON is an auditable intermediate handoff, not a solved optical design.
- **v0.9 optical behaviors are annotations, not physical simulations.** Semantic surface IDs do not yet resolve to precise Open CASCADE face indices or Zemax object-face indices. Assigning Lambertian, ABg or BRDF paths does not evaluate scattering, validate normalization, or instantiate an OpticStudio scatter model. Surface emissivity and temperature are stored but thermal emission is not ray-traced until verified Zemax integration.
- Internal rotation uses local XYZ Euler angles with `Rz @ Ry @ Rx`; a Zemax adapter must account for OpticStudio's coordinate conventions.
- Component transforms, material properties and simple dependent geometry are editable; image formation is not yet calculated.
- Camera view presets use the project's XYZ axes (the telescope's nominal axis is Z), not a particular satellite flight convention.

## Next milestones

1. Installed-version OpticStudio adapter verification and coordinate/material/face mapping, followed by a verified sequential Lens Data Editor builder that consumes the new draft prescription.
2. Precision B-rep sectioning and advanced mesh/B-rep selection in dense overlapping assemblies, plus persistent topological naming across STEP revisions; current cutaway and picking are screen/mesh approximations.
3. True direct CAD vertex/edge/face geometry editing, nonlinear mate solving, and engineering tolerance stack-up. Current constraints handle rigid placement and collision tests detect penetrating volume.
4. Optically solved telescope prescriptions and physically sourced OPIR thermal/BRDF data.

## Windows ZOS-API handoff (experimental)

After **Export STEP package** creates a directory with STEP files and `zemax_handoff.json`, transfer that whole directory to the Windows machine with licensed OpticStudio. Install `pythonnet` in a compatible Windows Python environment. From that directory run:

```bat
python build_zemax.py . OpticalSatelliteStudio_generated.zos
```

Before transferring to Windows, inspect the generated `zemax_cad_audit.json` and run the **portable dry run** from the export folder:

```bash
python build_zemax.py . OpticalSatelliteStudio_generated.zos --dry-run
```

The audit includes intended NSCE object indices and component IDs, but **does not apply surface, material or scatter properties to Zemax**. For a saved additional review JSON, append `--report review.json` (the file must not already exist).

The script refuses to overwrite an existing `.zos`, and refuses to import rotated objects unless `--allow-rotations` is explicitly supplied. **Do not enable that flag without verifying tilt conventions in your installed OpticStudio.** It installs uniquely named STEP copies into the configured OpticStudio CAD Files folder. Resulting CAD-only models do not constitute a validated optical simulation.

The canonical project schema intentionally does not depend on Zemax, while Zemax remains the intended optical simulation engine.

## External optical-results exchange (v0.11)

The results folder must contain `results_manifest.json` using the format in `examples/external_results_example`. Supply `rays_csv` and/or `detector_csv` as simple relative filenames. The ray file consists of one **world-coordinate point per row** grouped by `ray_id`, with increasing integer `sequence`; at least two points per displayed ray. The detector file is a complete rectangular grid of zero-based pixel indices and nonnegative irradiance in W/mm². Set `coordinate_frame` to `project_world_xyz_mm`. For reliable reuse of a CAD package, copy `external_results_manifest_template.json` to `results_manifest.json` and add normalized CSV files; its fingerprint checks that the imported results belong to the corresponding model. These CSV formats are application-defined, not a claim about native OpticStudio export formats.
