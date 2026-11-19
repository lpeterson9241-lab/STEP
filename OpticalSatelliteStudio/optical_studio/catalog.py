"""Inspectable optical material/coating catalogs; no fabricated optical constants."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import math

MATERIAL_CANDIDATES={
    'Germanium':'infrared optical material',
    'Silicon':'infrared optical material',
    'Zinc selenide':'infrared optical material',
    'Zinc sulfide':'infrared optical material',
    'Sapphire':'optical substrate',
    'Calcium fluoride':'optical material',
    'Fused silica':'optical material',
    'Aluminum':'structural/mirror substrate',
    'Beryllium':'mirror substrate',
    'Silicon carbide':'mirror substrate',
    'Invar':'structural material',
}


def candidate_record(name):
    if name not in MATERIAL_CANDIDATES:raise KeyError(name)
    return {'category':MATERIAL_CANDIDATES[name],
            'status':'name_only_no_validated_optical_constants',
            'source':'Not supplied',
            'spectral_constants':[],
            'note':'Import measured or authoritative optical constants before optical simulation.'}


def import_spectral_csv(path, name, source):
    """Import wavelength_um, n, k data; no extrapolation or inferred constants."""
    if not name.strip() or not source.strip():
        raise ValueError('Material name and source/provenance are required')
    with Path(path).open(newline='',encoding='utf-8-sig') as fp:
        reader=csv.DictReader(fp)
        if not {'wavelength_um','n','k'} <= set(reader.fieldnames or []):
            raise ValueError('CSV requires wavelength_um, n, k columns')
        samples=[]
        for row in reader:
            try:sample={key:float(row[key]) for key in ('wavelength_um','n','k')}
            except (ValueError,TypeError,KeyError) as exc:raise ValueError('Invalid spectral CSV number') from exc
            samples.append(sample)
    if not samples:raise ValueError('CSV contains no spectral samples')
    validate_spectral_samples(samples)
    return {'category':'user_imported','status':'user_supplied_unverified',
            'source':source.strip(),'spectral_constants':samples}


def validate_spectral_samples(samples):
    if not isinstance(samples,list) or not samples:raise ValueError('Nonempty spectral samples required')
    previous=-float('inf')
    for point in samples:
        wl,n,k=(float(point[key]) for key in ('wavelength_um','n','k'))
        if not all(math.isfinite(x) for x in (wl,n,k)) or wl<=previous or wl<=0 or n<=0 or k<0:
            raise ValueError('Wavelength must increase; n must be positive and k nonnegative')
        previous=wl


def interpolate_nk(record,wavelength_um):
    if not record.get('spectral_constants'):
        return refractive_index(record,wavelength_um),None
    samples=record.get('spectral_constants',[])
    validate_spectral_samples(samples)
    if wavelength_um<samples[0]['wavelength_um'] or wavelength_um>samples[-1]['wavelength_um']:
        raise ValueError('Wavelength outside supplied material data; extrapolation forbidden')
    for a,b in zip(samples,samples[1:]):
        if a['wavelength_um']<=wavelength_um<=b['wavelength_um']:
            t=(wavelength_um-a['wavelength_um'])/(b['wavelength_um']-a['wavelength_um'])
            return (a['n']+t*(b['n']-a['n']),a['k']+t*(b['k']-a['k']))
    return (samples[0]['n'],samples[0]['k'])


def has_refractive_data(record):
    return bool(record.get('spectral_constants') or record.get('dispersion') or
                record.get('refractive_index_samples'))


def refractive_index(record,wavelength_um):
    """Evaluate n from measured samples or a bounded, sourced Sellmeier fit."""
    if not isinstance(wavelength_um,(int,float)) or not math.isfinite(wavelength_um) or wavelength_um<=0:
        raise ValueError('Wavelength must be positive and finite (µm)')
    if record.get('spectral_constants'):
        return interpolate_nk(record,wavelength_um)[0]
    if record.get('dispersion'):
        formula=record['dispersion'];lo,hi=formula['wavelength_range_um']
        if not lo<=wavelength_um<=hi:
            raise ValueError(f'Wavelength outside fitted dispersion range [{lo}, {hi}] µm')
        sq=wavelength_um*wavelength_um
        n_squared=1.0
        for b,c in zip(formula['B'],formula['C_um']):
            denominator=sq-c*c
            if abs(denominator)<1e-14:
                raise ValueError('Wavelength at Sellmeier resonance')
            n_squared+=b*sq/denominator
        if n_squared<=0 or not math.isfinite(n_squared):raise ValueError('Nonphysical refractive index from formula')
        return math.sqrt(n_squared)
    samples=record.get('refractive_index_samples',[])
    if not samples:raise ValueError('No refractive-index data available for this material')
    if wavelength_um<samples[0]['wavelength_um'] or wavelength_um>samples[-1]['wavelength_um']:
        raise ValueError('Wavelength outside measured refractive-index range')
    for a,b in zip(samples,samples[1:]):
        if a['wavelength_um']<=wavelength_um<=b['wavelength_um']:
            t=(wavelength_um-a['wavelength_um'])/(b['wavelength_um']-a['wavelength_um'])
            return a['n']+t*(b['n']-a['n'])
    return samples[0]['n']


def import_library_json(path):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data,dict) or not all(isinstance(v,dict) for v in data.values()):
        raise ValueError('Catalog JSON must map material names to property objects')
    for name,record in data.items():
        validate_physical_record(record,'material')
        if record.get('spectral_constants'):validate_spectral_samples(record['spectral_constants'])
    return data


def validate_physical_record(record,kind):
    """Validate engineering units/ranges, not scientific truth or provenance."""
    if not isinstance(record,dict):raise ValueError('A catalog entry must be a property object')
    for key in ('source','status','category'):
        if key in record and not isinstance(record[key],str):
            raise ValueError(f'{key} must be text')
    positive=('density_kg_m3','thermal_conductivity_W_mK','youngs_modulus_Pa','roughness_rms_nm')
    for key in positive:
        if key in record:
            value=record[key]
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
                raise ValueError(f'{key} must be a finite nonnegative number')
    if 'density_kg_m3' in record and record['density_kg_m3']==0:
        raise ValueError('Density must be positive')
    if 'cte_per_K' in record:
        value=record['cte_per_K']
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('CTE must be finite')
    if kind=='material':
        for key in ('dn_dT_per_K','thermo_optic_reference_temperature_K','source_temperature_K'):
            if key in record:
                value=record[key]
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
                    raise ValueError(f'{key} must be finite')
                if key!='dn_dT_per_K' and value<=0:
                    raise ValueError(f'{key} must be positive kelvin')
        if 'dn_dT_per_K' in record and not record.get('thermo_optic_source'):
            raise ValueError('dn/dT requires thermo_optic_source provenance')
        if 'thermo_optic_temperature_range_K' in record:
            bounds=record['thermo_optic_temperature_range_K']
            if (not isinstance(bounds,list) or len(bounds)!=2 or
                any(isinstance(x,bool) or not isinstance(x,(int,float)) or
                    not math.isfinite(x) for x in bounds) or not 0<bounds[0]<bounds[1]):
                raise ValueError('Thermo-optic range requires two increasing positive kelvin values')
        if 'dn_dT_per_K' in record and (
            'thermo_optic_temperature_range_K' not in record or
            not any(k in record for k in ('thermo_optic_reference_temperature_K','source_temperature_K'))):
            raise ValueError('dn/dT needs reference temperature and validated temperature range')
    if kind=='coating' and 'temperature_coefficients_per_K' in record:
        coefficients=record['temperature_coefficients_per_K']
        if not isinstance(coefficients,dict) or set(coefficients)!={'reflectance','transmittance'}:
            raise ValueError('Coating thermal coefficients require reflectance and transmittance')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v)
               for v in coefficients.values()):
            raise ValueError('Coating thermal coefficients must be finite')
        bounds=record.get('thermal_valid_temperature_range_K')
        reference=record.get('thermal_reference_temperature_K')
        if (not record.get('thermal_source') or
            not isinstance(bounds,list) or len(bounds)!=2 or
            any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in bounds) or
            not 0<bounds[0]<bounds[1] or isinstance(reference,bool) or
            not isinstance(reference,(int,float)) or not math.isfinite(reference) or
            not bounds[0]<=reference<=bounds[1]):
            raise ValueError('Coating thermal response requires source, reference K and valid temperature range')
    if 'emissivity' in record:
        value=record['emissivity']
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=1:
            raise ValueError('Emissivity must lie from 0 to 1')
    samples=record.get('spectral_constants' if kind=='material' else 'spectral_rt',[])
    if not isinstance(samples,list):raise ValueError('Spectral samples must be an array')
    if kind=='material' and samples:validate_spectral_samples(samples)
    if kind=='material' and record.get('dispersion') is not None:
        formula=record['dispersion']
        if not isinstance(formula,dict) or formula.get('type')!='sellmeier_1':
            raise ValueError('Unsupported dispersion formula')
        B,C,limits=formula.get('B'),formula.get('C_um'),formula.get('wavelength_range_um')
        if not all(isinstance(v,list) for v in (B,C,limits)) or len(B)!=len(C) or not B or len(limits)!=2:
            raise ValueError('Sellmeier needs matched B and C_um lists and two wavelength bounds')
        if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in B+C+limits):
            raise ValueError('Sellmeier constants must be finite numbers')
        if limits[0]<=0 or limits[0]>=limits[1] or any(c<=0 for c in C):
            raise ValueError('Invalid Sellmeier wavelength bounds or resonance positions')
        # Every resonance inside the declared usable band invalidates the fit.
        if any(limits[0]<=c<=limits[1] for c in C):
            raise ValueError('Sellmeier resonance inside usable wavelength band')
        refractive_index(record,limits[0]);refractive_index(record,limits[1])
    if kind=='material' and record.get('refractive_index_samples') is not None:
        points=record['refractive_index_samples']
        if not isinstance(points,list):raise ValueError('Refractive-index samples must be a list')
        previous=0
        for p in points:
            try:w=float(p['wavelength_um']);n=float(p['n'])
            except (KeyError,TypeError,ValueError) as exc:raise ValueError('Invalid n-only sample') from exc
            if not math.isfinite(w) or not math.isfinite(n) or w<=previous or n<=0:
                raise ValueError('n-only sample wavelengths must increase; n must be positive')
            previous=w
    if kind=='coating':
        previous=0
        for s in samples:
            try:
                wl=float(s['wavelength_um']);r=float(s['reflectance']);t=float(s['transmittance'])
            except (KeyError,TypeError,ValueError) as ex:
                raise ValueError('Coating spectral sample requires wavelength_um, reflectance and transmittance') from ex
            if not all(math.isfinite(v) for v in (wl,r,t)) or wl<=previous or not 0<=r<=1 or not 0<=t<=1 or r+t>1+1e-8:
                raise ValueError('Coating samples must increase in wavelength and obey R + T ≤ 1')
            previous=wl
