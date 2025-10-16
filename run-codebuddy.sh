#!/usr/bin/env bash
set -e

# -------------------------------
# Common variables
# -------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/main.py"
CONDA_EXCLUDE_ENV="base"

# -------------------------------
# Functions (reuse from conda.sh)
# -------------------------------
check_conda() {
    if ! command -v conda >/dev/null 2>&1; then
        echo "Error: Conda not found. Please install Conda first."
        exit 1
    fi
}

select_conda_env() {
    # Use current env if set and not base
    if [ -n "$CONDA_DEFAULT_ENV" ] && [ "$CONDA_DEFAULT_ENV" != "$CONDA_EXCLUDE_ENV" ]; then
        ENV_NAME="$CONDA_DEFAULT_ENV"
        echo "Using current Conda environment: $ENV_NAME"
        return
    fi
    
    # List environments excluding base
    mapfile -t ENV_LIST < <(
        conda env list \
        | awk 'NR>2 {gsub(/\*/,""); print $1}' \
        | grep -v "^$CONDA_EXCLUDE_ENV$" \
        | grep -v "^#" \
        | grep -v "^$"
    )
    
    if [ ${#ENV_LIST[@]} -eq 0 ]; then
        echo "No Conda environments found besides $CONDA_EXCLUDE_ENV. Please create one first."
        exit 1
    fi
    
    echo "Select a Conda environment to activate (skip $CONDA_EXCLUDE_ENV):"
    select ENV_NAME in "${ENV_LIST[@]}"; do
        if [ -n "$ENV_NAME" ]; then
            echo "Activating Conda environment: $ENV_NAME"
            eval "$(conda shell.bash hook)"
            conda activate "$ENV_NAME"
            break
        else
            echo "Invalid selection."
        fi
    done
}

# -------------------------------
# Main
# -------------------------------
check_conda
select_conda_env

# Verify Python exists
if ! command -v python >/dev/null 2>&1; then
    echo "Error: Python not found in environment $ENV_NAME."
    exit 1
fi

# Launch frontend script
exec python "$PYTHON_SCRIPT"