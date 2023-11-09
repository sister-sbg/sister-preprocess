#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SISTER
Space-based Imaging Spectroscopy and Thermal PathfindER
Author: Adam Chlus

"""

import glob
import os
import shutil
import sys
import tarfile
import json
import hytools as ht
from hytools.io import parse_envi_header, write_envi_header
import numpy as np
from PIL import Image
from sister.sensors import prisma,aviris,desis,emit
from sister.utils import download_file


def main():

    pge_path = os.path.dirname(os.path.realpath(__file__))

    run_config_json = sys.argv[1]

    with open(run_config_json, 'r') as in_file:
        run_config = json.load(in_file)

    base_name = os.path.basename(run_config['inputs']['raw_dataset'])

    experimental = run_config['inputs']['experimental']

    os.mkdir('output')
    os.mkdir('temp')

    aws_cop_url='https://copernicus-dem-30m.s3.amazonaws.com/'

    if base_name.startswith('PRS'):

        smile = f'{pge_path}/data/prisma/PRISMA_Mali1_wavelength_shift_surface_smooth.npz'
        rad_coeff = f'{pge_path}/data/prisma/PRS_Mali1_radcoeff_surface.npz'

        landsat_directory = os.path.dirname(run_config['inputs']['raw_dataset']).replace('raw', 'landsat_reference')
        landsat_url=f'{landsat_directory}/PRS_{base_name[16:50]}_landsat.tar.gz'
        landsat_tar = 'input/%s' % os.path.basename(landsat_url)

        download_file(landsat_tar,
                      landsat_url)

        with tarfile.open(landsat_tar, 'r') as tar_ref:
            tar_ref.extractall('input')

        landsat = landsat_tar[:-7]

        prisma.he5_to_envi(f'input/{base_name}',
                            'output/',
                            'temp/',
                            aws_cop_url,
                            shift = smile,
                            rad_coeff =rad_coeff,
                            proj = True,
                            match=landsat)

        l1p_dir = glob.glob('output/PRS*')[0]
        datetime = '%sT%s' %  (base_name[16:24],base_name[24:30])
        sensor = 'PRISMA'

    elif base_name.startswith('ang') or base_name.startswith('f'):
        aviris.preprocess(f'input/{base_name}',
                            'output/',
                            'temp/',
                            res = 30)
        l1p_dir = glob.glob('output/S*')[0]
        sensor = os.path.basename(l1p_dir).split('_')[1]

    elif base_name.startswith('DESIS'):
        desis.l1c_process(f'input/{base_name}',
                            'output/',
                            'temp/',
                            aws_cop_url)

        l1p_dir = glob.glob('output/DESIS*')[0]

        # Get starting time of image acquisition
        header_file = glob.glob(l1p_dir + '/*rdn_prj.hdr')[0]
        header = parse_envi_header(header_file)
        datetime =(header['start acquisition time'].replace('-','').replace(':','')[:-1]).upper()
        sensor = 'DESIS'

    elif base_name.startswith('EMIT'):

        emit_directory = os.path.dirname(run_config['inputs']['raw_dataset'])
        obs_base_name = base_name.replace('RAD','OBS')
        obs_url = f'{emit_directory}/{obs_base_name}'
        obs_nc = f'input/{obs_base_name}'

        download_file(obs_nc,
                      obs_url)

        emit.nc_to_envi(f'input/{base_name}',
                            'output/',
                            'temp/',
                            obs_file = obs_nc,
                            export_loc = True,
                            crid = str(run_config['inputs']['crid']))
        sensor = 'EMIT'
    else:
        print("Unrecognized input sensor")

    #Rename DESIS and PRISMA output files
    if sensor in ['PRISMA','DESIS']:
        for file in glob.glob('%s/*' % l1p_dir):
            if ('loc' in file) & ('.csv' not in file):
                product = '_LOC'
            elif 'obs' in file:
                product = '_OBS'
            elif'rdn' in file:
                product = ''
            else:
                continue
            ext = os.path.splitext(file)[-1]
            if ext == '':
                ext = '.bin'

            old_file = os.path.basename(file)
            new_file = f'SISTER_{sensor}_L1B_RDN_{datetime}_CRID{product}{ext}'

            os.rename('%s/%s' % (l1p_dir,old_file),
                      'output/%s' % (new_file))
        shutil.rmtree(l1p_dir)

    for dataset in glob.glob("output/SISTER*.bin"):
        generate_metadata(dataset.replace('.bin','.hdr'),
                                  'output/', experimental)

    #Update crid
    for file in glob.glob("output/SISTER*"):
        os.rename(file,file.replace('CRID',
                                        str(run_config['inputs']['crid'])))

    rdn_file =  glob.glob("output/*%s.bin" % run_config['inputs']['crid'])[0]
    generate_quicklook(rdn_file,'output/')

    shutil.copyfile(run_config_json,
                    'output/%s.runconfig.json' % os.path.basename(rdn_file)[:-4])

    shutil.copyfile('run.log',
                    'output/%s.log' % os.path.basename(rdn_file)[:-4])


    # If experimental, prefix filenames with "EXPERIMENTAL-"
    if experimental:
        for file in glob.glob(f"output/SISTER*"):
            shutil.move(file, f"output/EXPERIMENTAL-{os.path.basename(file)}")


def generate_quicklook(input_file,output_dir):

    img = ht.HyTools()
    img.read_file(input_file)
    image_file =f"{output_dir}/{img.base_name}.png"

    if 'DESIS' in img.base_name:
        band3 = img.get_wave(560)
        band2 = img.get_wave(850)
        band1 = img.get_wave(660)
    else:
        band3 = img.get_wave(560)
        band2 = img.get_wave(850)
        band1 = img.get_wave(1660)

    rgb=  np.stack([band1,band2,band3])
    rgb[rgb == img.no_data] = np.nan

    rgb = np.moveaxis(rgb,0,-1).astype(float)
    bottom = np.nanpercentile(rgb,5,axis = (0,1))
    top = np.nanpercentile(rgb,95,axis = (0,1))
    rgb = np.clip(rgb,bottom,top)
    rgb = (rgb-np.nanmin(rgb,axis=(0,1)))/(np.nanmax(rgb,axis= (0,1))-np.nanmin(rgb,axis= (0,1)))
    rgb = (rgb*255).astype(np.uint8)

    im = Image.fromarray(rgb)
    im.save(image_file)


def generate_metadata(header_file,output_dir,experimental):

    header = parse_envi_header(header_file)
    # First update the description with disclaimer if experimental
    if experimental:
        header['description'] = header['description'].capitalize() + \
                                " (DISCLAIMER: THIS DATA IS EXPERIMENTAL AND NOT INTENDED FOR SCIENTIFIC USE)"
        write_envi_header(header_file, header)
    base_name =os.path.basename(header_file)[:-4]

    metadata = {}
    metadata['sensor'] = header['sensor type'].upper()
    metadata['start_time'] = header['start acquisition time'].upper()
    metadata['end_time'] = header['end acquisition time'].upper()

    # Split corner coordinates string into list
    coords = [float(x) for x in header['bounding box'].replace(']','').replace('[','').split(',')]

    metadata['bounding_box'] = [list(x) for x in zip(coords[::2],coords[1::2])]
    metadata['product'] = base_name.split('_')[4]
    metadata['processing_level'] = base_name.split('_')[2]

    config_file = f'{output_dir}/{base_name}.met.json'

    with open(config_file, 'w') as outfile:
        json.dump(metadata,outfile,indent=3)


if __name__=='__main__':

    main()
