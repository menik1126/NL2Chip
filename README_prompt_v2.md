# Public Prompt V2

This is a separate, opt-in prompt revision. The v1 sources, prompt receipts,
candidates, and 8/10 diagnostic result remain unchanged.

## Changes

- Use the complete public specification/context instead of references to the
  old inferred interface contract.
- Name the actual Codex Lean compiler CLI, not unavailable function tools.
- Ask for a concise requirement inventory in the existing reasoning:
  `item | requirement | public source | evidence status`.
- Distinguish `explicit`, `inferred`, `ambiguous`, and `unspecified` requirements.
  Quotations and context references must come from the public input.
- Apply the same public interpretation rules to Lean and direct-Verilog,
  including compile repair. There are no task-specific prompt branches.

## Unchanged

No reviewer, parser for the inventory, validation gate, self-test, new model
call, or post-compile SV inspection is introduced. Action budgets, compile-only
feedback filtering, repair triggering, and immediate stopping after a successful
Lean/export check are unchanged. The inventory does not require extra compiler
calls. Compiler/export feedback, Sparkle, and evaluator/adapter code are unchanged.

The evaluation top-module label remains separately disclosed routing metadata;
it is not a source of port or behavior requirements. This prompt was developed
after inspecting benchmark failures, so its targeted regression result is not
an untouched held-out evaluation.

## Staging and Tests

`stage_prompt_v2.py` creates `/root/NL2Chip_public_prompt_v2_20260912` from the
frozen v1 workspace and validates backend/evaluator hashes. It never launches
an experiment. The new policy flag is `--interface-prompt-policy public-spec-v2`;
the default remains legacy. Continue using the original v1 workspace for v1.

Run these offline tests from the staged workspace, with that workspace and its
`agent` directory on `PYTHONPATH`:

```sh
.venv/bin/python -m pytest tests/test_public_prompt.py tests/test_prompt_v2_scope.py tests/test_cktarchon.py -q
```

Scope tests compare the frozen v1 AST against v2 for all non-prompt functions,
Codex execution, and the action-budget watcher. They also compare the final
stop instructions verbatim, ensure legacy prompts are unchanged, and verify
that the public input packet is unchanged. These are offline checks, not
evidence of a new benchmark score.

Verified on X5 on 2026-09-12: 112 offline tests passed. The four changed Python
modules also passed `py_compile`. Staging verified 48 backend/evaluator source
files against v1. No model experiment was launched and no old result was replaced.
