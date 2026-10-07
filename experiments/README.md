# Experiments

Entry points for the current NL2Chip evaluation tree. Dataset dumps, result jsonl
trees, and API keys are not in git.

## Archon Verilog baseline (direct SystemVerilog)

This is the CktArchon harness writing `candidate.sv` instead of Lean:

```bash
# wrapper
experiments/run_archon_verilog.sh --dataset verilogeval --workers 4

# equivalent
python3 -m cktarchon.run_verilog --dataset verilogeval --workers 4
```

Datasets: `verilogeval`, `rtllm`, `resbench`, `cvdp`, `realbench`.
Outputs go under `results/archon_verilog_run_*`.

The older one-shot VerilogEval-style baseline (no Archon tools) is still:

```bash
experiments/05_baseline_verilog_direct.sh
python3 experiments/baseline_verilog.py
python3 experiments/baseline_verilog_iterative.py
```

## CKTLean / CktArchon (Lean → Verilog)

```bash
python3 -m cktarchon.run --dataset verilogeval
```

See `docs/CktArchon_Replacement.md`.
