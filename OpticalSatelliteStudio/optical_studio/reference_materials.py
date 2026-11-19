"""Small bundled, source-attributed optical-index references.

Values derive from refractiveindex.info's CC0 database/source papers. They
represent particular specimen/temperature conditions, not vendor-certified
material lots. Sellmeier records describe n ONLY, never an assumed k=0.
"""
from __future__ import annotations

from copy import deepcopy


_DATABASE = {
    'Fused silica': {
        'category':'optical substrate', 'status':'published_reference_not_lot_certified',
        'source':'I. H. Malitson, JOSA 55, 1205 (1965); refractiveindex.info SiO2/Malitson (CC0)',
        'source_url':'https://refractiveindex.info/?book=SiO2&page=Malitson&shelf=main',
        'source_temperature_K':293.0,
        'dispersion':{'type':'sellmeier_1','wavelength_range_um':[0.21,3.71],
            'B':[0.6961663,0.4079426,0.8974794],
            'C_um':[0.0684043,0.1162414,9.896161]},
        'note':'Formula valid to 3.71 µm per original 1965 paper. No extinction coefficient supplied.'},
    'Silicon': {
        'category':'infrared optical material', 'status':'published_reference_not_lot_certified',
        'source':'C. D. Salzberg and J. J. Villa, JOSA 47, 244 (1957); refractiveindex.info Si/Salzberg (CC0)',
        'source_url':'https://refractiveindex.info/?book=Si&page=Salzberg&shelf=main',
        'source_temperature_K':299.0,
        'dispersion':{'type':'sellmeier_1','wavelength_range_um':[1.36,11.0],
            'B':[10.6684293,0.0030434748,1.54133408],
            'C_um':[0.301516485,1.13475115,1104.0]},
        'note':'n-only bulk crystalline Si formula at 26 °C; extinction must be supplied separately.'},
    'Zinc selenide': {
        'category':'infrared optical material', 'status':'published_reference_not_lot_certified',
        'source':'J. Connolly et al., Proc. SPIE 181, 141 (1979); B. Tatian, Appl. Opt. 23, 4477 (1984); refractiveindex.info ZnSe/Connolly (CC0)',
        'source_url':'https://refractiveindex.info/?book=ZnSe&page=Connolly&shelf=main',
        'source_temperature_K':296.0,
        'dispersion':{'type':'sellmeier_1','wavelength_range_um':[0.54,18.2],
            'B':[4.45813734,0.467216334,2.89566290],
            'C_um':[0.200859853,0.391371166,47.1362108]},
        'note':'n-only CVD ZnSe formula at 23 °C; absorption is not provided.'},
    'Calcium fluoride': {
        'category':'optical material', 'status':'published_reference_not_lot_certified',
        'source':'I. H. Malitson, Applied Optics 2, 1103 (1963); refractiveindex.info CaF2/Malitson (CC0)',
        'source_url':'https://refractiveindex.info/?book=CaF2&page=Malitson&shelf=main',
        'source_temperature_K':297.0,
        'dispersion':{'type':'sellmeier_1','wavelength_range_um':[0.23,9.7],
            'B':[0.5675888,0.4710914,3.8484723],
            'C_um':[0.050263605,0.1003909,34.649040]},
        'note':'n-only CaF2 formula at ~24 °C; check index reference medium and crystal grade.'},
    'Germanium': {
        'category':'infrared optical material', 'status':'manufacturer_reference_not_lot_certified',
        'source':'Crystran, Handbook of Infra-Red and Ultra-Violet Optical Materials (2023); refractiveindex.info specs/crystran/optical/Ge.yml (CC0)',
        'source_url':'https://refractiveindex.info/database/data/specs/crystran/optical/Ge.yml',
        'source_temperature_K':293.0,
        'refractive_index_samples':[
            {'wavelength_um':2.058,'n':4.1020},{'wavelength_um':2.153,'n':4.0919},
            {'wavelength_um':2.313,'n':4.0786},{'wavelength_um':2.437,'n':4.0708},
            {'wavelength_um':2.577,'n':4.0609},{'wavelength_um':2.714,'n':4.0562},
            {'wavelength_um':2.998,'n':4.0452},{'wavelength_um':3.303,'n':4.0369},
            {'wavelength_um':4.258,'n':4.0216},{'wavelength_um':4.866,'n':4.0170},
            {'wavelength_um':6.238,'n':4.0094},{'wavelength_um':8.660,'n':4.0043},
            {'wavelength_um':9.720,'n':4.0034},{'wavelength_um':11.04,'n':4.0026},
            {'wavelength_um':12.00,'n':4.0023},{'wavelength_um':13.02,'n':4.0021}],
        'density_kg_m3':5330.0, 'cte_per_K':6.1e-6,
        'thermal_conductivity_W_mK':58.62, 'youngs_modulus_Pa':102.7e9,
        'note':'Manufacturer handbook n samples; k is not combined with n here because absorption is wavelength dependent. Do not assume k=0.'},
}


def reference_materials():
    return deepcopy(_DATABASE)
