from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _is_reserved(position: int) -> bool:
    return position == 0 or position & (position - 1) == 0


def _scatter(data: int, data_width: int, parity_width: int) -> int:
    encoded_width = data_width + parity_width + 1
    result = 0
    data_index = 0
    for position in range(encoded_width):
        if _is_reserved(position):
            continue
        result |= ((data >> data_index) & 1) << position
        data_index += 1
    return result


def _indexed_parity(value: int, width: int, parity_width: int) -> int:
    result = 0
    for parity_index in range(parity_width):
        parity = 0
        for position in range(width):
            if (position >> parity_index) & 1:
                parity ^= (value >> position) & 1
        result |= parity << parity_index
    return result


def _place_parity(base: int, parity: int, width: int) -> int:
    result = base
    position = 1
    parity_index = 0
    while position < width:
        result &= ~(1 << position)
        result |= ((parity >> parity_index) & 1) << position
        parity_index += 1
        position <<= 1
    return result


def _encode(data: int, data_width: int, parity_width: int) -> tuple[int, int, int]:
    encoded_width = data_width + parity_width + 1
    scattered = _scatter(data, data_width, parity_width)
    parity = _indexed_parity(scattered, encoded_width, parity_width)
    placed = _place_parity(scattered, parity, encoded_width)
    encoded = placed | ((placed.bit_count() & 1) << 0)
    return scattered, parity, encoded


def _literal(width: int, value: int) -> str:
    return f"{width}'h{value:x}"


def test_indexed_parameter_primitives_at_unseen_widths(tmp_path: Path):
    for command in ("lake", "lean", "iverilog", "vvp"):
        assert shutil.which(command), f"required command is unavailable: {command}"

    lean_sim = subprocess.run(
        ["lake", "env", "lean", "Tests/IndexedParameterSim.lean"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert lean_sim.returncode == 0, lean_sim.stdout + lean_sim.stderr
    assert "INDEXED_PARAMETER_LEAN_SIM_PASS" in lean_sim.stdout

    emitted = subprocess.run(
        ["lake", "env", "lean", "Tests/IndexedParameterEmit.lean"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert emitted.returncode == 0, emitted.stdout + emitted.stderr
    generated_sv = tmp_path / "indexed_parameters.sv"
    generated_sv.write_text(emitted.stdout, encoding="utf-8")

    cases = [(4, 3, 0xB), (8, 4, 0xA5), (16, 5, 0xBEEF)]
    declarations = [
        "logic [16:0] identity_in;",
        "wire [16:0] identity_out;",
        "logic [7:0] chunks_in;",
        "wire [19:0] chunks_out;",
        "logic [3:0] primitive_data;",
        "wire [7:0] scatter_out;",
        "wire [2:0] parity_out;",
        "wire [7:0] placed_out;",
        "wire [3:0] gather_out;",
        "wire [15:0] roundtrip_out;",
        "logic [15:0] roundtrip_in;",
    ]
    instances = [
        "indexedGenerateIdentity #(.W(17)) identity_dut "
        "(._gen_data(identity_in), .out(identity_out));",
        "indexedGenerateChunks #(.N(5)) chunks_dut "
        "(._gen_data(chunks_in), .out(chunks_out));",
        "indexedScatter #(.DATAW(4), .PARITYW(3)) scatter_dut "
        "(._gen_data(primitive_data), .out(scatter_out));",
        "indexedParity #(.W(8), .PARITYW(3)) parity_dut "
        "(._gen_value(scatter_out), .out(parity_out));",
        "indexedPlaceParity #(.DATAW(4), .PARITYW(3)) place_dut "
        "(._gen_base(scatter_out), ._gen_parity(parity_out), .out(placed_out));",
        "indexedGather #(.DATAW(4), .PARITYW(3)) gather_dut "
        "(._gen_encoded(placed_out), .out(gather_out));",
        "indexedRoundTrip #(.DATAW(16), .PARITYW(5)) roundtrip_dut "
        "(._gen_data(roundtrip_in), .out(roundtrip_out));",
    ]
    checks = [
        "identity_in = 17'h15555;",
        "chunks_in = 8'h00;",
        "primitive_data = 4'hb;",
        "roundtrip_in = 16'hbeef;",
        "#1;",
        "if (identity_out !== identity_in) $fatal(1, \"indexed identity mismatch\");",
        "if (chunks_out !== 20'h43210) $fatal(1, \"indexed chunks mismatch\");",
        f"if (scatter_out !== {_literal(8, _scatter(0xB, 4, 3))}) "
        "$fatal(1, \"scatter mismatch\");",
        f"if (parity_out !== {_literal(3, _encode(0xB, 4, 3)[1])}) "
        "$fatal(1, \"parity mismatch\");",
        f"if (placed_out !== {_literal(8, _encode(0xB, 4, 3)[2] & ~1)}) "
        "$fatal(1, \"parity placement mismatch\");",
        "if (gather_out !== primitive_data) $fatal(1, \"gather mismatch\");",
        "if (roundtrip_out !== roundtrip_in) $fatal(1, \"round-trip mismatch\");",
    ]

    for data_width, parity_width, data in cases:
        encoded_width = data_width + parity_width + 1
        tag = f"d{data_width}p{parity_width}"
        declarations.extend([
            f"logic [{data_width - 1}:0] encode_{tag}_in;",
            f"wire [{encoded_width - 1}:0] encode_{tag}_out;",
            f"logic [{encoded_width - 1}:0] decode_{tag}_in;",
            f"wire [{data_width - 1}:0] decode_{tag}_out;",
        ])
        instances.extend([
            f"indexedHammingEncode #(.DATAW({data_width}), .PARITYW({parity_width})) "
            f"encode_{tag}_dut (._gen_data(encode_{tag}_in), .out(encode_{tag}_out));",
            f"indexedHammingDecode #(.DATAW({data_width}), .PARITYW({parity_width})) "
            f"decode_{tag}_dut (._gen_encoded(decode_{tag}_in), .out(decode_{tag}_out));",
        ])
        encoded = _encode(data, data_width, parity_width)[2]
        checks.extend([
            f"encode_{tag}_in = {_literal(data_width, data)};",
            f"decode_{tag}_in = {_literal(encoded_width, encoded)};",
            "#1;",
            f"if (encode_{tag}_out !== {_literal(encoded_width, encoded)}) "
            f"$fatal(1, \"encode {tag} mismatch\");",
            f"if (decode_{tag}_out !== {_literal(data_width, data)}) "
            f"$fatal(1, \"decode {tag} mismatch\");",
            f"decode_{tag}_in = {_literal(encoded_width, encoded ^ (1 << 3))};",
            "#1;",
            f"if (decode_{tag}_out !== {_literal(data_width, data)}) "
            f"$fatal(1, \"single-error correction {tag} mismatch\");",
        ])

    testbench = "\n".join([
        "module indexed_parameter_behavior_tb;",
        *[f"    {line}" for line in declarations],
        "",
        *[f"    {line}" for line in instances],
        "",
        "    initial begin",
        *[f"        {line}" for line in checks],
        "        $display(\"INDEXED_PARAMETER_BEHAVIOR_PASS\");",
        "        $finish;",
        "    end",
        "endmodule",
        "",
    ])
    tb_file = tmp_path / "indexed_parameter_behavior.sv"
    tb_file.write_text(testbench, encoding="utf-8")
    executable = tmp_path / "indexed_parameter_behavior"
    compiled = subprocess.run(
        [
            "iverilog", "-g2012", "-s", "indexed_parameter_behavior_tb",
            "-o", str(executable), str(generated_sv), str(tb_file),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    assert "expects" not in compiled.stderr, compiled.stderr
    simulated = subprocess.run(
        ["vvp", str(executable)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert simulated.returncode == 0, simulated.stdout + simulated.stderr
    assert "INDEXED_PARAMETER_BEHAVIOR_PASS" in simulated.stdout
