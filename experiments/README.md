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

## Datasets

Benchmark data is not tracked in git, except RTLLM, which is vendored. The
loader in `agent/dataset.py` looks in these places:

| Dataset | Location | Setup |
|---|---|---|
| `verilogeval` | `verilog-eval/dataset_spec-to-rtl` | `git clone https://github.com/NVlabs/verilog-eval.git`, pinned at `c498220` |
| `rtllm` | `RTLLM/` | Vendored copy of RTLLM v2.0 (upstream commit `41b2689`) |
| `resbench` | `ResBench/` or `benchmarks/ResBench/` | Override with `RESBENCH_ROOT` |
| `cvdp` | `cvdp-benchmark-dataset/cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl` | Override with `CVDP_DATASET_FILE` |
