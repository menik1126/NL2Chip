from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from cvdp_harness_adapter import (  # noqa: E402
    CVDP_HARNESS_PROFILE_OFFICIAL,
    CVDP_HARNESS_PROFILE_RACE_SAFE,
    PROGRESS_MONITOR_NAME,
    adapt_cvdp_harness_files,
    cvdp_harness_profile_defaults,
    infer_cvdp_clock_ports,
    infer_cvdp_reset_polarities,
)


def test_versioned_harness_profile_defaults_are_independent_and_validated():
    official = cvdp_harness_profile_defaults(CVDP_HARNESS_PROFILE_OFFICIAL)
    race_safe = cvdp_harness_profile_defaults(CVDP_HARNESS_PROFILE_RACE_SAFE)

    assert official == {
        "normalize_reset_helpers": False,
        "stabilize_cocotb_edges": False,
        "initialize_cocotb_inputs": False,
        "align_reset_release": False,
        "emit_progress_monitor": False,
    }
    assert race_safe == {
        **official,
        "stabilize_cocotb_edges": True,
        "align_reset_release": True,
    }
    race_safe["stabilize_cocotb_edges"] = False
    assert cvdp_harness_profile_defaults(CVDP_HARNESS_PROFILE_RACE_SAFE)[
        "stabilize_cocotb_edges"
    ] is True
    with pytest.raises(ValueError, match="Unknown CVDP harness profile"):
        cvdp_harness_profile_defaults("race-safe-latest")


def test_race_safe_profile_aligns_reset_and_settles_before_stimulus_changes():
    files = {
        "src/test_divider.py": """
import cocotb
from cocotb.triggers import RisingEdge, Timer

async def reset_dut(reset_n, active=True):
    reset_n.value = 0 if active else 1
    await Timer(25, unit="ns")
    reset_n.value = 1 if active else 0

@cocotb.test()
async def test_divider(dut):
    await reset_dut(dut.rst_n, active=True)
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
""",
    }
    options = cvdp_harness_profile_defaults(CVDP_HARNESS_PROFILE_RACE_SAFE)

    adapted, manifest = adapt_cvdp_harness_files(
        files,
        input_ports={"clk", "rst_n", "start"},
        clock_ports={"clk"},
        reset_polarities={"rst_n": "active-low"},
        harness_profile=CVDP_HARNESS_PROFILE_RACE_SAFE,
        **options,
    )

    source = adapted["src/test_divider.py"]
    ast.parse(source)
    assert (
        "await reset_dut(dut.rst_n, active=True)\n"
        "    await FallingEdge(dut.clk)\n"
        "    await Timer(1, unit='step')\n"
        "    dut.start.value = 1"
    ) in source
    assert (
        "await RisingEdge(dut.clk)\n"
        "    await Timer(1, unit='step')\n"
        "    dut.start.value = 0"
    ) in source
    assert manifest["harness_profile"] == CVDP_HARNESS_PROFILE_RACE_SAFE
    assert manifest["harness_profile_overrides"] == {}
    assert manifest["effective_options"] == options


def test_reset_inference_evaluates_active_false_at_call_site():
    files = {
        "src/harness_library.py": """
from cocotb.triggers import Timer

async def reset_dut(reset_n, duration_ns=25, active: bool=False):
    reset_n.value = 0 if active else 1
    await Timer(duration_ns, unit="ns")
    reset_n.value = 1 if active else 0
""",
        "src/test_dut.py": """
import harness_library as hrs_lb

async def test_dut(dut):
    await hrs_lb.reset_dut(dut.reset, active=False)
""",
    }

    assert infer_cvdp_reset_polarities(files, ["reset"]) == {
        "reset": "active-high"
    }


def test_reset_inference_uses_default_active_true_at_call_site():
    files = {
        "src/test_axi.py": """
from cocotb.triggers import Timer

async def reset_dut(reset_signal, duration_ns=25, active=True):
    reset_signal.value = 0 if active else 1
    await Timer(duration_ns, unit="ns")
    reset_signal.value = 1 if active else 0

async def test_dut(dut):
    await reset_dut(dut.axi_aresetn)
""",
    }

    assert infer_cvdp_reset_polarities(files, ["axi_aresetn"]) == {
        "axi_aresetn": "active-low"
    }


def test_reset_inference_reads_direct_dut_drive_sequence():
    files = {
        "src/test_dut.py": """
async def test_dut(dut):
    dut.rst.value = 1
    await RisingEdge(dut.clk)
    dut.rst.value = 0
""",
    }

    assert infer_cvdp_reset_polarities(files, ["rst"]) == {
        "rst": "active-high"
    }


def test_reversed_reset_helper_is_normalized_and_audited():
    files = {
        "src/harness_library.py": """
from cocotb.triggers import Timer

async def reset_dut(reset_n, duration_ns=25, active: bool=False):
    reset_n.value = 0 if active else 1
    await Timer(duration_ns, unit="ns")
    reset_n.value = 1 if active else 0
""",
        "src/test_dut.py": """
import harness_library as hrs_lb

async def test_dut(dut):
    await hrs_lb.reset_dut(dut.reset, active=False)
""",
    }

    adapted, manifest = adapt_cvdp_harness_files(
        files,
        reset_polarities={"reset": "active-low"},
        normalize_reset_helpers=True,
        stabilize_cocotb_edges=False,
    )

    source = adapted["src/harness_library.py"]
    ast.parse(source)
    assert "reset_n.value = 1 if active else 0" in source
    assert "reset_n.value = 0 if active else 1" in source
    assert manifest["changed_file_count"] == 1
    assert manifest["transformations"][0]["code"] == (
        "reset_helper_assert_deassert_reversed"
    )
    assert manifest["reset_polarities"] == {"reset": "active-low"}
    assert manifest["reset_contract_checks"][0]["contract_match"] is False
    assert manifest["reset_contract_checks"][0]["contract_match_after_swap"] is True


def test_active_true_low_reset_helper_is_not_reversed():
    files = {
        "src/test_axi.py": """
from cocotb.triggers import Timer

async def reset_dut(reset_signal, duration_ns=25, active=True):
    reset_signal.value = 0 if active else 1
    await Timer(duration_ns, unit="ns")
    reset_signal.value = 1 if active else 0

async def test_dut(dut):
    await reset_dut(dut.axi_aresetn)
""",
    }

    adapted, manifest = adapt_cvdp_harness_files(
        files,
        reset_polarities={"axi_aresetn": "active-low"},
        normalize_reset_helpers=True,
        stabilize_cocotb_edges=False,
    )

    assert adapted == files
    assert manifest["changed_file_count"] == 0
    assert manifest["transformations"] == []
    assert manifest["reset_contract_checks"][0]["contract_match"] is True


def test_clock_inference_prefers_port_exercised_by_cocotb_clock():
    files = {
        "src/test_dut.py": """
async def test_dut(dut):
    cocotb.start_soon(Clock(dut.axi_aclk, 10, unit="ns").start())
""",
    }

    assert infer_cvdp_clock_ports(
        files,
        {"axi_aclk", "clock_enable", "axi_aresetn"},
    ) == ["axi_aclk"]


def test_reset_release_is_aligned_to_falling_clock_edge():
    files = {
        "src/test_dut.py": """
from cocotb.triggers import Timer

async def reset_dut(reset_n, duration_ns=25, active=False):
    reset_n.value = 0 if active else 1
    await Timer(duration_ns, unit="ns")
    reset_n.value = 1 if active else 0

async def test_dut(dut):
    await reset_dut(dut.rst, active=True)
    dut.start.value = 1
""",
    }

    adapted, manifest = adapt_cvdp_harness_files(
        files,
        reset_polarities={"rst": "active-low"},
        input_ports={"clk", "rst", "start"},
        clock_ports={"clk"},
        normalize_reset_helpers=False,
        stabilize_cocotb_edges=False,
        initialize_cocotb_inputs=False,
        align_reset_release=True,
    )

    source = adapted["src/test_dut.py"]
    ast.parse(source)
    assert "from cocotb.triggers import Timer, FallingEdge" in source
    assert (
        "await reset_dut(dut.rst, active=True)\n"
        "    await FallingEdge(dut.clk)\n"
        "    dut.start.value = 1"
    ) in source
    assert manifest["clock_ports"] == ["clk"]
    assert manifest["transformations"] == [{
        "code": "cocotb_reset_release_aligned",
        "file": "src/test_dut.py",
        "alignments": [{"line": 10, "reset": "rst", "clock": "clk"}],
    }]


def test_reset_release_selects_matching_clock_prefix():
    files = {
        "src/test_dut.py": """
from cocotb.triggers import Timer

async def reset_dut(reset_n, duration_ns=25, active=True):
    reset_n.value = 0 if active else 1
    await Timer(duration_ns, unit="ns")
    reset_n.value = 1 if active else 0

async def test_dut(dut):
    await reset_dut(dut.axi_aresetn)
    dut.axi_awvalid.value = 1
""",
    }

    adapted, manifest = adapt_cvdp_harness_files(
        files,
        reset_polarities={"axi_aresetn": "active-low"},
        input_ports={"axi_aclk", "video_clk", "axi_aresetn", "axi_awvalid"},
        clock_ports={"axi_aclk", "video_clk"},
        align_reset_release=True,
        stabilize_cocotb_edges=False,
        initialize_cocotb_inputs=False,
    )

    assert "await FallingEdge(dut.axi_aclk)" in adapted["src/test_dut.py"]
    assert manifest["warnings"] == []


def test_cocotb_edges_wait_one_simulator_step_for_settling():
    files = {
        "src/test_dut.py": """
import cocotb
from cocotb.triggers import RisingEdge

@cocotb.test()
async def test_dut(dut):
    await RisingEdge(dut.clk)
    assert dut.done.value == 1
""",
    }

    adapted, manifest = adapt_cvdp_harness_files(
        files,
        normalize_reset_helpers=False,
        stabilize_cocotb_edges=True,
    )

    source = adapted["src/test_dut.py"]
    tree = ast.parse(source)
    assert "from cocotb.triggers import RisingEdge, Timer" in source
    assert "await RisingEdge(dut.clk)\n    await Timer(1, unit='step')" in source
    assert sum(
        isinstance(node, ast.Await)
        and getattr(getattr(node.value, "func", None), "id", None) == "Timer"
        for node in ast.walk(tree)
    ) == 1
    assert manifest["transformations"][0] == {
        "code": "cocotb_post_edge_settle_inserted",
        "file": "src/test_dut.py",
        "edge_waits": 1,
    }


def test_cocotb_edge_scheduling_is_preserved_by_default():
    files = {
        "src/test_dut.py": """
import cocotb
from cocotb.triggers import RisingEdge

@cocotb.test()
async def test_dut(dut):
    await RisingEdge(dut.clk)
    assert dut.done.value == 1
""",
    }

    adapted, manifest = adapt_cvdp_harness_files(
        files,
        normalize_reset_helpers=False,
        initialize_cocotb_inputs=False,
        align_reset_release=False,
    )

    assert adapted == files
    assert manifest["reset_helper_normalization_enabled"] is False
    assert manifest["cocotb_phase_stabilization_enabled"] is False
    assert manifest["cocotb_input_initialization_enabled"] is False
    assert manifest["cocotb_reset_release_alignment_enabled"] is False
    assert manifest["cocotb_progress_monitor_enabled"] is False
    assert manifest["transformations"] == []


def test_cocotb_public_inputs_are_initialized_but_clock_and_reset_are_not():
    files = {
        "src/test_dut.py": """
import cocotb

@cocotb.test()
async def test_dut(dut):
    \"\"\"Exercise a sequential datapath.\"\"\"
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    dut.start.value = 1
""",
    }

    adapted, manifest = adapt_cvdp_harness_files(
        files,
        input_ports={"clk", "rst", "num", "start"},
        normalize_reset_helpers=False,
        stabilize_cocotb_edges=False,
        initialize_cocotb_inputs=True,
    )

    source = adapted["src/test_dut.py"]
    tree = ast.parse(source)
    function = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
    )
    assert ast.get_docstring(function) == "Exercise a sequential datapath."
    assert "dut.num.value = 0" in source
    assert "dut.start.value = 0" in source
    assert "dut.clk.value = 0" not in source
    assert source.count("dut.rst.value = 0") == 1
    assert manifest["public_input_ports"] == ["clk", "num", "rst", "start"]
    assert manifest["transformations"][0]["code"] == (
        "cocotb_public_inputs_initialized"
    )


def test_progress_monitor_reports_only_public_dut_values():
    files = {
        "src/test_axi.py": """
import cocotb
from cocotb.triggers import RisingEdge

@cocotb.test()
async def test_axi(dut):
    await RisingEdge(dut.axi_aclk)
""",
    }

    adapted, manifest = adapt_cvdp_harness_files(
        files,
        input_ports={"axi_aclk", "axi_awvalid"},
        observed_ports={"axi_aclk", "axi_awvalid", "axi_awready"},
        normalize_reset_helpers=False,
        stabilize_cocotb_edges=False,
        initialize_cocotb_inputs=False,
        align_reset_release=False,
        emit_progress_monitor=True,
    )

    source = adapted["src/test_axi.py"]
    tree = ast.parse(source)
    assert f"cocotb.start_soon({PROGRESS_MONITOR_NAME}(dut))" in source
    assert "[CVDP_PROGRESS after=" in source
    assert "expected" not in source.lower()
    monitor = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == PROGRESS_MONITOR_NAME
    )
    assert monitor is not None
    assert manifest["public_observed_ports"] == [
        "axi_aclk", "axi_awready", "axi_awvalid"
    ]
    transformation = manifest["transformations"][0]
    assert transformation["code"] == (
        "cocotb_public_port_progress_monitor_inserted"
    )
    assert transformation["snapshot_delays_ns"] == [
        10, 10, 10, 10, 10, 10, 10, 10, 10, 10,
        20, 30, 50, 100, 500, 9000,
    ]


def test_unparseable_harness_is_preserved_with_warning():
    files = {"src/test_bad.py": "async def broken(:\n"}

    adapted, manifest = adapt_cvdp_harness_files(files)

    assert adapted == files
    assert manifest["changed_file_count"] == 0
    assert manifest["warnings"][0]["code"] == "harness_python_parse_failed"
