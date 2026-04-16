#!/bin/bash
# Synthesize baseline Verilog designs with Yosys (sky130hd) to measure synth pass rate and area.
# Usage: bash run_baseline_synth.sh

set -uo pipefail

BASELINE_DIR="/home/xiongjing/sparkle/results/baseline_verilog_20260411_022921"
OUTDIR="/home/xiongjing/sparkle/results/baseline_synth_$(date +%Y%m%d_%H%M%S)"
LIBERTY="/OpenROAD-flow-scripts/flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
DOCKER_IMAGE="openroad/orfs:latest"

mkdir -p "$OUTDIR"

total=0
synth_pass=0
synth_fail=0
results_file="$OUTDIR/results.jsonl"

echo "=== Baseline Synthesis Experiment ==="
echo "Input:  $BASELINE_DIR"
echo "Output: $OUTDIR"
echo ""

for sv in "$BASELINE_DIR"/Prob*.sv; do
    prob_id=$(basename "$sv" .sv)
    total=$((total + 1))

    # Run Yosys inside Docker
    log_file="$OUTDIR/${prob_id}_synth.log"
    docker run --rm \
        -v "$BASELINE_DIR:/input:ro" \
        -v "$OUTDIR:/output" \
        "$DOCKER_IMAGE" \
        yosys -p "
            read_verilog -sv /input/${prob_id}.sv;
            hierarchy -top TopModule;
            proc; opt; fsm; opt; memory; opt;
            techmap; opt;
            dfflibmap -liberty $LIBERTY;
            abc -liberty $LIBERTY;
            clean;
            stat -liberty $LIBERTY;
        " > "$log_file" 2>&1
    exit_code=$?

    # Extract area and cell count
    area=""
    cells=""
    if [ $exit_code -eq 0 ]; then
        area=$(grep -oP 'Chip area for module.*:\s*\K[0-9.]+' "$log_file" || true)
        cells=$(grep -oP 'Number of cells:\s*\K[0-9]+' "$log_file" || true)
        # Yosys exit 0 means synthesis succeeded; pure wire designs have no cells/area
        synth_pass=$((synth_pass + 1))
        echo "{\"prob_id\":\"$prob_id\",\"synth_pass\":true,\"area_um2\":${area:-0},\"cell_count\":${cells:-0}}" >> "$results_file"
        printf "  %-40s PASS  area=%-10s cells=%s\n" "$prob_id" "${area:-0}" "${cells:-0}"
    else
        synth_fail=$((synth_fail + 1))
        err=$(tail -1 "$log_file" | tr '"' "'")
        echo "{\"prob_id\":\"$prob_id\",\"synth_pass\":false,\"error\":\"$err\"}" >> "$results_file"
        printf "  %-40s FAIL  (exit %d)\n" "$prob_id" "$exit_code"
    fi
done

# Write summary
cat > "$OUTDIR/summary.json" << EOF
{
  "experiment": "baseline_verilog_synth",
  "total": $total,
  "synth_pass": $synth_pass,
  "synth_fail": $synth_fail,
  "synth_rate": "$(python3 -c "print(f'{$synth_pass/$total*100:.1f}%')")"
}
EOF

echo ""
echo "=== Summary ==="
echo "Total:     $total"
echo "Synth pass: $synth_pass ($( python3 -c "print(f'{$synth_pass/$total*100:.1f}%')" ))"
echo "Synth fail: $synth_fail"
echo "Results:   $OUTDIR"
