# P3 Stage 4 CVDP-12 Experiment Report

Date: 2026-08-17 (Asia/Shanghai)

## Scope

This report closes the P3 toolchain experiment on the 12 CVDP tasks with
public parameter sweeps. It records the strict agent result, parameter-pipeline
coverage, turn/token/wall-clock cost, and remaining failure attribution.

The token-accounted run uses commit `9b0a724` on branch
`p3-symbolic-width-toolchain-20260814`. The relevant preceding commits are:

- `2e8c0cc`: P3 symbolic-width stages 0-3.
- `07507b9`: indexed/Hamming/memory/signed APIs in the agent skill.
- `24b9245`: classify DUT progress timeouts as functional simulation failures.
- `19d7a88`: preserve timeout and `done`/`valid` hints in repair prompts.
- `0e8cf16`: require a fresh candidate to write a complete source file first.
- `9b0a724`: count uncached, cache-creation, and cache-read input tokens.

## Configuration

The run used `experiments/run_p3_cvdp12.sh` with:

| Setting | Value |
|---|---:|
| Model | `gpt-5.6-sol` |
| Workers | 4 |
| Initial session limit | 10 turns |
| Repair attempts | at most 9 |
| Repair session limit | 10 turns |
| Total budget | 100 turns/task |
| Candidate limit | 3 |
| Stagnation patience | 2 |
| Prompt profile | `cvdp-skill-fewshot` |
| Parameter mode | native parameter sweep |
| Guided self-test | disabled |
| Formal / CppSim / PPA | disabled |

Reproduction command, with credentials intentionally omitted:

```bash
KEY_ENV=<credential-env> \
MODEL=gpt-5.6-sol \
WORKERS=4 \
RESULTS_DIR=/home/sgli/work/results_p3_cvdp12_gpt56sol_stage4_tokenfixed_20260817 \
bash experiments/run_p3_cvdp12.sh
```

## Verification Gates

Before the benchmark run, the following command passed:

```bash
PATH=/home/sgli/work/NL2Chip/.venv/bin:/home/sgli/.elan/toolchains/leanprover--lean4---v4.28.0-rc1/bin:$PATH \
P3_PYTHON=/home/sgli/work/NL2Chip/.venv/bin/python \
bash scripts/verify_p3_toolchain.sh
```

Observed gates:

- `148 passed`
- `SIGNED_HELPER_SV_BEHAVIOR_PASS`
- `P3_SYMBOLIC_PARAMETER_BEHAVIOR_PASS`
- `P3_TOOLCHAIN_REGRESSION_PASS`

A live gateway probe returned nonzero cache usage and was normalized as
`5814 uncached + 0 cache-create + 4608 cache-read = 10422 total`.

For the full run, 58 session logs were independently parsed. They sum to the
same 513 turns and token totals as the 12 task records and `summary.json`.
There were no per-event, per-session, or per-task token invariant failures.

## Headline Result

| Metric | Token-accounted run |
|---|---:|
| Tasks | 12/12 |
| Lean/Sparkle compile pass | 11/12 |
| RTL simulation pass | 7/12 |
| RTL simulation fail | 4/12 |
| Lean not-run | 1/12 |
| API/agent errors | 0 |
| First-attempt simulation pass | 6 |
| Repaired simulation pass | 1 |
| Total turns | 513 |
| Average turns/task | 42.75 |
| Wall-clock, 4 workers | 33m47s |

The summary's `sim_error=1` is the Lean `not_run` task. It is not an API or
simulator infrastructure error.

## Run-to-Run Comparison

The metrics patch does not alter prompts or generation behavior, but the model
is stochastic. The strict rerun therefore changed the score as well as cost.
Do not combine the old score with the new token totals as though they came from
one run.

| Metric | Pre-fix reference (`0e8cf16`) | Token-accounted rerun (`9b0a724`) |
|---|---:|---:|
| Compile pass | 11/12 | 11/12 |
| Sim pass | 9/12 | 7/12 |
| Sim fail | 2/12 | 4/12 |
| Not run | 1/12 | 1/12 |
| First / repaired pass | 7 / 2 | 6 / 1 |
| Turns | 321 | 513 |
| Output tokens | 125,160 | 267,035 |
| Input tokens | invalid: cache fields absent | 7,460,687 |
| Wall-clock, 4 workers | 23m12s | 33m47s |

Two stochastic regressions explain the pass-count change:
`axil_precision_counter_0001` changed from pass to sim-fail, and
`gf_multiplier_0021` changed from pass to Lean not-run. All other pass/fail
statuses were stable, while `restoring_division_0001` improved from not-run to
a compilable design that reached simulation but timed out functionally.

## Per-Task Result

| Task | Compile | Sim | Turns | Feedback iters | Candidates | Input tokens | Output tokens |
|---|---:|---|---:|---:|---:|---:|---:|
| `filo_0005` | yes | pass | 5 | 0 | 1 | 53,156 | 2,590 |
| `car_parking_management_0001` | yes | pass | 10 | 0 | 1 | 97,276 | 3,835 |
| `hamming_code_tx_and_rx_0009` | yes | pass | 7 | 0 | 1 | 77,047 | 982 |
| `hamming_code_tx_and_rx_0011` | yes | pass | 6 | 0 | 1 | 65,990 | 1,572 |
| `nbit_swizzling_0001` | yes | pass | 7 | 0 | 1 | 66,845 | 1,217 |
| `word_reducer_0008` | yes | pass | 6 | 0 | 1 | 62,197 | 592 |
| `digital_dice_roller_0004` | yes | pass | 20 | 1 | 1 | 277,116 | 13,430 |
| `axil_precision_counter_0001` | yes | fail | 87 | 9 | 3 | 1,382,397 | 33,791 |
| `square_root_0003` | yes | fail | 83 | 9 | 3 | 1,253,513 | 59,008 |
| `restoring_division_0001` | yes | fail | 100 | 9 | 2 | 1,425,631 | 46,191 |
| `sync_lifo_0001` | yes | fail | 82 | 9 | 2 | 1,202,175 | 53,502 |
| `gf_multiplier_0021` | no | not run | 100 | 9 | 3 | 1,497,344 | 50,325 |

## Token Accounting

`agent_input_tokens` is now the sum of all three input categories, while each
category remains available separately for billing analysis.

| Input category | Tokens | Share of input |
|---|---:|---:|
| Uncached | 77,088 | 1.03% |
| Cache creation | 2,101,711 | 28.17% |
| Cache read | 5,281,888 | 70.80% |
| **Total input** | **7,460,687** | **100.00%** |
| Output | 267,035 | - |
| Input + output | 7,727,722 | - |

Average input plus output is 643,976.83 tokens/task. Raw token categories
should remain separate when estimating monetary cost because providers may
price cache creation and cache reads differently.

Successful and unsuccessful tasks have sharply different search cost:

| Group | Tasks | Avg turns | Avg input | Avg output | Avg input + output |
|---|---:|---:|---:|---:|---:|
| Sim pass | 7 | 8.71 | 99,946.71 | 3,459.71 | 103,406.43 |
| Not sim pass | 5 | 90.40 | 1,352,212.00 | 48,563.40 | 1,400,775.40 |

The five unsuccessful tasks consumed 88.11% of turns, 90.62% of input tokens,
and 90.93% of output tokens. This is the main remaining cost problem.

## Remaining Failure Attribution

| Task | Evidence | Attribution |
|---|---|---|
| `axil_precision_counter_0001` | All 9 parameter cases pass contract/elaboration. At 1560 ns, `axi_rdata` is 10 after reset but expected 0; IRQ behavior also remains wrong. | Model-generated AXI/state/reset semantics. Not parameter lowering. |
| `square_root_0003` | All 4 parameter cases elaborate. At 40 ns, root remains 0 where 1, 2, and 14 are expected. | Model-generated algorithm/FSM/valid timing. |
| `restoring_division_0001` | All 5 parameter cases elaborate. Cocotb runs for 180 s without DUT completion. | Model-generated termination or `done` handshake semantics. The timeout is a functional progress failure. |
| `sync_lifo_0001` | All 5 parameter cases elaborate. `full` remains 0 at tested capacities 4, 8, 16, and 64. | Model-generated count/full/control semantics, not parameterized memory lowering. |
| `gf_multiplier_0021` | Final source fails synthesis with `Cannot infer hardware type from _uniq.7498` around the inlined helper chain. The same task passed on the preceding run. | Mixed model/API-ergonomics failure: the model selected a poorly inferable helper/cast structure and the compiler diagnostic is opaque. This is not evidence that native parameters generally fail. |

Thus, four of the five remaining failures are behaviorally wrong but fully
parameterized and elaborated designs. One is a generation/compiler-usability
failure. No failure is attributable to API quota, simulator availability,
wrapper fallback, or a generic Lean-to-Verilog semantic mistranslation.

## Conclusions and Next Work

1. P3 native parameter preservation is operational on this set: 11 tasks
   compile, and every compilable final design passes its complete public
   parameter contract/elaboration sweep.
2. Indexed/Hamming primitives are effective: both Hamming tasks pass on the
   first attempt, as do the generic swizzle and reducer tasks.
3. Search efficiency is now the dominant limitation. A two-strike rewrite is
   working mechanically, but difficult failures still consume nearly the full
   100-turn budget without semantic convergence.
4. The next engineering priorities are actionable `_uniq` diagnostics or
   rewrite hints, protocol/FSM templates for AXI/FIFO/division/square-root, and
   earlier termination/restart based on assertion-level semantic progress.
5. For paper reporting, use the complete token-accounted run for all cost
   numbers and report stochastic accuracy separately, ideally over repeated
   runs rather than selecting the higher single-run score.

## Artifacts

- Problem list: `experiments/cvdp_parameterized_12.txt`
- Launch script: `experiments/run_p3_cvdp12.sh`
- Token-accounted run:
  `/home/sgli/work/results_p3_cvdp12_gpt56sol_stage4_tokenfixed_20260817/cktarchon_run_20260817_222141`
- Pre-fix reference run:
  `/home/sgli/work/results_p3_cvdp12_gpt56sol_stage4_final_20260817/cktarchon_run_20260817_214250`
