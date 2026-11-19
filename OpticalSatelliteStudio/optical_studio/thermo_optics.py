"""Temperature-conditioned optical bookkeeping; no ray tracing or radiative transfer.

Material dn/dT requires an explicitly sourced coefficient and reference temperature.
Planck radiance is emitted *at* each assigned surface; no view factors or paths to
the detector are inferred from the CAD preview.
"""
from __future__ import annotations

import math

H=6.62607015e-34
C=299792458.0
KB=1.380649e-23


def planck_radiance_w_m2_sr_um(wavelength_um, temperature_K):
    """Blackbody spectral radiance per micrometer of wavelength."""
    if not all(math.isfinite(float(v)) and float(v)>0 for v in (wavelength_um,temperature_K)):
        raise ValueError('Wavelength and temperature must be finite positive values')
    wl=wavelength_um*1e-6
    exponent=H*C/(wl*KB*temperature_K)
    if exponent>700:return 0.0
    return (2*H*C*C/wl**5)/math.expm1(exponent)*1e-6


def conditioned_index(record, wavelength_um, temperature_K):
    """Return n,k at T with optional sourced constant dn/dT, never extrapolating λ.

    When no dn/dT exists, non-reference temperatures are *not* silently treated
    as valid: the caller gets a warning and no conditioned index.
    """
    from .catalog import interpolate_nk
    from .catalog import refractive_index
    reference=record.get('thermo_optic_reference_temperature_K',record.get('source_temperature_K'))
    if reference is None:raise ValueError('No material optical reference temperature')
    n=refractive_index(record,wavelength_um)
    _,k=interpolate_nk(record,wavelength_um)
    if abs(temperature_K-reference)>1e-9:
        coefficient=record.get('dn_dT_per_K')
        if coefficient is None:raise ValueError('Missing sourced dn/dT coefficient')
        bounds=record.get('thermo_optic_temperature_range_K')
        if bounds is None:raise ValueError('Missing validated dn/dT temperature range')
        if not bounds[0]<=temperature_K<=bounds[1] or not bounds[0]<=reference<=bounds[1]:
            raise ValueError('Temperature outside supplied dn/dT validity interval')
        n+=coefficient*(temperature_K-reference)
        if not math.isfinite(n) or n<=0:raise ValueError('Nonphysical corrected refractive index')
    return {'wavelength_um':wavelength_um,'temperature_K':temperature_K,
            'n':n,'k':k,'k_known':k is not None,
            'method':'catalog_n_plus_linear_dn_dT' if temperature_K!=reference else 'catalog_n_at_reference_T',
            'reference_temperature_K':reference}


def conditioned_coating(record,wavelength_um,temperature_K):
    """Interpolate supplied R/T and optionally apply sourced, bounded linear dR/dT,dT/dT."""
    samples=record.get('spectral_rt',[])
    if not samples:raise ValueError('Coating has no supplied spectral R/T')
    if wavelength_um<samples[0]['wavelength_um'] or wavelength_um>samples[-1]['wavelength_um']:
        raise ValueError('Wavelength outside coating data')
    r,t=samples[0]['reflectance'],samples[0]['transmittance']
    for a,b in zip(samples,samples[1:]):
        if a['wavelength_um']<=wavelength_um<=b['wavelength_um']:
            ratio=(wavelength_um-a['wavelength_um'])/(b['wavelength_um']-a['wavelength_um'])
            r=a['reflectance']+(b['reflectance']-a['reflectance'])*ratio
            t=a['transmittance']+(b['transmittance']-a['transmittance'])*ratio
            break
    reference=record.get('thermal_reference_temperature_K')
    coefficients=record.get('temperature_coefficients_per_K')
    if reference is not None and temperature_K!=reference:
        if coefficients is None:raise ValueError('Missing sourced coating temperature coefficients')
        low,high=record['thermal_valid_temperature_range_K']
        if not low<=temperature_K<=high:raise ValueError('Outside coating thermal validity range')
        r+=coefficients['reflectance']*(temperature_K-reference)
        t+=coefficients['transmittance']*(temperature_K-reference)
    elif reference is None:
        raise ValueError('Coating R/T reference temperature not specified')
    if r<-1e-9 or t<-1e-9 or r+t>1+1e-9:
        raise ValueError('Thermal correction violates coating energy bounds')
    return {'wavelength_um':wavelength_um,'temperature_K':temperature_K,
            'reflectance':max(0.,r),'transmittance':max(0.,t),
            'absorptance':max(0.,1-r-t)}


def thermal_report(project,wavelengths_um=None):
    """Audit index changes and local surface emission with known emissivities.

    Surface temperature assignments take precedence over component temperatures.
    No radiance is propagated or summed at the image plane.
    """
    from .surface_catalog import semantic_surfaces,semantic_key
    from .catalog import has_refractive_data
    project.validate()
    if wavelengths_um is None:
        wavelengths_um=(project.metadata.get('optical_sequence') or {}).get('wavelengths_um',[3.,4.,5.])
    wavelengths=sorted(set(float(w) for w in wavelengths_um))
    if not wavelengths or any(not math.isfinite(w) or w<=0 for w in wavelengths):
        raise ValueError('Wavelengths must be positive finite values')
    refractive=[];emission=[];coatings=[];warnings=[]
    for comp in project.components:
        if not comp.enabled:continue
        material=project.material_library.get(comp.material,{})
        temperature=comp.metadata.get('operating_temperature_K',comp.metadata.get('temperature_K'))
        if comp.kind in ('lens','filter') and has_refractive_data(material):
            if temperature is None:
                warnings.append(f'{comp.name}: no temperature for temperature-conditioned index')
            else:
                for wavelength in wavelengths:
                    try:
                        values=conditioned_index(material,wavelength,temperature)
                        refractive.append({'component_id':comp.id,'component_name':comp.name,**values})
                    except ValueError as exc:
                        warnings.append(f'{comp.name} at {wavelength:g} µm: {exc}')
        for face in semantic_surfaces(comp):
            assignment=comp.surface_properties.get(semantic_key(face),{})
            coating_name=assignment.get('coating') or comp.coating
            emissivity=assignment.get('emissivity',material.get('emissivity'))
            surface_temperature=assignment.get('temperature_K',temperature)
            coating_record=project.coating_library.get(coating_name,{})
            if surface_temperature is not None and coating_record.get('spectral_rt'):
                for wavelength in wavelengths:
                    try:
                        coatings.append({'component_id':comp.id,'component_name':comp.name,
                                         'surface':face,'coating':coating_name,
                                         **conditioned_coating(coating_record,wavelength,surface_temperature)})
                    except ValueError as exc:
                        warnings.append(f'{comp.name}/{face} coating {wavelength:g} µm: {exc}')
            if emissivity is None or surface_temperature is None:
                continue
            values=[emissivity*planck_radiance_w_m2_sr_um(w,surface_temperature)
                    for w in wavelengths]
            emission.append({'component_id':comp.id,'component_name':comp.name,
                             'surface':face,'temperature_K':surface_temperature,
                             'emissivity_assumed_gray':emissivity,'wavelengths_um':wavelengths,
                             'spectral_exit_radiance_W_m2_sr_um':values})
    return {'format':'optical-satellite-studio-thermo-optical-audit','version':1,
            'status':'local_properties_only_no_ray_tracing_or_radiative_transfer',
            'active_operating_profile':project.metadata.get('active_operating_profile'),
            'wavelengths_um':wavelengths,'conditioned_refractive_indices':refractive,
            'conditioned_coating_rt':coatings,
            'surface_thermal_radiance':emission,'warnings':warnings,
            'limitations':[
                'A supplied constant dn/dT is first-order only, within its declared temperature range.',
                'Extinction k is not temperature-adjusted; wavelength-dependent k may be unknown.',
                'Emissivity is assumed gray at the sampled wavelengths; no spectral/angle dependence.',
                'Radiance is emitted at surfaces only; no visibility, reflection, occlusion, view factors, or detector loading calculated.',
                'No STOP, surface deformation, temperature-dependent scattering, Zemax ray trace, or infrared background simulation.']}
