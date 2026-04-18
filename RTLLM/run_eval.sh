#!/usr/bin/env bash
# ============================================================================
# RTLLM Benchmark Evaluation Script (iverilog-based)
#
# Usage:
#   ./run_eval.sh <results_dir>
#   ./run_eval.sh _chatgpt35          # evaluate ChatGPT-3.5 baseline
#   ./run_eval.sh /path/to/my_results # evaluate custom results
#
# Expected results_dir layout:
#   <results_dir>/t1/adder_8bit.v
#   <results_dir>/t1/accu.v
#   ...
#   <results_dir>/t2/adder_8bit.v
#   ...
#
# Each .v file should contain a module whose name matches the filename
# (without .v extension). The script compiles it against the corresponding
# RTLLM testbench using iverilog and checks for "Your Design Passed".
# ============================================================================

set -euo pipefail

RTLLM_ROOT="$(cd "$(dirname "$0")" && pwd)"
WORK_DIR=$(mktemp -d /tmp/rtllm_eval.XXXXXX)
trap 'rm -rf "$WORK_DIR"' EXIT

SIM_TIMEOUT=30  # seconds per simulation

# ---------- argument parsing ----------
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <results_dir>"
    echo "  results_dir: path to directory containing t1/, t2/, ... trial folders"
    exit 1
fi

RESULTS_DIR="$1"
# Allow relative path from RTLLM_ROOT (e.g. "_chatgpt35")
if [[ ! -d "$RESULTS_DIR" ]]; then
    RESULTS_DIR="${RTLLM_ROOT}/${1}"
fi
if [[ ! -d "$RESULTS_DIR" ]]; then
    echo "ERROR: results directory not found: $1"
    exit 1
fi

# ---------- build design_name -> testbench_dir map ----------
declare -A TB_MAP
while IFS= read -r tb_path; do
    design_dir="$(dirname "$tb_path")"
    design_name="$(basename "$design_dir")"
    TB_MAP["$design_name"]="$design_dir"
done < <(find "$RTLLM_ROOT" -name "testbench.v" -not -path "*/_chatgpt*" -not -path "*/_pic*")

echo "Found ${#TB_MAP[@]} designs with testbenches in RTLLM benchmark."
echo "Results directory: $RESULTS_DIR"
echo "========================================"

# ---------- counters ----------
total_designs=0
total_trials=0
total_compile_pass=0
total_sim_pass=0

declare -A DESIGN_COMPILE_PASS
declare -A DESIGN_SIM_PASS
declare -A DESIGN_TRIALS

# ---------- discover trials ----------
TRIALS=()
for trial_dir in "$RESULTS_DIR"/t*/; do
    [[ -d "$trial_dir" ]] && TRIALS+=("$trial_dir")
done

if [[ ${#TRIALS[@]} -eq 0 ]]; then
    echo "ERROR: no trial directories (t1/, t2/, ...) found in $RESULTS_DIR"
    exit 1
fi
echo "Found ${#TRIALS[@]} trials: $(printf '%s ' "${TRIALS[@]}" | sed "s|$RESULTS_DIR/||g")"
echo "========================================"

# ---------- evaluate ----------
for trial_dir in "${TRIALS[@]}"; do
    trial_name="$(basename "$trial_dir")"

    for vfile in "$trial_dir"/*.v; do
        [[ -f "$vfile" ]] || continue
        fname="$(basename "$vfile" .v)"

        # Handle known naming mismatches (e.g. calender.v -> calendar testbench)
        lookup_name="$fname"
        if [[ "$fname" == "calender" ]]; then
            lookup_name="calendar"
        fi

        tb_dir="${TB_MAP[$lookup_name]:-}"
        if [[ -z "$tb_dir" ]]; then
            echo "  [$trial_name] $fname: SKIP (no testbench found)"
            continue
        fi

        # Track design
        DESIGN_TRIALS["$lookup_name"]=$(( ${DESIGN_TRIALS[$lookup_name]:-0} + 1 ))

        tb_file="$tb_dir/testbench.v"
        run_dir="$WORK_DIR/${trial_name}_${fname}"
        mkdir -p "$run_dir"

        # Compile with iverilog
        compile_ok=0
        if iverilog -g2012 -o "$run_dir/sim.vvp" "$vfile" "$tb_file" > "$run_dir/compile.log" 2>&1; then
            compile_ok=1
        fi

        if [[ $compile_ok -eq 1 ]]; then
            DESIGN_COMPILE_PASS["$lookup_name"]=$(( ${DESIGN_COMPILE_PASS[$lookup_name]:-0} + 1 ))
            total_compile_pass=$((total_compile_pass + 1))

            # Run simulation with timeout
            sim_output=""
            sim_ok=0
            if sim_output=$(timeout "$SIM_TIMEOUT" vvp "$run_dir/sim.vvp" 2>&1); then
                :
            fi
            echo "$sim_output" > "$run_dir/sim.log"

            if echo "$sim_output" | grep -q "Your Design Passed"; then
                sim_ok=1
                DESIGN_SIM_PASS["$lookup_name"]=$(( ${DESIGN_SIM_PASS[$lookup_name]:-0} + 1 ))
                total_sim_pass=$((total_sim_pass + 1))
            fi

            if [[ $sim_ok -eq 1 ]]; then
                echo "  [$trial_name] $fname: COMPILE_PASS | SIM_PASS"
            else
                echo "  [$trial_name] $fname: COMPILE_PASS | SIM_FAIL"
            fi
        else
            echo "  [$trial_name] $fname: COMPILE_FAIL"
        fi

        total_trials=$((total_trials + 1))
    done
done

# ---------- summary ----------
echo ""
echo "========================================"
echo "                SUMMARY"
echo "========================================"

# Count unique designs tested
for key in "${!DESIGN_TRIALS[@]}"; do
    total_designs=$((total_designs + 1))
done

echo "Designs tested:  $total_designs / ${#TB_MAP[@]}"
echo "Total trials:    $total_trials"
echo "Compile pass:    $total_compile_pass / $total_trials"
echo "Simulation pass: $total_sim_pass / $total_trials"
echo ""

# Per-design breakdown
echo "Per-design results (compile_pass/trials | sim_pass/trials):"
echo "----------------------------------------"
for design in $(echo "${!DESIGN_TRIALS[@]}" | tr ' ' '\n' | sort); do
    trials=${DESIGN_TRIALS[$design]}
    cp=${DESIGN_COMPILE_PASS[$design]:-0}
    sp=${DESIGN_SIM_PASS[$design]:-0}
    printf "  %-28s compile: %d/%d  sim: %d/%d\n" "$design" "$cp" "$trials" "$sp" "$trials"
done

# pass@1 calculation (using at-least-one heuristic across trials)
compile_at_least_one=0
sim_at_least_one=0
for design in "${!DESIGN_TRIALS[@]}"; do
    if [[ ${DESIGN_COMPILE_PASS[$design]:-0} -gt 0 ]]; then
        compile_at_least_one=$((compile_at_least_one + 1))
    fi
    if [[ ${DESIGN_SIM_PASS[$design]:-0} -gt 0 ]]; then
        sim_at_least_one=$((sim_at_least_one + 1))
    fi
done

echo ""
echo "Designs with at least 1 compile pass: $compile_at_least_one / $total_designs"
echo "Designs with at least 1 sim pass:     $sim_at_least_one / $total_designs"
