#!/usr/bin/env bash
# Hangang CPU full-suite verification. This script does not claim engineering
# or field qualification; the runner records test evidence and limitations.
# Submit from the project scratch root after staging the pinned source archive,
# wheelhouse, and SHA256SUMS files.
#SBATCH --job-name=pipe-physical
#SBATCH --partition=5thEx_cpu
#SBATCH --nodes=1
#SBATCH --ntasks=16
#SBATCH --cpus-per-task=1
#SBATCH --time=12:00:00
#SBATCH --comment="field=che;appl=in_house"
#SBATCH --output=pipe-physical-%j.out
#SBATCH --error=pipe-physical-%j.err

set -Eeuo pipefail

PROJECT_ROOT="${PIPELINE_HPC_ROOT:?Set PIPELINE_HPC_ROOT to your staged validation root}"
PROJECT_SOURCE_ARCHIVE="$PROJECT_ROOT/hangang-source-v4.tar.gz"
PROJECT_SOURCE_SHA256="d1e631c815d821e1e577b50eb8a3d7be7545cf5e064398de01530986fb87c85e"
EXTERNAL_ARCHIVE="$PROJECT_ROOT/hangang-external-sources-runtime-v3.tar.gz"
RAW_ARCHIVE="$PROJECT_ROOT/hangang-raw-data-v2.tar.gz"
SOURCE_MAP="$PROJECT_ROOT/hangang-source-map-runtime-v3.json"
DATA_MAP="$PROJECT_ROOT/hangang-data-map-v2.json"
TRANSFER_PLAN="$PROJECT_ROOT/hangang-data-plan-v2.json"
WHEELHOUSE="$PROJECT_ROOT/wheelhouse"
RUN_ROOT="$PROJECT_ROOT/runs/$SLURM_JOB_ID"
SOURCE_ARCHIVE="$RUN_ROOT/pipeline-integrity-source-$SLURM_JOB_ID.tar.gz"
LOCAL_ROOT="$RUN_ROOT/work"
DEPENDENCY_ROOT="${SLURM_TMPDIR:-/tmp}/pipeline-integrity-deps-$SLURM_JOB_ID"

mkdir -p "$RUN_ROOT/results"
mkdir -p "$LOCAL_ROOT"

finish() {
  rc=$?
  trap - EXIT
  set +e
  if [ -d "$LOCAL_ROOT/results" ]; then
    cp -a "$LOCAL_ROOT/results/." "$RUN_ROOT/results/"
  fi
  printf '%s\n' "$rc" > "$RUN_ROOT/job_exit_status"
  date -Is > "$RUN_ROOT/finished_at"
  exit "$rc"
}
trap finish EXIT

module load cray-python/3.12.12
python3 --version > "$RUN_ROOT/python-version.txt"
printf '%s  %s\n' "$PROJECT_SOURCE_SHA256" "$PROJECT_SOURCE_ARCHIVE" | sha256sum -c - > "$RUN_ROOT/project-source-integrity.txt"
sha256sum -c "$PROJECT_ROOT/hangang-payloads.SHA256SUMS" > "$RUN_ROOT/payload-integrity.txt"
(cd "$WHEELHOUSE" && sha256sum -c "$PROJECT_ROOT/wheelhouse.SHA256SUMS") > "$RUN_ROOT/wheel-integrity-base.txt"
cp "$PROJECT_ROOT"/extra-wheels/*.whl "$WHEELHOUSE"/
(cd "$WHEELHOUSE" && sha256sum *.whl > "$PROJECT_ROOT/wheelhouse.SHA256SUMS")
(cd "$WHEELHOUSE" && sha256sum -c "$PROJECT_ROOT/wheelhouse.SHA256SUMS") > "$RUN_ROOT/wheel-integrity.txt"

mkdir -p "$PROJECT_ROOT/owners" "$PROJECT_ROOT/data/raw"
tar -xzf "$EXTERNAL_ARCHIVE" -C "$PROJECT_ROOT"
tar -xzf "$RAW_ARCHIVE" -C "$PROJECT_ROOT/data"

mkdir "$LOCAL_ROOT/project"
tar -xzf "$PROJECT_SOURCE_ARCHIVE" -C "$LOCAL_ROOT/project"
cd "$LOCAL_ROOT/project/pipeline-integrity-toolkit"

python3 scripts/hpc_tests.py bundle --output "$SOURCE_ARCHIVE" \
  > "$RUN_ROOT/source-bundle.log" 2>&1
SOURCE_SHA256="$(sha256sum "$SOURCE_ARCHIVE" | awk '{print $1}')"
printf '%s  %s\n' "$SOURCE_SHA256" "$SOURCE_ARCHIVE" | sha256sum -c - \
  > "$RUN_ROOT/source-integrity.txt"

python3 -m pip install \
  --disable-pip-version-check \
  --no-index \
  --find-links "$WHEELHOUSE" \
  --target "$DEPENDENCY_ROOT" \
  --requirement requirements-tested.txt \
  > "$RUN_ROOT/dependency-install.log" 2>&1

# The monitoring owner's public package entry point imports its scaler eagerly.
# Freeze and checksum the small integration wheels so this online dependency is
# still reproducible from the run record while the main scientific stack stays
# offline and pinned in the wheelhouse.
MONITORING_WHEELHOUSE="$RUN_ROOT/monitoring-wheels"
mkdir -p "$MONITORING_WHEELHOUSE"
python3 -m pip download \
  --disable-pip-version-check \
  --only-binary=:all: \
  --no-deps \
  --index-url https://pypi.org/simple \
  --dest "$MONITORING_WHEELHOUSE" \
  scikit-learn==1.9.1 joblib==1.6.0 narwhals==2.28.0 threadpoolctl==3.7.0 \
  > "$RUN_ROOT/monitoring-wheel-download.log" 2>&1
sha256sum "$MONITORING_WHEELHOUSE"/*.whl > "$RUN_ROOT/monitoring-wheel-integrity.txt"
python3 -m pip install \
  --disable-pip-version-check \
  --no-index \
  --no-deps \
  --find-links "$MONITORING_WHEELHOUSE" \
  --target "$DEPENDENCY_ROOT" \
  scikit-learn==1.9.1 joblib==1.6.0 narwhals==2.28.0 threadpoolctl==3.7.0 \
  > "$RUN_ROOT/monitoring-dependency-install.log" 2>&1
python3 -m pip list --path "$DEPENDENCY_ROOT" --format=freeze | sort \
  > "$RUN_ROOT/dependency-freeze.txt"

python3 -m pip install \
  --disable-pip-version-check \
  --no-index \
  --no-deps \
  --no-build-isolation \
  --target "$DEPENDENCY_ROOT" \
  "$PROJECT_ROOT/owners/lh2dt_models" \
  "$PROJECT_ROOT/owners/human_reliability" \
  "$PROJECT_ROOT/owners/process_monitoring" \
  "$PROJECT_ROOT/owners/matintegrity" \
  "$PROJECT_ROOT/owners/prism" \
  > "$RUN_ROOT/external-owner-install.log" 2>&1

export PYTHONPATH="$DEPENDENCY_ROOT"
export PIPELINE_HPC_TRUSTED_PYTHONPATH="$DEPENDENCY_ROOT"
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export PYTHONHASHSEED=0
export PYTHONIOENCODING=utf-8
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

RESULTS="$LOCAL_ROOT/results"
mkdir "$RESULTS"
PLAN="$RESULTS/manifest.json"
python3 scripts/hpc_tests.py prepare --shards "$SLURM_NTASKS" --manifest "$PLAN" \
  --source-map "$SOURCE_MAP" --data-map "$DATA_MAP" \
  > "$RUN_ROOT/prepare.log" 2>&1

launch_status=0
# The test workers use Slurm rank environment variables, not MPI collectives.
# Run independent singleton MPI worlds so optional serial FiPy solvers can load
# safely on whichever rank owns the soil cross-section tests.
srun --mpi=none --ntasks="$SLURM_NTASKS" --cpu-bind=cores \
  python3 scripts/hpc_tests.py run --manifest "$PLAN" --results "$RESULTS" \
  --source-map "$SOURCE_MAP" --data-map "$DATA_MAP" \
  > "$RUN_ROOT/launcher.log" 2>&1 || launch_status=$?

aggregate_status=0
python3 scripts/hpc_tests.py aggregate --manifest "$PLAN" --results "$RESULTS" \
  > "$RUN_ROOT/aggregate.log" 2>&1 || aggregate_status=$?

if [ "$launch_status" -ne 0 ] || [ "$aggregate_status" -ne 0 ]; then
  exit 1
fi
