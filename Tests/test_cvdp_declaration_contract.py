from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from evaluator import generate_cvdp_wrapper, parse_module_ports, _cvdp_parse_module_parameters


@pytest.mark.parametrize("header", ["", " ", "a, y,", "a,, y"])
def test_reference_nonansi_empty_entries_do_not_crash(header):
    from evaluator import _parse_ref_module_ports
    source = f"module dut({header}); input a; output y; endmodule"
    expected = [("input", "", "a"), ("output", "", "y")] if header.strip() else None
    assert _parse_ref_module_ports(source) == expected


def test_empty_helper_header_does_not_hide_real_module_ports():
    from evaluator import _parse_ref_module_ports
    source = "module helper(); endmodule module dut(a,y); input a; output y; endmodule"
    assert _parse_ref_module_ports(source, ["a", "y"]) == [("input", "", "a"), ("output", "", "y")]


def compile_wrapper(tmp_path, core, public, harness, parameters=()):
    module, ports = parse_module_ports(core)
    wrapper = generate_cvdp_wrapper("dut", module, ports, public, harness, sv_code=core)
    assert wrapper is not None
    source = tmp_path / "dut.sv"
    source.write_text(core + "\n" + wrapper)
    if shutil.which("iverilog") is None:
        pytest.skip("iverilog is required")
    result = subprocess.run(
        ["iverilog", "-g2012", "-s", "dut", "-o", str(tmp_path / "dut.vvp"),
         *[f"-Pdut.{name}={value}" for name, value in parameters], str(source)],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return wrapper


def test_observed_declared_parameters_are_not_output_ports(tmp_path):
    core = "module core(input [7:0] _gen_data, output [7:0] result); assign result = _gen_data; endmodule"
    public = "module dut #(parameter NBW_DATA=8, parameter NBW_KEY=4)(input [NBW_DATA-1:0] data, output [NBW_DATA-1:0] result); endmodule"
    harness = {"src/test.py": "n = int(dut.NBW_DATA.value)\nk = int(dut.NBW_KEY.value)\ndut.data.value = 0\nr = int(dut.result.value)"}
    wrapper = compile_wrapper(tmp_path, core, public, harness)
    assert {name for _, _, name in parse_module_ports(wrapper)[1]} == {"data", "result"}
    assert "assign NBW_DATA" not in wrapper
    assert "assign NBW_KEY" not in wrapper


@pytest.mark.parametrize("count", [2, 4, 9])
def test_header_localparams_preserve_symbolic_dependency_chain(tmp_path, count):
    public = """module dut #(
        parameter integer COUNT = 4,
        localparam integer INNER = $clog2(COUNT),
        localparam integer INDEX_W = ((INNER > 0) ? INNER : 1)
    )(input [INDEX_W-1:0] data, output [INDEX_W-1:0] result); endmodule"""
    core = """module core #(parameter integer COUNT=4)(
        input [((($clog2(COUNT)>0)?$clog2(COUNT):1))-1:0] _gen_data,
        output [((($clog2(COUNT)>0)?$clog2(COUNT):1))-1:0] result);
        assign result = _gen_data; endmodule"""
    wrapper = compile_wrapper(tmp_path, core, public, {}, [("COUNT", count)])
    assert "localparam integer INNER = $clog2(COUNT)" in wrapper
    assert "localparam integer INDEX_W = ((INNER > 0) ? INNER : 1)" in wrapper
    assert ".INDEX_W(" not in wrapper


def test_parameter_declarations_are_scoped_to_selected_module():
    public = """module helper #(parameter X=99)(input x); endmodule
    module dut #(parameter integer X=2, Y=3, localparam integer Z=(X+Y))(input [Z-1:0] x); endmodule"""
    declarations = _cvdp_parse_module_parameters(public, set(), module_name="dut")
    assert declarations == ["parameter integer X=2", "parameter integer Y=3", "localparam integer Z=(X+Y)"]


def test_localparam_observation_is_not_promoted_to_overridable_parameter(tmp_path):
    core = "module core(input [2:0] _gen_data, output [2:0] result); assign result=_gen_data; endmodule"
    public = "module dut #(parameter COUNT=8, localparam SrcWidth=$clog2(COUNT))(input [SrcWidth-1:0] data, output [SrcWidth-1:0] result); endmodule"
    harness = {"src/test.py": "width = int(dut.SrcWidth.value)\ndut.data.value = 0\nr = int(dut.result.value)"}
    wrapper = compile_wrapper(tmp_path, core, public, harness)
    assert "localparam SrcWidth=$clog2(COUNT)" in wrapper
    assert "parameter SrcWidth = 1" not in wrapper
    assert "output logic SrcWidth" not in wrapper


def test_core_localparams_are_not_forwarded_as_parameter_overrides(tmp_path):
    core = """module core #(parameter COUNT=8, localparam integer SrcWidth=$clog2(COUNT), CopyWidth=SrcWidth)
        (input [SrcWidth-1:0] _gen_data, output [SrcWidth-1:0] result);
        assign result = _gen_data; endmodule"""
    public = """module dut #(parameter COUNT=8, localparam integer SrcWidth=$clog2(COUNT))
        (input [SrcWidth-1:0] data, output [SrcWidth-1:0] result); endmodule"""
    wrapper = compile_wrapper(tmp_path, core, public, {}, [("COUNT", 16)])
    assert ".COUNT(COUNT)" in wrapper
    assert ".SrcWidth(" not in wrapper
    assert ".CopyWidth(" not in wrapper
