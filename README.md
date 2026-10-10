# CKTLean

**Natural language to chip, through a Lean 4 hardware DSL.**

CKTLean turns a natural-language hardware specification into SystemVerilog by
way of Lean 4. An LLM agent writes the design in a Lean-embedded HDL, the Lean
compiler type-checks it and emits SystemVerilog, and an evaluation backend
simulates, synthesizes and places-and-routes the result.

Because the intermediate artifact is a Lean program rather than Verilog text,
ill-typed circuits are rejected before any simulation runs, and the same file
can carry a specification and a machine-checked proof about the circuit.

CKTLean builds on [Sparkle HDL](https://github.com/Verilean/sparkle), and we
thank its authors for their contribution. The language reference is in
[docs/HDL_Guide.md](docs/HDL_Guide.md).

## How it works

```
natural-language spec
        │
        ▼
  agent (cktlean)  ──writes──▶  Generated/<prob_id>.lean
        ▲                                │
        │ compiler diagnostics           ▼
        └───────────────────────  Lean type-check (lean_check)
                                         │ passes
                                         ▼
                                 #synthesizeVerilog  ──▶  SystemVerilog
                                         │
                                         ▼
                      compile · lint · simulate against the benchmark testbench
                                         │ optional
                                         ▼
                      synthesis · place and route · DRC · LVS  (OpenROAD, sky130hd)
```

1. **Generate.** The agent reads the specification and writes one Lean file. It
   can call `lean_check` as often as its turn budget allows and repair the file
   from the compiler's diagnostics.
2. **Extract.** `#synthesizeVerilog` lowers the Lean definition to
   SystemVerilog.
3. **Evaluate.** The evaluator compiles the SystemVerilog and simulates it
   against the benchmark's own testbench.
4. **Implement.** Designs that pass simulation can go through synthesis, place
   and route, DRC and LVS.

## Repository layout

| Path | Contents |
|---|---|
| `Sparkle/`, `Sparkle.lean`, `c_src/` | The HDL: Signal DSL, elaborator, IR, SystemVerilog backend, verification library |
| `cktlean/` | Agent harness: `run.py` (Lean flow), `run_verilog.py` (direct-SystemVerilog baseline), tools, path guard, prompts |
| `agent/` | `evaluator.py` (scoring and backend flow), `dataset.py` (benchmark loaders), `lean_repl.py`, `orfs_runner.py`, `search.py` (PPA optimization and architecture exploration) |
| `experiments/` | Benchmark runners, baselines and post-hoc backend evaluation |
| `Tests/` | Python tests for the pipeline (`test_*.py`) and Lean tests for the HDL |
| `Generated/` | Where the agent writes Lean files; contents are not tracked |
| `Examples/`, `IP/`, `Tools/`, `firmware/`, `verilator/`, `hw/` | HDL examples and IP cores inherited from Sparkle |
| `docs/` | HDL guide, harness notes and IP documentation |

## Requirements

- [elan](https://github.com/leanprover/elan). The Lean version is pinned in
  `lean-toolchain`.
- Python 3.13 and [uv](https://docs.astral.sh/uv/).
- [Icarus Verilog](https://github.com/steveicarus/iverilog) (`iverilog`, `vvp`)
  for simulation.
- Docker with the `openroad/orfs` image, only for the synthesis and
  place-and-route stages.
- A model endpoint: an Anthropic API key for the default harness, or the Codex
  CLI plus an Archon checkout for `--harness codex-agent`.

## Setup

```bash
git clone <repository-url> cktlean
cd cktlean

lake build     # builds the HDL; the first build fetches Lean dependencies
uv sync        # creates .venv with the Python dependencies

echo 'ANTHROPIC_API_KEY=<your key>' > key.env   # key.env is ignored by git
```

Benchmark datasets are downloaded separately. See the
[Datasets](experiments/README.md#datasets) table for where each one goes.

## Running

Generate and evaluate one benchmark with the Lean flow:

```bash
uv run python -m cktlean.run --dataset verilogeval \
  --results-dir results/verilogeval --workers 4
```

`--dataset` is one of `verilogeval`, `rtllm`, `resbench`, `cvdp` or `realbench`.

Each invocation creates `cktlean_run_<timestamp>/` under `--results-dir` with:

- `results.jsonl`: one row per problem (`compile_pass`, `lint_pass`,
  `sim_status`, turn and token counts)
- `summary.json`: totals for that invocation
- `sv/`: the extracted SystemVerilog
- `logs/`: the agent's event log for each problem

Commonly used options:

| Option | Effect |
|---|---|
| `--harness {anthropic-api,codex-agent}` | Which agent loop to run. See [docs/cktlean.md](docs/cktlean.md) |
| `--model`, `--max-turns` | Model and per-problem turn budget |
| `--sim-feedback --feedback-mode compile-only` | Extra repair rounds that see compiler output but no simulation results |
| `--problem-file`, `--filter`, `--limit` | Run a subset |
| `--resume --resume-mode completed` | Skip problems that already have a result |
| `--eval-only` | Score existing `Generated/*.lean` without calling a model |
| `--synth`, `--pnr`, `--drc`, `--lvs` | Run backend stages on passing designs |

A full-budget configuration with the Codex-agent harness and compile-only
feedback:

```bash
uv run python -m cktlean.run --dataset verilogeval \
  --results-dir results/verilogeval --workers 4 \
  --harness codex-agent --model "$MODEL" --codex-effort ultra \
  --codex-bin "$CODEX_BIN" --archon-src "$ARCHON_SRC" \
  --codex-sandbox workspace-write \
  --prompt-profile compact --interface-prompt-policy legacy \
  --max-turns 40 --generation-turn-cap 40 --total-turn-budget 100 \
  --sim-feedback --feedback-mode compile-only \
  --sim-feedback-max-iters 9 --sim-feedback-turns-per-iter 10 \
  --sim-feedback-patience 0 --pre-sim-semantic-repair \
  --cvdp-harness-profile race-safe-v1 --hide-cvdp-harness-from-agent \
  --resume --resume-mode completed
```

### Baselines

The same harness can write SystemVerilog directly instead of Lean:

```bash
uv run python -m cktlean.run_verilog --dataset verilogeval --workers 4
```

`experiments/baseline_verilog.py` is a single-shot baseline with no tools, and
`experiments/baseline_verilog_iterative.py` adds compile and simulation
feedback. [experiments/README.md](experiments/README.md) lists the other
runners.

### Backend evaluation

Pass `--synth`, `--pnr`, `--drc` or `--lvs` to `cktlean.run`, or evaluate a
finished run afterwards with `experiments/main_backend_posthoc.py`.

## Tests

```bash
uv run pytest Tests -q
```

The Python tests make no model calls. Tests that need Icarus Verilog, a built
Lean toolchain or a benchmark dataset are skipped when those are missing. The HDL's own tests
are Lean executables declared in `lakefile.lean`.

## Documentation

- [docs/HDL_Guide.md](docs/HDL_Guide.md): the language, with examples
- [docs/SignalDSL_Syntax.md](docs/SignalDSL_Syntax.md): Signal DSL syntax reference
- [docs/Verification_Framework.md](docs/Verification_Framework.md): writing specifications and proofs
- [docs/Troubleshooting_Synthesis.md](docs/Troubleshooting_Synthesis.md): common synthesis errors
- [docs/cktlean.md](docs/cktlean.md): the agent harness
- [experiments/README.md](experiments/README.md): experiment entry points and datasets

## License and acknowledgments

Apache License 2.0. See [LICENSE](LICENSE).

CKTLean builds on [Sparkle HDL](https://github.com/Verilean/sparkle) by Junji
Hashimoto, released under Apache 2.0. We thank the Sparkle authors for their
contribution. The examples and IP cores under `Examples/` and `IP/` come from
Sparkle.

The benchmarks (VerilogEval, RTLLM, ResBench, CVDP) belong to their respective
authors and are distributed under their own licenses.
