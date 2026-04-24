#!/bin/bash

cd $(dirname "$0")
python=".venv/bin/python"

exec env PYTHONPATH=src ${python} -P -c "from memect.cli import main;main()" "$@"
