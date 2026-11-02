#!/usr/bin/env bash
# Focused Hangang CPU campaign for the independently derived laminar gas path.
# JUnit evidence is solver/test verification, not field or engineering qualification.
#SBATCH --job-name=gas-plenum-check
#SBATCH --partition=5thEx_cpu
#SBATCH --nodes=1
#SBATCH --ntasks=7
#SBATCH --cpus-per-task=1
#SBATCH --time=02:00:00
#SBATCH --comment="field=che;appl=in_house"
#SBATCH --output=gas-plenum-%j.out
#SBATCH --error=gas-plenum-%j.err

set -Eeuo pipefail

PROJECT_ROOT="${PIPELINE_HPC_ROOT:?Set PIPELINE_HPC_ROOT to your staged validation root}"
STAGE_ROOT="$PROJECT_ROOT/hangang-gas-potential-wave2-deploy"
SOURCE_ARCHIVE="$STAGE_ROOT/hangang-gas-potential-source-v3.tar.gz"
SOURCE_SHA256="645c5170f922de1cbb10c8bcac4f3b7cea1ec0f773fbb75c3d0f73636f82147c"
TEST_DELTA_ARCHIVE="$STAGE_ROOT/gas-plenum-test-delta-v1.tar"
TEST_DELTA_SHA256="b0cdeb8b5bd955ad0d456b0925f145fa942f8eab241fbbac433caf3c952b4cbc"
SOURCE_MAP="$STAGE_ROOT/source-map-v2.json"
SOURCE_MAP_SHA256="f15eede2e1780d088cc4212a42982f8aaec768eb886e7afa7416c1baaf288f03"
EXPECTED_MANIFEST_DIGEST="315323129eb240d8e95a62dab2661943685f7435f3083b5280a18f3a535932f6"
WHEELHOUSE="$PROJECT_ROOT/wheelhouse"
RUN_ROOT="$PROJECT_ROOT/runs/$SLURM_JOB_ID"
LOCAL_ROOT="$RUN_ROOT/work"
DEPENDENCY_ROOT="${SLURM_TMPDIR:-/tmp}/gas-potential-deps-$SLURM_JOB_ID"
SELECTED_TESTS=(
  tests/test_gas_potential_analytic_validation.py
  tests/test_gas_potential_plenum_validation.py
  tests/test_gas_network.py
  tests/test_batch_transport.py
  tests/test_thermal_conservation_validation.py
  tests/test_thermal_coupling_analytic_validation.py
  tests/test_transient.py
)

mkdir -p "$RUN_ROOT/results" "$LOCAL_ROOT/project"

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
printf '%s  %s\n' "$SOURCE_SHA256" "$SOURCE_ARCHIVE" | sha256sum -c - \
  > "$RUN_ROOT/source-archive-integrity.txt"
printf '%s  %s\n' "$SOURCE_MAP_SHA256" "$SOURCE_MAP" | sha256sum -c - \
  > "$RUN_ROOT/source-map-integrity.txt"
(cd "$WHEELHOUSE" && sha256sum -c "$PROJECT_ROOT/wheelhouse.SHA256SUMS") \
  > "$RUN_ROOT/wheelhouse-integrity.txt"

tar -xzf "$SOURCE_ARCHIVE" -C "$LOCAL_ROOT/project"
printf '%s  %s\n' "$TEST_DELTA_SHA256" "$TEST_DELTA_ARCHIVE" | sha256sum -c - > "$RUN_ROOT/test-delta-integrity.txt"
tar -xf "$TEST_DELTA_ARCHIVE" -C "$LOCAL_ROOT/project/pipeline-integrity-toolkit"
cd "$LOCAL_ROOT/project/pipeline-integrity-toolkit"
python3 -m pip install --disable-pip-version-check --no-index \
  --find-links "$WHEELHOUSE" --find-links "$PROJECT_ROOT/extra-wheels" \
  --target "$DEPENDENCY_ROOT" --requirement requirements-tested.txt \
  > "$RUN_ROOT/dependency-install.log" 2>&1
python3 -m pip install --disable-pip-version-check --no-index --no-deps \
  --no-build-isolation --target "$DEPENDENCY_ROOT" \
  "$PROJECT_ROOT/owners/lh2dt_models" \
  > "$RUN_ROOT/lh2dt-install.log" 2>&1
python3 -m pip list --path "$DEPENDENCY_ROOT" --format=freeze | sort \
  > "$RUN_ROOT/dependency-freeze.txt"

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
PLAN="$RESULTS/manifest.json"
python3 scripts/hpc_tests.py prepare --shards "$SLURM_NTASKS" \
  --manifest "$PLAN" --source-map "$SOURCE_MAP" --only-tests "${SELECTED_TESTS[@]}" \
  > "$RUN_ROOT/prepare.log" 2>&1
ACTUAL_MANIFEST_DIGEST="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["manifest_digest"])' "$PLAN")"
if [ "$ACTUAL_MANIFEST_DIGEST" != "$EXPECTED_MANIFEST_DIGEST" ]; then
  printf 'manifest mismatch: %s\n' "$ACTUAL_MANIFEST_DIGEST" > "$RUN_ROOT/manifest-mismatch.txt"
  exit 1
fi

launch_status=0
srun --mpi=none --ntasks="$SLURM_NTASKS" --cpu-bind=cores \
  python3 scripts/hpc_tests.py run --manifest "$PLAN" --results "$RESULTS" \
  --source-map "$SOURCE_MAP" > "$RUN_ROOT/launcher.log" 2>&1 || launch_status=$?
aggregate_status=0
python3 scripts/hpc_tests.py aggregate --manifest "$PLAN" --results "$RESULTS" \
  > "$RUN_ROOT/aggregate.log" 2>&1 || aggregate_status=$?
if [ "$launch_status" -ne 0 ] || [ "$aggregate_status" -ne 0 ]; then
  exit 1
fi

