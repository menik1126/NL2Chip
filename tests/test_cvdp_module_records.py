from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from evaluator import _module_records, _parse_ref_module_ports, parse_module_ports


@pytest.mark.parametrize("prose", [
    "The module also includes an enable input.",
    "The module integrates a counter and encoder.",
    "This module is a parameterized datapath.",
    "The module takes a serial input.",
    "The module computes (a, b) and registers the result.",
])
def test_markdown_prose_does_not_swallow_real_module(prose):
    code = prose + """
    ```systemverilog
    module dut(input [3:0] data, output [3:0] result);
      assign result = data;
    endmodule
    ```
    """
    assert [name for name, _, _ in _module_records(code)] == ["dut"]
    assert _parse_ref_module_ports(code) == [
        ("input", " [3:0]", "data"), ("output", " [3:0]", "result")
    ]
    assert parse_module_ports(code)[0] == "dut"


@pytest.mark.parametrize("code", [
    "The module is a serial converter.",
    "The module takes input (one bit) and produces output.",
    "module incomplete(input a) without a terminator",
    "module invalid # without a parameter list; endmodule",
])
def test_prose_and_malformed_headers_have_no_reference_ports(code):
    assert _module_records(code) == []
    assert _parse_ref_module_ports(code) is None


@pytest.mark.parametrize("header", ["module empty();", "module empty;", "module empty #();"])
def test_valid_zero_port_modules(header):
    code = header + " endmodule"
    assert [name for name, _, _ in _module_records(code)] == ["empty"]
    assert parse_module_ports(code) == ("empty", [])
    assert _parse_ref_module_ports(code) is None


@pytest.mark.parametrize("ports", ["a,", ",a", "a,,b,", " , "])
def test_empty_nonansi_port_entries_do_not_crash(ports):
    code = f"module legacy({ports}); input a; output b; endmodule"
    names = [name.strip() for name in ports.split(",") if name.strip()]
    expected = [("input" if name == "a" else "output", "", name) for name in names]
    assert _parse_ref_module_ports(code) == (expected or None)


@pytest.mark.parametrize("invalid", [
    "module bogus # no_parameters;",
    "module bogus(input x) no_semicolon",
    "module bogus unrecognized_header",
    "module bogus #(",
    "module bogus(",
])
def test_invalid_header_recovers_at_next_module(invalid):
    code = invalid + "\nmodule actual(input a, output b); assign b=a; endmodule"
    assert [name for name, _, _ in _module_records(code)] == ["actual"]
    assert _parse_ref_module_ports(code) == [("input", "", "a"), ("output", "", "b")]


def test_nested_parameter_expressions_and_nonansi_selection():
    code = """
    module helper(input a); endmodule
    module actual #(parameter N = f(2, g(3, 4)), W = (N + 1)) (x, y, z);
      input [W-1:0] x, y;
      output z;
    endmodule
    """
    assert [name for name, _, _ in _module_records(code)] == ["helper", "actual"]
    assert _parse_ref_module_ports(code, ["x", "z"]) == [
        ("input", " [W-1:0]", "x"),
        ("input", " [W-1:0]", "y"),
        ("output", "", "z"),
    ]
    assert parse_module_ports(code, module_name="actual")[0] == "actual"


def test_comments_and_empty_modules_do_not_hide_real_ports():
    code = """
    // module ignored(input wrong); endmodule
    /* module ignored_too(input wrong); endmodule */
    module empty(); endmodule
    module dut(input a, b, output c); assign c=a & b; endmodule
    """
    assert [name for name, _, _ in _module_records(code)] == ["empty", "dut"]
    assert _parse_ref_module_ports(code) == [
        ("input", "", "a"), ("input", "", "b"), ("output", "", "c")
    ]
