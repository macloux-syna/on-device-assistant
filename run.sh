#!/bin/bash

set -e

# Activate venv
source .venv/bin/activate

# Run the assistant
python assistant.py --qa-file=data/qa_vodafone.json
