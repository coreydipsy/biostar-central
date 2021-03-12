#!/bin/bash

BATCH_SIZE=5000

# Load the conda commands.
source ~/miniconda3/etc/profile.d/conda.sh

export POSTGRES_HOST=/var/run/postgresql

# Activate the conda environemnt.
conda activate engine

# Set the configuration module.
export DJANGO_SETTINGS_MODULE=conf.run.site_settings

# Add BATCH_SIZE posts to search index
python manage.py index --size ${BATCH_SIZE}