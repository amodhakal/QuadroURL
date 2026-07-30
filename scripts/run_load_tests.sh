#!/usr/bin/env bash
#
# run_load_tests.sh
#
# Automates a clean, repeatable Locust benchmark sweep against the app:
#   - Full docker compose reset (down -v / up --build -d) before EACH level
#   - Waits for Postgres + app /health to be ready before starting load
#   - Runs Locust headless at each concurrency level with a controlled spawn rate
#   - Saves CSV results per level so runs are comparable apples-to-apples
#
# Usage:
#   ./run_load_tests.sh
#   ./run_load_tests.sh --levels 500,1000,2000 --spawn-rate 10 --run-time 3m
#
# Requires: docker compose, uvx (or locust installed), curl

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults (override via flags)
# ---------------------------------------------------------------------------
LEVELS="500,1000,2000"
SPAWN_RATE=10
RUN_TIME=""                        # if set (e.g. "3m"), used as a FIXED run-time for every level, overriding the auto-computed one below
PEAK_TIME=240                       # seconds of steady-state load AFTER ramp-up completes, per level (only used when RUN_TIME is unset)
HOST="http://127.0.0.1"        
LOCUST_FILE="scripts/test_locust.py"
HEALTH_URL="${HOST}/health"
RESULTS_DIR="loadtests/results_$(date +%Y%m%d_%H%M%S)"
HEALTH_TIMEOUT=120                # seconds to wait for app to come up
POST_UP_SETTLE=5                  # small buffer after health check passes

# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --levels) LEVELS="$2"; shift 2 ;;
    --spawn-rate) SPAWN_RATE="$2"; shift 2 ;;
    --run-time) RUN_TIME="$2"; shift 2 ;;
    --peak-time) PEAK_TIME="$2"; shift 2 ;;
    --host) HOST="$2"; HEALTH_URL="${HOST}/health"; shift 2 ;;
    --locust-file) LOCUST_FILE="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--levels 500,1000,2000] [--spawn-rate 10] [--peak-time 60] [--run-time 3m] [--host http://127.0.0.1] [--locust-file scripts/test_locust.py]"
      echo ""
      echo "By default, run-time is computed PER LEVEL as (ramp-up time to reach that many users) + --peak-time"
      echo "seconds of steady-state load, so higher user counts automatically get a longer test instead of"
      echo "spending most of a fixed window still ramping up. Pass --run-time explicitly to override this and"
      echo "use one fixed duration for every level instead."
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1
      ;;
  esac
done

mkdir -p "${RESULTS_DIR}"
SUMMARY_FILE="${RESULTS_DIR}/summary.txt"
echo "Load test sweep started $(date)" | tee "${SUMMARY_FILE}"
if [[ -n "${RUN_TIME}" ]]; then
  echo "Levels: ${LEVELS} | spawn-rate: ${SPAWN_RATE} | run-time: ${RUN_TIME} (fixed, applied to every level) | host: ${HOST}" | tee -a "${SUMMARY_FILE}"
else
  echo "Levels: ${LEVELS} | spawn-rate: ${SPAWN_RATE} | run-time: auto (ramp-up-time + ${PEAK_TIME}s peak, per level) | host: ${HOST}" | tee -a "${SUMMARY_FILE}"
fi
echo "" | tee -a "${SUMMARY_FILE}"

wait_for_health() {
  echo "Waiting for ${HEALTH_URL} to respond healthy (timeout ${HEALTH_TIMEOUT}s)..."
  local waited=0
  until curl -sf "${HEALTH_URL}" > /dev/null 2>&1; do
    sleep 2
    waited=$((waited + 2))
    if [[ "${waited}" -ge "${HEALTH_TIMEOUT}" ]]; then
      echo "ERROR: app did not become healthy within ${HEALTH_TIMEOUT}s" >&2
      exit 1
    fi
  done
  echo "App healthy after ~${waited}s."
  sleep "${POST_UP_SETTLE}"
}

reset_stack() {
  echo ""
  echo "=== Resetting stack (down -v && up --build -d) ==="
  docker compose down -v
  docker compose up --build -d
  wait_for_health
}

run_locust_level() {
  local users="$1"
  local csv_prefix="${RESULTS_DIR}/results_${users}"

  local run_time_for_level
  if [[ -n "${RUN_TIME}" ]]; then
    run_time_for_level="${RUN_TIME}"
  else
    # Ramp-up time = time (seconds) for Locust to spawn all `users` at SPAWN_RATE
    # users/sec, rounded up. Then add PEAK_TIME seconds of actual steady-state
    # load at full concurrency, so every level gets a comparable peak window
    # instead of a fixed total duration that's mostly ramp-up at high user counts.
    local ramp_seconds=$(( (users + SPAWN_RATE - 1) / SPAWN_RATE ))  # ceil division
    local total_seconds=$(( ramp_seconds + PEAK_TIME ))
    run_time_for_level="${total_seconds}s"
    echo "Auto run-time for ${users} users: ramp-up ~${ramp_seconds}s + ${PEAK_TIME}s peak = ${run_time_for_level}"
  fi

  echo ""
  echo "=== Running Locust: ${users} users | spawn-rate ${SPAWN_RATE} | run-time ${run_time_for_level} ==="

  local start_ts
  start_ts=$(date +%s)

  # Locust exits 1 whenever a run had ANY failures (including expected,
  # transient ramp-up 502s) -- that's a signal we inspect via the CSVs, not
  # something that should abort the whole sweep. Capture the exit code
  # without letting `set -e` kill the script.
  set +e
  uvx locust -f "${LOCUST_FILE}" --host "${HOST}" \
    --users "${users}" --spawn-rate "${SPAWN_RATE}" --run-time "${run_time_for_level}" \
    --headless --csv="${csv_prefix}" --csv-full-history \
    --logfile "${csv_prefix}_locust.log"
  local locust_exit=$?
  set -e

  if [[ "${locust_exit}" -ne 0 ]]; then
    echo "NOTE: locust exited with code ${locust_exit} for ${users} users (non-zero just means the run had failures -- check ${csv_prefix}_failures.csv, this is expected for ramp-up 502s)."
  fi

  local end_ts
  end_ts=$(date +%s)
  local elapsed=$((end_ts - start_ts))

  echo "Level ${users} complete in ${elapsed}s (target run-time was ${run_time_for_level}). CSVs: ${csv_prefix}_stats.csv, ${csv_prefix}_failures.csv, ${csv_prefix}_stats_history.csv"

  # Pull the aggregated row out of the stats CSV for a quick eyeballed summary
  if [[ -f "${csv_prefix}_stats.csv" ]]; then
    echo "--- ${users} users (run-time ${run_time_for_level}): Aggregated row ---" | tee -a "${SUMMARY_FILE}"
    { head -n 1 "${csv_prefix}_stats.csv"; grep -i "^\"\?Aggregated" "${csv_prefix}_stats.csv" || true; } \
      | column -t -s, | tee -a "${SUMMARY_FILE}"
    echo "" | tee -a "${SUMMARY_FILE}"
  fi
}

# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------
IFS=',' read -ra LEVEL_ARR <<< "${LEVELS}"

for users in "${LEVEL_ARR[@]}"; do
  reset_stack
  run_locust_level "${users}"
done

echo ""
echo "=== Sweep complete ===" | tee -a "${SUMMARY_FILE}"
echo "Results directory: ${RESULTS_DIR}" | tee -a "${SUMMARY_FILE}"
echo "Review ${SUMMARY_FILE} for a quick aggregated comparison across levels."
echo "Grafana: http://localhost:3000 (check the golden-signals dashboard time range for each run's window)"
