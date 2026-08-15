#!/usr/bin/env python3
"""Run a real two-width ORFS smoke for P3 parameter-family PPA."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from cvdp_specialization import FiniteParameterPlan, SpecializationCase  # noqa: E402
from evaluator import Evaluator  # noqa: E402
from parameter_backends import run_ppa_parameter_policy  # noqa: E402


SMOKE_SV = """module p3_ppa_xor #(
    parameter integer WIDTH = 8
) (
    input logic clk,
    input logic [WIDTH-1:0] lhs,
    input logic [WIDTH-1:0] rhs,
    output logic [WIDTH-1:0] out
);
    assign out = lhs ^ rhs;
endmodule
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pnr",
        action="store_true",
        help="Run place and route after synthesis for both widths.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan = FiniteParameterPlan(
        design_name="p3_ppa_xor",
        parameter_names=("WIDTH",),
        cases=tuple(
            SpecializationCase(
                parameters=(("WIDTH", width),),
                module_name=f"unused_{width}",
            )
            for width in (3, 17)
        ),
    )
    with tempfile.TemporaryDirectory(
        prefix="p3_ppa_orfs_regression_",
        dir=str(PROJECT_ROOT.parent),
    ) as output_dir:
        evaluator = Evaluator(
            project_root=PROJECT_ROOT,
            enable_synth=True,
            enable_pnr=args.pnr,
        )
        manifest = run_ppa_parameter_policy(
            sv_code=SMOKE_SV,
            top_module="p3_ppa_xor",
            prob_id="p3_ppa_orfs_regression",
            plan=plan,
            output_dir=Path(output_dir),
            synth_runner=evaluator._run_synthesis,
            pnr_runner=evaluator._run_pnr if args.pnr else None,
            required=True,
        )
        summary = {
            "status": manifest["status"],
            "coverage": manifest["coverage"],
            "synthesis_family_covered": manifest["synthesis_family_covered"],
            "pnr_family_covered": manifest["pnr_family_covered"],
            "cases": [
                {
                    "parameters": row["parameters"],
                    "synth_status": row["synth_status"],
                    "pnr_status": row["pnr_status"],
                    "area_um2": row["area_um2"],
                    "cell_count": row["cell_count"],
                    "wns_ns": row["wns_ns"],
                    "power_uw": row["power_uw"],
                    "diagnostic": row.get("diagnostic"),
                }
                for row in manifest["cases"]
            ],
        }
        print(json.dumps(summary, indent=2))
        cells = [row["cell_count"] for row in manifest["cases"]]
        if manifest["status"] != "passed" or any(value is None for value in cells):
            return 1
        if len(set(cells)) != len(cells):
            print("PPA smoke did not distinguish the two elaborated widths", file=sys.stderr)
            return 1
    print("P3_PPA_ORFS_REGRESSION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
