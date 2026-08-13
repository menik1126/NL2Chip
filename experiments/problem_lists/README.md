# Rebuttal Problem Lists

This directory records public benchmark problem identifiers used by the rebuttal experiments. These files are manifests only: they do not include hidden testbenches, expected traces, generated solutions, run logs, or evaluation results.

## CVDP 72-task subset

`cvdp72_rebuttal_subset.txt` contains the 72 CVDP `prob_id`s used for the CktArchon Lean/Sparkle and Verilog baseline comparison runs.

Example:

```bash
python -m cktarchon.run \
  --dataset cvdp \
  --problem-file experiments/problem_lists/cvdp72_rebuttal_subset.txt \
  --model claude-sonnet-4.5 \
  --prompt-profile cvdp-skill-fewshot \
  --max-turns 10 \
  --sim-feedback \
  --sim-feedback-max-iters 10 \
  --sim-feedback-turns-per-iter 10 \
  --sim-feedback-turn-budget 90 \
  --guided-search \
  --disable-guided-self-test \
  --candidate-search-max 3 \
  --candidate-stagnation-patience 2 \
  --workers 16 \
  --results-dir /path/to/results
```
