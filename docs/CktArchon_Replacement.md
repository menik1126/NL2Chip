# CktArchon Replacement Plan

This note records the NL2Chip/CktFormalizer path for replacing the legacy
`agent/search.py` generation loop with an Archon-style harness while preserving
the existing evaluator and result schema.

## Goals

- Keep NL2Chip's benchmark discovery, prompt construction, and
  `Evaluator.evaluate(prob_id, run_dir)` as the authoritative scoring backend.
- Replace the ad hoc generation loop with a harness boundary that can run either:
  - `anthropic-api`: a direct Anthropic tool-use loop with Archon-like JSONL.
  - `codex-agent`: official Archon's `CodexAgent` backend (`codex exec --json`)
    when the host has the Codex CLI installed.
- Preserve old `results.jsonl` fields for downstream analysis:
  `compile_pass`, `lint_pass`, `sim_status`, `agent_turns_total`,
  `agent_input_tokens`, `agent_output_tokens`, `agent_tool_counts`,
  `agent_compile_checks`, and elapsed-time fields.
- Treat the generated file name and synthesized module name separately:
  `Generated/<prob_id>.lean` is the artifact path, while the Lean
  function passed to `#synthesizeVerilog` must be the benchmark
  `design_name`/target module.

## Implemented Harness Behavior

`anthropic-api` is the direct Claude API path on H20:

- exposes controlled tools: `read_file`, `write_file`, `edit_file`, `grep`,
  `glob`, `list_directory`, `bash`, and `lean_check`;
- uses a `PathGuard` so writes are limited to
  `Generated/<prob_id>.lean`, `Generated/<prob_id>_*.lean`, and
  `cktarchon_work/<prob_id>/**`;
- supports `lean_check(code=...)` for the existing NL2Chip prompt contract and
  `lean_check(path=...)` for file-based checks;
- writes Archon-style JSONL events under `logs/<prob_id>/generate.jsonl`;
- retries transient Anthropic/provider failures using the same
  `SPARKLE_API_*` backoff knobs as the legacy `CodingAgent`.

`codex-agent` is wired but host-dependent:

- imports official Archon from `/home/sgli/work/archon-official/src`;
- runs official `archon.agents.codex.CodexAgent`, which normalizes
  `codex exec --json` into Archon JSONL;
- fails loudly if `codex` is not installed or exposed via `ARCHON_CODEX_BIN`;
- instructs Codex to run `.venv/bin/python -m cktarchon.tools lean-check
  Generated/<prob_id>.lean` for Lean feedback;
- automatically starts a local Responses-to-Chat proxy when no native Codex
  Responses gateway is configured. This lets Codex CLI speak `/v1/responses`
  while the H20 provider only exposes `/v1/chat/completions`.
- backs up and removes any pre-existing `Generated/<prob_id>.lean` before
  agent generation, under `run_dir/preexisting_generated/`, so stale outputs
  from previous experiments cannot be evaluated as current Codex-agent results.
- uses a compact CVDP/code-generation prompt instead of the full legacy
  `agent/search.py` skill. The compact prompt keeps the Sparkle template,
  stable operators, Lean-check workflow, and CVDP-specific hazard rules, while
  dropping architecture exploration/proof sections that inflated token cost.
- places Codex-specific stop rules after the NL2Chip problem prompt: once
  `.venv/bin/python -m cktarchon.tools lean-check Generated/<prob_id>.lean`
  succeeds, Codex should stop and let the outer evaluator run lint/simulation.
- enforces the CktArchon `--max-turns` budget outside Codex by monitoring
  normalized Archon JSONL events (`text` + `tool_call`). Codex CLI has no
  native max-turn flag, so the wrapper sets `cancel_event` when the budget is
  exhausted and logs `cktarchon_budget_exceeded`.
- if Codex is interrupted after writing `Generated/<prob_id>.lean`, the runner
  still evaluates that generated file. This preserves compile/lint/sim evidence
  for budget-exceeded attempts while recording the `agent_error` field.

H20 now has Codex CLI available at:

- `/home/sgli/work/codex-runtime-0.136/bin/codex` (`codex-cli 0.136.0`)
- `/home/sgli/work/codex-runtime/bin/codex` (`codex-cli 0.146.0`)

The current Codex-agent CVDP72 run uses the 0.136 launcher plus
`claude-sonnet-4.5` through the local proxy.

## Responses-to-Chat Proxy

`cktarchon/responses_chat_proxy.py` implements the minimal bridge Codex needs:

- converts Codex `/v1/responses` requests into OpenAI-compatible
  `/v1/chat/completions` requests;
- converts chat assistant text and `tool_calls` back into Responses SSE events
  (`response.created`, `response.output_item.done`, `response.completed`);
- preserves usage tokens from chat responses for Archon log normalization;
- converts `function_call_output` items into chat `tool` messages;
- inherits proxy environment variables from same-host processes when the ssh
  login environment does not include them, without logging their values.

Important provider note: `https://yunwu.ai/v1/chat/completions` works for the
current key/model, while `https://yunwu.ai/v1/responses` returns a provider
error. The shim is therefore required for Codex CLI on H20 unless Yunwu adds a
Responses-compatible endpoint.

## Run Commands

Smoke test without API calls:

```bash
cd /home/sgli/work/NL2Chip
.venv/bin/python -m py_compile cktarchon/*.py
.venv/bin/python -m pytest tests/test_cktarchon.py -q
.venv/bin/python -m cktarchon.run \
  --dataset cvdp \
  --problem-file /tmp/cktarchon_smoke_one.txt \
  --eval-only --no-repl \
  --results-dir /home/sgli/work/nl2chip_cktarchon_smoke_20260804
```

Claude/Sonnet 72-task run on H20:

```bash
RUN_BASE=/home/sgli/work/nl2chip_cktarchon_cvdp72_sonnet45_20260804
nohup /home/sgli/work/NL2Chip/.venv/bin/python -u -m cktarchon.run \
  --dataset cvdp \
  --problem-file /home/sgli/work/nl2chip_sv2lean_cvdp72_h20_20260802_0623/cvdp_baseline_simpass_72.txt \
  --model claude-sonnet-4.5 \
  --max-turns 80 \
  --workers 1 \
  --results-dir "$RUN_BASE" \
  --resume --resume-mode completed \
  > "$RUN_BASE/run.log" 2>&1 &
echo $! > "$RUN_BASE/run.pid"
```

Official Archon Codex-agent run on H20:

```bash
RUN_BASE=/home/sgli/work/nl2chip_cktarchon_codex_cvdp72_sonnet45_20260804_v6
mkdir -p "$RUN_BASE"
cd /home/sgli/work/NL2Chip
export CODEX_HOME=/home/sgli/work/codex-runtime-0.136/home
nohup .venv/bin/python -u -m cktarchon.run \
  --dataset cvdp \
  --problem-file /home/sgli/work/nl2chip_sv2lean_cvdp72_h20_20260802_0623/cvdp_baseline_simpass_72.txt \
  --harness codex-agent \
  --model claude-sonnet-4.5 \
  --max-turns 80 \
  --workers 1 \
  --api-timeout 180 \
  --results-dir "$RUN_BASE" \
  --codex-bin /home/sgli/work/codex-runtime-0.136/bin/codex \
  --codex-idle-timeout 900 \
  --codex-max-attempts 3 \
  --resume --resume-mode completed \
  > "$RUN_BASE/run.log" 2>&1 &
echo $! > "$RUN_BASE/run.pid"
```

Status check:

```bash
RUN_BASE=/home/sgli/work/nl2chip_cktarchon_codex_cvdp72_sonnet45_20260804_v6
ps -p "$(cat "$RUN_BASE/run.pid")" -o pid,stat,etime,pcpu,pmem,cmd
RUN=$(find "$RUN_BASE" -maxdepth 1 -type d -name 'cktarchon_run_*' | sort | tail -1)
wc -l "$RUN/results.jsonl" 2>/dev/null || true
tail -20 "$RUN_BASE/run.log"
```

## Final CVDP72 Codex-Agent Run

The official Archon `CodexAgent` CVDP72 run completed on H20 on 2026-08-04.

- Run base: `/home/sgli/work/nl2chip_cktarchon_codex_cvdp72_sonnet45_20260804_v6`
- Run dir: `/home/sgli/work/nl2chip_cktarchon_codex_cvdp72_sonnet45_20260804_v6/cktarchon_run_20260804_063822`
- Aggregate summary: `aggregate_summary.json` in the run dir
- Harness: `codex-agent` using official Archon `CodexAgent`
- Model alias: `claude-sonnet-4.5` -> `claude-sonnet-4-5-20250929`
- Codex launcher: `/home/sgli/work/codex-runtime-0.136/bin/codex`
- Problem file: `/home/sgli/work/nl2chip_sv2lean_cvdp72_h20_20260802_0623/cvdp_baseline_simpass_72.txt`

Final de-duplicated aggregate over all 72 selected CVDP tasks:

- Completed rows: 72/72
- Compile pass: 60/72 (83.3%)
- Lint pass: 60/72 (83.3%)
- Simulation status: `sim_pass` 23, `sim_fail` 34, `sim_error` 3, `not_run` 12
- Sim pass rate: 23/72 (31.9%) overall, or 23/60 (38.3%) among compile-passing tasks
- Agent errors: 8, all from the CktArchon max-turn budget wrapper
- Budget-exceeded rows: 8. One of these (`cvdp_copilot_kogge_stone_adder_0007`) still produced a file that compile/lint/sim passed after evaluation.
- Non-budget compile-failure `not_run` rows: 5
- Token lower bound: 21,648,142 input / 344,233 output tokens
- Average token lower bound per task: 300,668.64 input / 4,781.01 output tokens
- Total normalized turns: 2,505; average turns: 34.79
- Aggregate task elapsed time: 16,899.204 seconds (4.69 hours); timestamp span: 2026-08-04T04:50:11 to 2026-08-04T09:34:40 (4.74 hours)

Token accounting note: token totals are lower bounds because Codex/provider usage is
missing for budget-cancelled sessions that terminate before a final usage-bearing
`session_end`. Those rows keep recovered turn counts from partial JSONL logs, but
input/output tokens remain 0 unless the provider emitted usage before cancellation.

The raw runner `summary.json` for the final resumed process is not the 72-task
aggregate: it counts only tasks actually executed by that process and records
previously completed tasks under `skipped`. Use `aggregate_summary.json` or a
prob-id de-duplicated scan across `*/results.jsonl` for paper numbers.

## Follow-Up Hardening

- Add a post-run file-change audit for native Codex/Claude Code agents if we
  need enforcement stronger than prompt discipline for non-API harnesses. The
  current Codex-agent path already backs up/removes stale target files and
  instructs Codex to edit only the generated artifact plus per-task scratch.

