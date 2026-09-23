"""
Dataset abstraction for Sparkle evaluation pipeline.

Supports:
  - verilogeval: VerilogEval benchmark (flat directory, Prob001_xxx_prompt.txt / _ref.sv / _test.sv)
  - rtllm: RTLLM benchmark (nested directories, design_description.txt / verified_*.v / testbench.v)
  - cvdp: CVDP non-agentic code-generation JSONL with cocotb harness files
  - resbench: ResBench problems.json (56 synthetic/resource-oriented tasks)
  - realbench: RealBench module-level tasks (decrypted .md specs + reference .v)

Usage:
    from dataset import Dataset

    ds = Dataset("rtllm", project_root=Path("/home/user/sparkle"))
    problems = ds.discover_problems(limit=10, filter_re="adder")
    prompt, ref_code, design_name = ds.load_problem("adder_8bit")
    sim_cfg = ds.get_sim_config()
"""
from __future__ import annotations

import re
import json
import os
from dataclasses import dataclass, field
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
    metadata: dict = field(default_factory=dict)


class Dataset:
    """Unified dataset interface for VerilogEval, RTLLM, CVDP, and RealBench."""

    def __init__(self, name: str, project_root: Path):
        self.name = name.lower()
        self.project_root = project_root.resolve()

        if self.name == "verilogeval":
            self.dataset_dir = self.project_root / "verilog-eval" / "dataset_spec-to-rtl"
        elif self.name == "rtllm":
            self.dataset_dir = self.project_root / "RTLLM"
        elif self.name == "cvdp":
            self.dataset_dir = self._resolve_cvdp_file()
        elif self.name == "realbench":
            self.dataset_dir = self._resolve_realbench_root()
        elif self.name == "resbench":
            self.dataset_dir = self._resolve_resbench_root()
        else:
            raise ValueError(
                f"Unknown dataset: {name}. Use 'verilogeval', 'rtllm', 'resbench', 'cvdp', or 'realbench'."
            )

        # Cache RTLLM problem paths: design_name -> directory path
        self._rtllm_index: dict[str, Path] | None = None
        self._cvdp_index: dict[str, dict] | None = None
        self._realbench_index: dict[str, Path] | None = None
        self._resbench_index: dict[str, tuple[str, dict]] | None = None

    # ── Dataset path resolution ──────────────────────────────────

    def _first_existing(self, candidates: list[Path]) -> Path:
        for path in candidates:
            if path.exists():
                return path
        return candidates[0]

    def _resolve_cvdp_file(self) -> Path:
        env_path = os.environ.get("CVDP_DATASET_FILE")
        candidates = []
        if env_path:
            candidates.append(Path(env_path).expanduser())
        candidates.extend([
            self.project_root / "cvdp-benchmark-dataset" / "cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl",
            self.project_root / "benchmarks" / "cvdp-benchmark-dataset" / "cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl",
            self.project_root.parent / "benchmarks" / "cvdp-benchmark-dataset" / "cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl",
            Path.home() / "work" / "benchmarks" / "cvdp-benchmark-dataset" / "cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl",
        ])
        return self._first_existing(candidates).resolve()

    def _resolve_realbench_root(self) -> Path:
        env_path = os.environ.get("REALBENCH_ROOT")
        candidates = []
        if env_path:
            candidates.append(Path(env_path).expanduser())
        candidates.extend([
            self.project_root / "RealBench",
            self.project_root / "benchmarks" / "RealBench",
            self.project_root.parent / "benchmarks" / "RealBench",
            Path.home() / "work" / "benchmarks" / "RealBench",
        ])
        return self._first_existing(candidates).resolve()

    def _resolve_resbench_root(self) -> Path:
        env_path = os.environ.get("RESBENCH_ROOT")
        candidates = []
        if env_path:
            candidates.append(Path(env_path).expanduser())
        candidates.extend([
            self.project_root / "ResBench",
            self.project_root / "benchmarks" / "ResBench",
            self.project_root.parent / "benchmarks" / "ResBench",
            Path.home() / "work" / "benchmarks" / "ResBench",
        ])
        return self._first_existing(candidates).resolve()

    # ── Discovery ────────────────────────────────────────────────

    def discover_problems(
        self, limit: int | None = None, filter_re: str | None = None,
    ) -> list[str]:
        if self.name == "verilogeval":
            return self._discover_verilogeval(limit, filter_re)
        if self.name == "rtllm":
            return self._discover_rtllm(limit, filter_re)
        if self.name == "cvdp":
            return self._discover_cvdp(limit, filter_re)
        if self.name == "resbench":
            return self._discover_resbench(limit, filter_re)
        return self._discover_realbench(limit, filter_re)

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
        index = {}
        for desc_file in sorted(self.dataset_dir.rglob("design_description.txt")):
            design_dir = desc_file.parent
            design_name = design_dir.name
            # Skip hidden/output directories
            if design_name.startswith("_") or design_name.startswith("."):
                continue
            index[design_name] = design_dir
        self._rtllm_index = index

    def _build_cvdp_index(self):
        if self._cvdp_index is not None:
            return
        if not self.dataset_dir.exists():
            raise FileNotFoundError(f"CVDP dataset JSONL not found: {self.dataset_dir}")
        index = {}
        for line in self.dataset_dir.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            index[row["id"]] = row
        self._cvdp_index = index

    @staticmethod
    def _parse_env_value(env_text: str, key: str) -> str:
        m = re.search(rf"^{re.escape(key)}\s*=\s*(.*?)\s*$", env_text, re.MULTILINE)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _cvdp_design_name(row: dict) -> str:
        env_text = row.get("harness", {}).get("files", {}).get("src/.env", "")
        return Dataset._parse_env_value(env_text, "TOPLEVEL") or row["id"]

    @staticmethod
    def _cvdp_verilog_sources(row: dict) -> list[str]:
        env_text = row.get("harness", {}).get("files", {}).get("src/.env", "")
        raw = Dataset._parse_env_value(env_text, "VERILOG_SOURCES")
        return [s.strip() for s in raw.split() if s.strip()]

    def _discover_cvdp(self, limit, filter_re) -> list[str]:
        self._build_cvdp_index()
        pattern = re.compile(filter_re) if filter_re else None
        prob_ids = []
        for pid, row in sorted(self._cvdp_index.items()):
            categories = " ".join(row.get("categories", []))
            haystack = f"{pid} {categories} {self._cvdp_design_name(row)}"
            if pattern and not pattern.search(haystack):
                continue
            prob_ids.append(pid)
        if limit:
            prob_ids = prob_ids[:limit]
        return prob_ids

    def _build_realbench_index(self):
        if self._realbench_index is not None:
            return
        if not self.dataset_dir.exists():
            raise FileNotFoundError(f"RealBench root not found: {self.dataset_dir}")
        index = {}
        for group in ("aes", "sdc", "e203_hbirdv2"):
            group_dir = self.dataset_dir / group
            if not group_dir.exists():
                continue
            for design_dir in sorted(p for p in group_dir.iterdir() if p.is_dir()):
                md_path = design_dir / f"{design_dir.name}.md"
                v_path = design_dir / f"{design_dir.name}.v"
                if md_path.exists() and v_path.exists():
                    pid = f"realbench_{group}_{design_dir.name}"
                    index[pid] = design_dir
        self._realbench_index = index

    def _discover_realbench(self, limit, filter_re) -> list[str]:
        self._build_realbench_index()
        pattern = re.compile(filter_re) if filter_re else None
        prob_ids = []
        for pid, design_dir in sorted(self._realbench_index.items()):
            haystack = f"{pid} {design_dir.parent.name} {design_dir.name}"
            if pattern and not pattern.search(haystack):
                continue
            prob_ids.append(pid)
        if limit:
            prob_ids = prob_ids[:limit]
        return prob_ids

    def _build_resbench_index(self):
        if self._resbench_index is not None:
            return
        problems_path = self.dataset_dir / "problems.json"
        if not problems_path.exists():
            raise FileNotFoundError(f"ResBench problems.json not found: {problems_path}")
        data = json.loads(problems_path.read_text())
        index = {}
        for category, rows in data.items():
            for row in rows:
                module = row["module"]
                pid = f"resbench_{module}"
                index[pid] = (category, row)
        self._resbench_index = index

    def _discover_resbench(self, limit, filter_re) -> list[str]:
        self._build_resbench_index()
        pattern = re.compile(filter_re) if filter_re else None
        prob_ids = []
        for pid, (category, row) in sorted(self._resbench_index.items()):
            haystack = f"{pid} {category} {row.get('module', '')}"
            if pattern and not pattern.search(haystack):
                continue
            prob_ids.append(pid)
        if limit:
            prob_ids = prob_ids[:limit]
        return prob_ids

    # ── Loading ──────────────────────────────────────────────────

    def load_problem(self, prob_id: str) -> ProblemInfo:
        if self.name == "verilogeval":
            return self._load_verilogeval(prob_id)
        if self.name == "rtllm":
            return self._load_rtllm(prob_id)
        if self.name == "cvdp":
            return self._load_cvdp(prob_id)
        if self.name == "resbench":
            return self._load_resbench(prob_id)
        return self._load_realbench(prob_id)

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

    def _load_cvdp(self, prob_id: str) -> ProblemInfo:
        self._build_cvdp_index()
        if prob_id not in self._cvdp_index:
            raise FileNotFoundError(f"CVDP problem not found: {prob_id}")
        row = self._cvdp_index[prob_id]
        input_obj = row.get("input", {})
        prompt = input_obj.get("prompt", "") if isinstance(input_obj, dict) else str(input_obj)
        input_ctx = input_obj.get("context", {}) if isinstance(input_obj, dict) else {}
        if isinstance(input_ctx, dict) and input_ctx:
            ctx_blocks = []
            for rel_path, code in input_ctx.items():
                if str(code).strip():
                    ctx_blocks.append(
                        f"### Context file: {rel_path}\n"
                        f"```systemverilog\n{code}\n```"
                    )
            if ctx_blocks:
                prompt = (
                    f"{prompt.rstrip()}\n\n"
                    "The following RTL/context files are part of the task:\n\n"
                    + "\n\n".join(ctx_blocks)
                )
        output_ctx = (row.get("output") or {}).get("context") or {}
        ref_source_ctx = output_ctx if any(str(code).strip() for code in output_ctx.values()) else input_ctx
        ref_chunks = [str(code) for code in ref_source_ctx.values() if str(code).strip()]
        ref_code = "\n\n".join(ref_chunks) if ref_chunks else "(no public reference Verilog available)"
        design_name = self._cvdp_design_name(row)
        sources = self._cvdp_verilog_sources(row)
        categories = row.get("categories", [])
        return ProblemInfo(
            prob_id=prob_id,
            design_name=design_name,
            prompt_text=prompt,
            ref_code=ref_code,
            testbench_path=self.dataset_dir,
            ref_path=None,
            metadata={
                "dataset": "cvdp",
                "categories": categories,
                "category": next((c for c in categories if c.startswith("cid")), ""),
                "difficulty": next((c for c in categories if c in {"easy", "medium", "hard"}), ""),
                "harness_files": row.get("harness", {}).get("files", {}),
                "verilog_sources": sources,
                "input_context_files": input_ctx if isinstance(input_ctx, dict) else {},
                "cvdp_row": row,
            },
        )

    def _load_realbench(self, prob_id: str) -> ProblemInfo:
        self._build_realbench_index()
        if prob_id not in self._realbench_index:
            raise FileNotFoundError(f"RealBench design not found: {prob_id}")
        design_dir = self._realbench_index[prob_id]
        design_name = design_dir.name
        md_path = design_dir / f"{design_name}.md"
        v_path = design_dir / f"{design_name}.v"
        group = design_dir.parent.name
        return ProblemInfo(
            prob_id=prob_id,
            design_name=design_name,
            prompt_text=md_path.read_text(errors="replace"),
            ref_code=v_path.read_text(errors="replace"),
            testbench_path=design_dir,
            ref_path=v_path,
            metadata={
                "dataset": "realbench",
                "group": group,
                "design_dir": str(design_dir),
            },
        )

    def _load_resbench(self, prob_id: str) -> ProblemInfo:
        self._build_resbench_index()
        if prob_id not in self._resbench_index:
            raise FileNotFoundError(f"ResBench problem not found: {prob_id}")
        category, row = self._resbench_index[prob_id]
        module = row["module"]
        problem = row.get("Problem", "")
        header = row.get("Module header", "")
        prompt = f"{problem}\n\nRequired module header:\n```verilog\n{header}\n```"
        return ProblemInfo(
            prob_id=prob_id,
            design_name=module,
            prompt_text=prompt,
            ref_code=header,
            testbench_path=self.dataset_dir / "problems.json",
            ref_path=None,
            metadata={
                "dataset": "resbench",
                "category": category,
                "testbench": row.get("Testbench", ""),
                "module_header": header,
            },
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
        if self.name == "rtllm":
            return SimConfig(
                pass_pattern=r"Your Design Passed",
                needs_ref_in_compile=False,
                needs_wrapper=True,
                wrapper_module_name=design_name,
            )
        if self.name == "cvdp":
            return SimConfig(
                pass_pattern=r"(passed|PASSED)",
                needs_ref_in_compile=False,
                needs_wrapper=False,
                wrapper_module_name=design_name,
            )
        if self.name == "resbench":
            return SimConfig(
                pass_pattern=r"All tests passed",
                needs_ref_in_compile=False,
                needs_wrapper=False,
                wrapper_module_name=design_name,
            )
        return SimConfig(
            pass_pattern=r"",
            needs_ref_in_compile=False,
            needs_wrapper=False,
            wrapper_module_name=design_name,
        )
