# CktArchon harness

`cktarchon/` runs the generation agent. It owns the agent loop, the tools the
agent may call and the files it may touch; scoring stays in
`agent/evaluator.py`.

## Design

- Benchmark discovery, prompt construction and
  `Evaluator.evaluate(prob_id, run_dir)` are shared by every harness, so
  results are comparable across them.
- `results.jsonl` keeps one row per problem with `compile_pass`, `lint_pass`,
  `sim_status`, `agent_turns_total`, `agent_input_tokens`,
  `agent_output_tokens`, `agent_tool_counts`, `agent_compile_checks` and
  elapsed-time fields.
- The artifact path and the synthesized module name are separate:
  `Generated/<prob_id>.lean` is the file, while the Lean function passed to
  `#synthesizeVerilog` must be the benchmark's target module name.

## Harnesses

Select one with `--harness`.

### `anthropic-api`

A direct Anthropic tool-use loop.

- Tools: `read_file`, `write_file`, `edit_file`, `grep`, `glob`,
  `list_directory`, `bash` and `lean_check`.
- A `PathGuard` limits writes to `Generated/<prob_id>.lean`,
  `Generated/<prob_id>_*.lean` and `cktarchon_work/<prob_id>/**`.
- `lean_check(code=...)` checks a snippet; `lean_check(path=...)` checks a file.
- Events are written as JSONL under `logs/<prob_id>/generate.jsonl`.
- Transient provider failures are retried with the `SPARKLE_API_MAX_RETRIES`,
  `SPARKLE_API_BASE_DELAY` and `SPARKLE_API_MAX_DELAY` backoff settings.

### `codex-agent`

Runs the Codex CLI through Archon's `CodexAgent`, which normalizes
`codex exec --json` output into the same JSONL event format.

- Needs an Archon checkout (`ARCHON_SRC` or `--archon-src`) and the Codex CLI
  (`--codex-bin` or `ARCHON_CODEX_BIN`). It fails loudly if either is missing.
- Codex gets Lean feedback by running
  `.venv/bin/python -m cktarchon.tools lean-check Generated/<prob_id>.lean`.
  Once that succeeds it is told to stop and let the evaluator run simulation.
- Any existing `Generated/<prob_id>.lean` is moved to
  `run_dir/preexisting_generated/` before generation, so output from an earlier
  run cannot be scored as a new result.
- The Codex CLI has no turn limit, so the wrapper counts normalized `text` and
  `tool_call` events and cancels the session when `--max-turns` is exhausted,
  logging `cktarchon_budget_exceeded`.
- If Codex is cancelled after it has written the Lean file, that file is still
  evaluated and the row records the `agent_error`.

## Responses-to-Chat proxy

The Codex CLI speaks the `/v1/responses` API. When the model provider only
offers `/v1/chat/completions`, `cktarchon/responses_chat_proxy.py` bridges the
two:

- converts Responses requests into chat-completions requests;
- converts assistant text and `tool_calls` back into Responses SSE events;
- carries usage tokens through so token accounting still works;
- converts `function_call_output` items into chat `tool` messages.

It starts automatically when no Codex gateway is configured. Pass
`--no-codex-chat-proxy` to disable it, or point Codex at a native gateway with
`--codex-base-url-env` and `--codex-key-env`.

## Commands

Check the installation without calling a model:

```bash
python -m py_compile cktarchon/*.py
python -m pytest Tests/test_cktarchon.py -q
```

Score Lean files that already exist in `Generated/`:

```bash
python -m cktarchon.run --dataset cvdp --problem-file ids.txt \
  --eval-only --no-repl --results-dir results/eval_only
```

Run the Codex-agent harness:

```bash
python -m cktarchon.run --dataset cvdp --harness codex-agent \
  --model "$MODEL" --codex-bin "$CODEX_BIN" --archon-src "$ARCHON_SRC" \
  --max-turns 80 --workers 1 \
  --results-dir results/codex_cvdp --resume --resume-mode completed
```

`--resume --resume-mode completed` skips problems that already have a row, so an
interrupted run can be restarted with the same command.

## Token accounting

Token totals are lower bounds. A session cancelled by the turn budget may end
before the provider reports usage, and those rows keep their turn counts but
record zero tokens.

After a resumed run, `summary.json` covers only the problems that process
executed. For totals over a whole benchmark, de-duplicate `results.jsonl` rows by
`prob_id` across the run directories.
