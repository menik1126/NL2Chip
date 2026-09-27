# ICLR rebuttal launchers

These scripts are the ones that actually ran on H20. Paths inside them point at
`/home/sgli/work/...` trees; they do not overwrite frozen PPA / equiv / Direct
result directories.

| Script | What it is |
|---|---|
| `../run_archon_verilog.sh` | **Archon Verilog baseline** — `cktarchon.run_verilog` |
| `../05_baseline_verilog_direct.sh` | One-shot LLM → Verilog (no agent tools) |
| `tmp_launch_direct_sv_compilefb.py` | Direct SV + compile-only feedback (no Archon) |
| `tmp_launch_direct_sv_compilefb_pd.py` | Post-hoc PD for that Direct SV tree |
| `tmp_launch_direct_sparkle.py` | Direct Sparkle (no Archon) |
| `tmp_launch_direct_sparkle_compilefb.py` | Direct Sparkle + compile feedback |
| `tmp_launch_direct_sparkle_pd.py` | Post-hoc PD for Direct Sparkle |
| `tmp_contract_validate.py` | Extract / normalize Lean contracts (`«in»`, keep helpers) |
| `tmp_contract_checkers.py` | Syntax ∧ soundness ∧ completeness (I/O-divergent mutants) |
| `tmp_launch_contract_gen_ve12.py` | NL → observational Lean (Toklens chat) |
| `tmp_launch_contract_regen_ve12.py` | Repair loop with unkilled-mutant feedback |
| `tmp_launch_contract_check_ve12.py` | Score frozen VE-12 contracts |
| `tmp_toklens_llm.py` | OpenAI-compatible chat client (keys from env file, not git) |
| `tmp_launch_ppa_opt_ve63.py` / `tmp_launch_ppa_arch_only_ve63.py` | PPA loops |
| `tmp_launch_wns_timing_ve12*.py` | Timing-guided WNS |

Contract LLM credentials: copy `env.example` to a chmod-600 file and set
`TOKLENS_ENV` / `TOKLENS_API_KEY`. Do not commit the filled file.
