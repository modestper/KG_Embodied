#!/bin/bash

source /data/zc/apps/miniconda3/etc/profile.d/conda.sh
conda activate lade

exec "$@"