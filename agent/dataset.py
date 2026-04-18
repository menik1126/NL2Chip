"""
Dataset abstraction for Sparkle evaluation pipeline.

Supports:
  - verilogeval: VerilogEval benchmark (flat directory, Prob001_xxx_prompt.txt / _ref.sv / _test.sv)
  - rtllm: RTLLM benchmark (nested directories, design_description.txt / verified_*.v / testbench.v)

Usage:
    from dataset import Dataset

    ds = Dataset("rtllm", project_root=Path("/home/user/sparkle"))
    problems = ds.discover_problems(limit=10, filter_re="adder")
    prompt, ref_code, design_name = ds.load_problem("adder_8bit")
    sim_cfg = ds.get_sim_config()
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SimConfig:
    """How to run simulation for this dataset."""
    pass_pattern: str          # regex to detect pass in sim output
    needs_ref_in_compile: bool # whether ref.sv is needed in iverilog command
    needs_wrapper: bool        # whether a TopModule/design-name wrapper is needed
    wrapper_module_name: str   # "TopModule" for verilogeval, design_name for rtllm


@dataclass
class ProblemInfo:
    """All info needed to run a problem."""
    prob_id: str
    design_name: str       # module name expected by testbench
    prompt_text: str       # NL description
    ref_code: str          # reference Verilog implementation
    testbench_path: Path   # path to testbench file
    ref_path: Path | None  # path to ref.sv (needed for verilogeval sim compile)


class Dataset:
    """Unified dataset interface for VerilogEval and RTLLM."""

    def __init__(self, name: str, project_root: Path):
        self.name = name.lower()
        self.project_root = project_root.resolve()

        if self.name == "verilogeval":
            self.dataset_dir = self.project_root / "verilog-eval" / "dataset_spec-to-rtl"
        elif self.name == "rtllm":
            self.dataset_dir = self.project_root / "RTLLM"
        else:
            raise ValueError(f"Unknown dataset: {name}. Use 'verilogeval' or 'rtllm'.")

        # Cache RTLLM problem paths: design_name -> directory path
        self._rtllm_index: dict[str, Path] | None = None

    # ── Discovery ────────────────────────────────────────────────

    def discover_problems(
        self, limit: int | None = None, filter_re: str | None = None,
    ) -> list[str]:
        if self.name == "verilogeval":
            return self._discover_verilogeval(limit, filter_re)
        else:
            return self._discover_rtllm(limit, filter_re)

    def _discover_verilogeval(self, limit, filter_re) -> list[str]:
        pattern = re.compile(filter_re) if filter_re else None
        prob_ids = set()
        for f in sorted(self.dataset_dir.iterdir()):
            m = re.match(r"(Prob\d+_\w+)_prompt\.txt$", f.name)
            if not m:
                continue
            pid = m.group(1)
            if pattern and not pattern.search(pid):
                continue
            prob_ids.add(pid)
        result = sorted(prob_ids)
        if limit:
            result = result[:limit]
        return result

    def _discover_rtllm(self, limit, filter_re) -> list[str]:
        self._build_rtllm_index()
        pattern = re.compile(filter_re) if filter_re else None
        prob_ids = []
        for name in sorted(self._rtllm_index.keys()):
            if pattern and not pattern.search(name):
                continue
            prob_ids.append(name)
        if limit:
            prob_ids = prob_ids[:limit]
        return prob_ids

    def _build_rtllm_index(self):
        if self._rtllm_index is not None:
            return
        self._rtllm_index = {}
        for desc_file in sorted(self.dataset_dir.rglob("design_description.txt")):
            design_dir = desc_file.parent
            design_name = design_dir.name
            # Skip hidden/output directories
            if design_name.startswith("_") or design_name.startswith("."):
                continue
            self._rtllm_index[design_name] = design_dir

    # ── Loading ──────────────────────────────────────────────────

    def load_problem(self, prob_id: str) -> ProblemInfo:
        if self.name == "verilogeval":
            return self._load_verilogeval(prob_id)
        else:
            return self._load_rtllm(prob_id)

    def _load_verilogeval(self, prob_id: str) -> ProblemInfo:
        prompt_file = self.dataset_dir / f"{prob_id}_prompt.txt"
        ref_file = self.dataset_dir / f"{prob_id}_ref.sv"
        test_file = self.dataset_dir / f"{prob_id}_test.sv"

        prompt = prompt_file.read_text() if prompt_file.exists() else "(no description)"
        ref_code = ref_file.read_text() if ref_file.exists() else "(no reference)"

        return ProblemInfo(
            prob_id=prob_id,
            design_name="TopModule",
            prompt_text=prompt,
            ref_code=ref_code,
            testbench_path=test_file,
            ref_path=ref_file if ref_file.exists() else None,
        )

    def _load_rtllm(self, prob_id: str) -> ProblemInfo:
        self._build_rtllm_index()
        if prob_id not in self._rtllm_index:
            raise FileNotFoundError(f"RTLLM design not found: {prob_id}")

        design_dir = self._rtllm_index[prob_id]
        desc_file = design_dir / "design_description.txt"
        tb_file = design_dir / "testbench.v"

        prompt = desc_file.read_text() if desc_file.exists() else "(no description)"

        # Find verified_*.v reference
        ref_files = list(design_dir.glob("verified_*.v"))
        ref_code = ref_files[0].read_text() if ref_files else "(no reference)"

        return ProblemInfo(
            prob_id=prob_id,
            design_name=prob_id,  # RTLLM testbench instantiates by design name
            prompt_text=prompt,
            ref_code=ref_code,
            testbench_path=tb_file,
            ref_path=ref_files[0] if ref_files else None,
        )

    # ── Sim config ───────────────────────────────────────────────

    def get_sim_config(self, design_name: str = "") -> SimConfig:
        if self.name == "verilogeval":
            return SimConfig(
                pass_pattern=r"Mismatches:\s*0\s",
                needs_ref_in_compile=True,
                needs_wrapper=True,
                wrapper_module_name="TopModule",
            )
        else:
            return SimConfig(
                pass_pattern=r"Your Design Passed",
                needs_ref_in_compile=False,
                needs_wrapper=True,
                wrapper_module_name=design_name,
            )
