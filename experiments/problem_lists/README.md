# Rebuttal Problem Lists

This directory records public benchmark problem identifiers used by the rebuttal experiments. These files are manifests only: they do not include hidden testbenches, expected traces, generated solutions, run logs, or evaluation results.

## CVDP 72-task subset

`cvdp72_rebuttal_subset.txt` contains the 72 CVDP `prob_id`s used for the CktArchon Lean/Sparkle and Verilog baseline comparison runs. It is a subset of `cvdp168_mainline_intersection.txt`.

## CVDP 168-task mainline set

`cvdp168_mainline_intersection.txt` contains the 168 unique CVDP `prob_id`s used as the mainline CVDP full-set口径 in the rebuttal experiments. This list was checked against the three historical source runs:

- `/home/sgli/work/nl2chip_main_cvdp_source`
- `/home/sgli/work/nl2chip_cvdp_plain_backend_align`
- `/home/sgli/work/nl2chip_baseline_cvdp_source_lab`

Each source run has 168 unique tasks, and their intersection is 168/168.

Example for the 72-task subset:

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

For the 168-task set, replace the `--problem-file` value with:

```bash
--problem-file experiments/problem_lists/cvdp168_mainline_intersection.txt
```
