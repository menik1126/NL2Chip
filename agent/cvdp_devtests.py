"""Deterministic, public-spec-only development tests for the CVDP12 slice.

This module deliberately has a narrow input boundary.  It accepts only a
problem id, the public prompt, and public input context files.  In particular,
it never accepts a benchmark ``ProblemInfo`` and therefore cannot accidentally
copy a hidden harness, reference implementation, or output context into model
feedback.

The generated tests are *development* evidence.  They are not a replacement
for a one-shot hidden holdout.  Their counterexamples are safe to return to a
model because every vector and every oracle below is derived from the public
specification and the fixed seed in this file.
"""
from __future__ import annotations

import hashlib
import json
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping

from dataset import ProblemInfo


CVDP_PUBLIC_DEV_VERSION = "cvdp-public-dev-v2"
DEFAULT_CVDP_DEV_SEED = 0xC0D3_2026
PUBLIC_SPEC_SOURCE = "public_spec"
CVDP_PUBLIC_DEV_SOURCE = PUBLIC_SPEC_SOURCE


Port = tuple[str, str, str]


class _FrozenDict(dict):
    """A JSON-compatible read-only dictionary used at the trust boundary."""

    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("generated public development mappings are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return _FrozenDict({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class PublicCVDPInput:
    """Deep-copied immutable view of only a CVDP row's public input fields."""

    prob_id: str
    prompt_text: str
    input_context_files: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "input_context_files",
            MappingProxyType(dict(self.input_context_files)),
        )


@dataclass(frozen=True)
class GeneratedDevSuite:
    """A generated public development suite, or an explicit closed failure."""

    prob_id: str
    supported: bool
    reason: str | None
    public_info: ProblemInfo | None
    harness_files: Mapping[str, str]
    verilog_sources: tuple[str, ...]
    benchmark_ports: tuple[Port, ...]
    seed: int
    version: str
    sha256: str
    source: str = CVDP_PUBLIC_DEV_SOURCE

    def __post_init__(self) -> None:
        object.__setattr__(self, "harness_files", _deep_freeze(self.harness_files))

    def validate(self) -> None:
        """Validate provenance, sanitization, identity, and canonical digest."""
        if self.version != CVDP_PUBLIC_DEV_VERSION:
            raise ValueError("public dev suite version mismatch")
        if self.source != CVDP_PUBLIC_DEV_SOURCE:
            raise ValueError("public dev suite source mismatch")
        if not self.supported:
            expected = _unsupported_digest(self.prob_id, self.seed, self.reason)
            if any((self.public_info, self.harness_files, self.verilog_sources, self.benchmark_ports)):
                raise ValueError("unsupported public dev suite carries executable content")
            if self.sha256 != expected:
                raise ValueError("unsupported public dev suite digest mismatch")
            return
        if self.reason is not None or self.public_info is None:
            raise ValueError("supported public dev suite has inconsistent status")
        template = _TEMPLATES.get(self.prob_id)
        if template is None:
            raise ValueError("supported public dev suite is outside the trusted catalog")
        info = self.public_info
        if (
            info.prob_id != self.prob_id
            or info.design_name != template.design_name
            or info.ref_code
            or info.ref_path is not None
            or info.testbench_path != Path(f"generated-public-dev/{self.prob_id}")
        ):
            raise ValueError("public dev ProblemInfo identity/reference boundary violated")
        metadata = info.metadata or {}
        allowed_metadata = {
            "dataset", "public_dev_generated", "public_dev_source",
            "public_dev_version", "public_dev_seed", "public_dev_sha256",
            "public_input_context_sha256", "public_prompt_text",
            "public_dev_oracle_scope", "harness_files", "verilog_sources",
            "public_input_context_files", "input_context_files",
            "benchmark_ports",
        }
        if set(metadata) != allowed_metadata:
            raise ValueError("public dev metadata contains unknown or forbidden fields")
        context = metadata.get("public_input_context_files")
        raw_prompt = metadata.get("public_prompt_text")
        if not isinstance(context, Mapping) or not isinstance(raw_prompt, str):
            raise ValueError("public dev context provenance is malformed")
        sanitized_context, error = _sanitize_context(context)
        if error or sanitized_context != dict(context):
            raise ValueError("public dev context provenance is malformed")
        if (
            _canonical_sha({"prompt": raw_prompt, "context": sanitized_context})
            != _AUDITED_PUBLIC_INPUT_SHA256.get(self.prob_id)
        ):
            raise ValueError("public prompt/context fingerprint is not audited")
        try:
            compilation_context = _compilation_context(
                sanitized_context, template.design_name
            )
        except ValueError as error:
            raise ValueError("public compilation context is malformed") from error
        expected_harness, expected_sources = _generated_harness(
            template, compilation_context, self.seed
        )
        expected_hashes = {
            path: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for path, content in sanitized_context.items()
        }
        expected_digest = _supported_digest(
            prob_id=self.prob_id,
            design_name=template.design_name,
            prompt_text=raw_prompt,
            seed=self.seed,
            context_hashes=expected_hashes,
            compilation_context=compilation_context,
            harness_files=expected_harness,
            verilog_sources=expected_sources,
            benchmark_ports=template.ports,
        )
        expected_metadata = _generated_metadata(
            prob_id=self.prob_id,
            prompt_text=raw_prompt,
            seed=self.seed,
            digest=expected_digest,
            context=sanitized_context,
            context_hashes=expected_hashes,
            compilation_context=compilation_context,
            harness_files=expected_harness,
            verilog_sources=expected_sources,
            benchmark_ports=template.ports,
        )
        if info.prompt_text != _render_public_prompt(raw_prompt, sanitized_context):
            raise ValueError("rendered public prompt does not match raw public input")
        if self.harness_files != expected_harness:
            raise ValueError("public dev harness differs from the trusted template")
        if self.verilog_sources != expected_sources:
            raise ValueError("public dev sources differ from the trusted template")
        if self.benchmark_ports != template.ports:
            raise ValueError("public dev ports differ from the trusted template")
        if metadata != expected_metadata:
            raise ValueError("public dev metadata differs from the trusted template")
        if self.sha256 != expected_digest:
            raise ValueError("public dev suite canonical digest mismatch")


@dataclass(frozen=True)
class _Template:
    design_name: str
    required_prompt_anchors: tuple[str, ...]
    parameters: tuple[dict[str, int], ...]
    ports: tuple[Port, ...]
    body: str


_COMMON_TEST_HEADER = r'''
import json
import math
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer


PARAMETERS = json.loads(os.environ["CVDP_PUBLIC_DEV_PARAMETERS"])
PUBLIC_SEED = int(os.environ["CVDP_PUBLIC_DEV_SEED"])


def _case_seed():
    value = PUBLIC_SEED & 0xFFFFFFFF
    for byte in json.dumps(PARAMETERS, sort_keys=True).encode("utf-8"):
        value = ((value * 16777619) ^ byte) & 0xFFFFFFFF
    return value


RNG = random.Random(_case_seed())


def _read(signal, name):
    try:
        return int(signal.value)
    except (TypeError, ValueError) as error:
        raise AssertionError(
            f"CVDP_PUBLIC_DEV_MISMATCH signal={name} actual=unresolved "
            f"parameters={PARAMETERS} seed={PUBLIC_SEED}"
        ) from error


def _expect(label, actual, expected, vector):
    assert actual == expected, (
        f"CVDP_PUBLIC_DEV_MISMATCH label={label} input={json.dumps(vector, sort_keys=True)} "
        f"expected={expected} actual={actual} parameters={PARAMETERS} seed={PUBLIC_SEED}"
    )


async def _settle():
    await Timer(1, unit="ns")


async def _tick(dut, clock_name):
    await RisingEdge(getattr(dut, clock_name))
    await _settle()

'''


_WORD_BODY = r'''
@cocotb.test()
async def public_dev_word_reducer(dut):
    width = int(PARAMETERS["BIT_WIDTH"])
    mask = (1 << width) - 1
    vectors = [(0, 0), (0, mask), (mask, 0), (mask, mask)]
    vectors += [(RNG.randrange(mask + 1), RNG.randrange(mask + 1)) for _ in range(20)]
    for a, b in vectors:
        dut.input_A.value = a
        dut.input_B.value = b
        await _settle()
        _expect(
            "bit_difference_count",
            _read(dut.bit_difference_count, "bit_difference_count"),
            (a ^ b).bit_count(),
            {"input_A": a, "input_B": b},
        )
'''


_SWIZZLE_BODY = r'''
def _reverse(value, width):
    result = 0
    for index in range(width):
        result |= ((value >> index) & 1) << (width - 1 - index)
    return result


def _swizzle(value, width, sel):
    sections = 1 << sel
    section_width = width // sections
    section_mask = (1 << section_width) - 1
    result = 0
    for base in range(0, width, section_width):
        section = (value >> base) & section_mask
        result |= _reverse(section, section_width) << base
    return result


@cocotb.test()
async def public_dev_swizzle(dut):
    width = int(PARAMETERS["DATA_WIDTH"])
    mask = (1 << width) - 1
    values = [0, 1, mask, int("a5" * (width // 8), 16)]
    values += [RNG.randrange(mask + 1) for _ in range(12)]
    for sel in range(4):
        for value in values:
            dut.data_in.value = value
            dut.sel.value = sel
            await _settle()
            _expect(
                "data_out",
                _read(dut.data_out, "data_out"),
                _swizzle(value, width, sel),
                {"data_in": value, "sel": sel},
            )
'''


_SYNC_LIFO_BODY = r'''
@cocotb.test()
async def public_dev_sync_lifo(dut):
    width = int(PARAMETERS["DATA_WIDTH"])
    depth = 1 << int(PARAMETERS["ADDR_WIDTH"])
    mask = (1 << width) - 1
    dut.clock.value = 0
    dut.reset.value = 1
    dut.write_en.value = 0
    dut.read_en.value = 0
    dut.data_in.value = 0
    cocotb.start_soon(Clock(dut.clock, 10, unit="ns").start())
    await _tick(dut, "clock")
    dut.reset.value = 0
    await _tick(dut, "clock")
    _expect("empty_after_reset", _read(dut.empty, "empty"), 1, {})
    _expect("full_after_reset", _read(dut.full, "full"), 0, {})
    _expect("data_out_after_reset", _read(dut.data_out, "data_out"), 0, {})

    values = [((index * 7) + 3) & mask for index in range(depth)]
    for index, value in enumerate(values):
        dut.data_in.value = value
        dut.write_en.value = 1
        await _tick(dut, "clock")
        _expect("empty_after_write", _read(dut.empty, "empty"), 0, {"index": index})
    dut.write_en.value = 0
    _expect("full_at_depth", _read(dut.full, "full"), 1, {"depth": depth})

    # Overflow is ignored.
    dut.data_in.value = mask ^ values[-1]
    dut.write_en.value = 1
    await _tick(dut, "clock")
    dut.write_en.value = 0
    for index, expected in enumerate(reversed(values)):
        dut.read_en.value = 1
        await _tick(dut, "clock")
        _expect("lifo_pop", _read(dut.data_out, "data_out"), expected, {"pop_index": index})
    dut.read_en.value = 0
    _expect("empty_after_drain", _read(dut.empty, "empty"), 1, {})
    _expect("full_after_drain", _read(dut.full, "full"), 0, {})

    # Underflow holds the previous output.
    held = _read(dut.data_out, "data_out")
    dut.read_en.value = 1
    await _tick(dut, "clock")
    _expect("underflow_holds", _read(dut.data_out, "data_out"), held, {})
'''


_GF_BODY = r'''
def _gf_mul(left, right):
    product = 0
    for _ in range(8):
        if right & 1:
            product ^= left
        high = left & 0x80
        left = (left << 1) & 0xFF
        if high:
            left ^= 0x1B  # low eight bits of the public 0x11B polynomial
        right >>= 1
    return product


def _gf_mac(a, b, width):
    result = 0
    for offset in range(0, width, 8):
        result ^= _gf_mul((a >> offset) & 0xFF, (b >> offset) & 0xFF)
    return result


@cocotb.test()
async def public_dev_gf_mac(dut):
    width = int(PARAMETERS["WIDTH"])
    mask = (1 << width) - 1
    values = [(0, 0), (mask, mask), (1, 1)]
    values += [(RNG.randrange(mask + 1), RNG.randrange(mask + 1)) for _ in range(20)]
    valid = width % 8 == 0
    for a, b in values:
        dut.a.value = a
        dut.b.value = b
        await _settle()
        expected = _gf_mac(a, b, width) if valid else 0
        vector = {"a": a, "b": b, "WIDTH": width}
        _expect("result", _read(dut.result, "result"), expected, vector)
        _expect("error_flag", _read(dut.error_flag, "error_flag"), int(not valid), vector)
        _expect("valid_result", _read(dut.valid_result, "valid_result"), int(valid), vector)
'''


_PARKING_BODY = r'''
async def _wait_counts(dut, expected_available, expected_count, limit=4):
    for _ in range(limit):
        if (_read(dut.available_spaces, "available_spaces") == expected_available
                and _read(dut.count_car, "count_car") == expected_count):
            return
        await _tick(dut, "clk")
    _expect(
        "parking_counts",
        [_read(dut.available_spaces, "available_spaces"), _read(dut.count_car, "count_car")],
        [expected_available, expected_count],
        {},
    )


async def _sensor_pulse(dut, entry):
    dut.vehicle_entry_sensor.value = int(entry)
    dut.vehicle_exit_sensor.value = int(not entry)
    await _tick(dut, "clk")
    dut.vehicle_entry_sensor.value = 0
    dut.vehicle_exit_sensor.value = 0


@cocotb.test()
async def public_dev_parking(dut):
    total = int(PARAMETERS["TOTAL_SPACES"])
    dut.clk.value = 0
    dut.reset.value = 1
    dut.vehicle_entry_sensor.value = 0
    dut.vehicle_exit_sensor.value = 0
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await _settle()
    dut.reset.value = 0
    await _tick(dut, "clk")
    await _wait_counts(dut, total, 0)
    _expect("available_led", _read(dut.led_status, "led_status"), 1, {})

    for count in range(1, total + 1):
        await _sensor_pulse(dut, True)
        await _wait_counts(dut, total - count, count)
    _expect("full_led", _read(dut.led_status, "led_status"), 0, {})

    # An entry at capacity is denied.
    await _sensor_pulse(dut, True)
    await _wait_counts(dut, 0, total)
    await _sensor_pulse(dut, False)
    await _wait_counts(dut, 1, total - 1)
    _expect("available_led_after_exit", _read(dut.led_status, "led_status"), 1, {})

    # The public prompt does not define the active-high/active-low seven-segment
    # glyph table, so generated feedback intentionally does not guess it.  It
    # still rejects unresolved display outputs.
    for name in (
        "seven_seg_display_available_tens",
        "seven_seg_display_available_units",
        "seven_seg_display_count_tens",
        "seven_seg_display_count_units",
    ):
        _read(getattr(dut, name), name)
'''


_HAMMING_HELPERS = r'''
def _hamming_encode(data, data_width, parity_bits):
    encoded_width = data_width + parity_bits + 1
    encoded = 0
    data_index = 0
    for position in range(1, encoded_width):
        if position & (position - 1):
            encoded |= ((data >> data_index) & 1) << position
            data_index += 1
    for parity_index in range(parity_bits):
        parity = 0
        for position in range(1, encoded_width):
            if position & (1 << parity_index):
                parity ^= (encoded >> position) & 1
        encoded |= parity << (1 << parity_index)
    return encoded


def _hamming_data(encoded, data_width, parity_bits):
    encoded_width = data_width + parity_bits + 1
    data = 0
    data_index = 0
    for position in range(1, encoded_width):
        if position & (position - 1):
            data |= ((encoded >> position) & 1) << data_index
            data_index += 1
    return data
'''


_HAMMING_TX_BODY = _HAMMING_HELPERS + r'''
@cocotb.test()
async def public_dev_hamming_tx(dut):
    data_width = int(PARAMETERS["DATA_WIDTH"])
    parity_bits = int(PARAMETERS["PARITY_BIT"])
    mask = (1 << data_width) - 1
    values = [0, 1, mask]
    values += [RNG.randrange(mask + 1) for _ in range(20)]
    for data in values:
        dut.data_in.value = data
        await _settle()
        _expect(
            "data_out",
            _read(dut.data_out, "data_out"),
            _hamming_encode(data, data_width, parity_bits),
            {"data_in": data},
        )
'''


_HAMMING_RX_BODY = _HAMMING_HELPERS + r'''
@cocotb.test()
async def public_dev_hamming_rx(dut):
    data_width = int(PARAMETERS["DATA_WIDTH"])
    parity_bits = int(PARAMETERS["PARITY_BIT"])
    encoded_width = data_width + parity_bits + 1
    mask = (1 << data_width) - 1
    values = [0, 1, mask]
    values += [RNG.randrange(mask + 1) for _ in range(10)]
    for data in values:
        encoded = _hamming_encode(data, data_width, parity_bits)
        for error_position in [None] + list(range(encoded_width)):
            received = encoded if error_position is None else encoded ^ (1 << error_position)
            dut.data_in.value = received
            await _settle()
            _expect(
                "corrected_data_out",
                _read(dut.data_out, "data_out"),
                data,
                {"encoded": encoded, "single_error_position": error_position},
            )
'''


_SQRT_BODY = r'''
async def _run_sqrt(dut, value, width):
    previous_root = _read(dut.final_root, "final_root")
    expected = math.isqrt(value)
    expected_cycles = expected + 2
    vector = {"num": value, "latency_cycles": expected_cycles}

    # Public latency convention: the edge accepting start is cycle 1.  Each
    # successful odd-number subtraction consumes one cycle and the final
    # compare/output assignment consumes the last cycle.
    dut.num.value = value
    dut.start.value = 1
    await _tick(dut, "clk")
    _expect("done_not_early", _read(dut.done, "done"), 0, {**vector, "cycle": 1})
    _expect("root_not_early", _read(dut.final_root, "final_root"), previous_root, {**vector, "cycle": 1})
    dut.start.value = 0

    for cycle in range(2, expected_cycles):
        await _tick(dut, "clk")
        _expect("done_not_early", _read(dut.done, "done"), 0, {**vector, "cycle": cycle})
        _expect("root_not_early", _read(dut.final_root, "final_root"), previous_root, {**vector, "cycle": cycle})

    await _tick(dut, "clk")
    _expect("done_exact_latency", _read(dut.done, "done"), 1, vector)
    _expect("final_root", _read(dut.final_root, "final_root"), expected, vector)
    await _tick(dut, "clk")
    _expect("done_one_cycle", _read(dut.done, "done"), 0, vector)
    _expect("root_holds", _read(dut.final_root, "final_root"), expected, vector)


@cocotb.test()
async def public_dev_square_root(dut):
    width = int(PARAMETERS["WIDTH"])
    mask = (1 << width) - 1
    dut.clk.value = 0
    dut.rst.value = 1
    dut.start.value = 0
    dut.num.value = 0
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await _settle()
    _expect("done_async_reset", _read(dut.done, "done"), 0, {})
    _expect("root_async_reset", _read(dut.final_root, "final_root"), 0, {})
    dut.rst.value = 0
    await _tick(dut, "clk")
    _expect("done_after_reset", _read(dut.done, "done"), 0, {})
    _expect("root_after_reset", _read(dut.final_root, "final_root"), 0, {})
    values = [0, 1, 2, 3, 4, 8, 9, mask]
    values += [RNG.randrange(mask + 1) for _ in range(4)]
    for value in dict.fromkeys(values):
        await _run_sqrt(dut, value, width)
'''


_FILO_BODY = r'''
@cocotb.test()
async def public_dev_filo(dut):
    width = int(PARAMETERS["DATA_WIDTH"])
    depth = int(PARAMETERS["FILO_DEPTH"])
    mask = (1 << width) - 1
    dut.clk.value = 0
    dut.reset.value = 1
    dut.push.value = 0
    dut.pop.value = 0
    dut.data_in.value = 0
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await _settle()
    _expect("empty_async_reset", _read(dut.empty, "empty"), 1, {})
    _expect("full_async_reset", _read(dut.full, "full"), 0, {})
    dut.reset.value = 0
    await _tick(dut, "clk")
    _expect("empty_after_reset", _read(dut.empty, "empty"), 1, {})
    _expect("full_after_reset", _read(dut.full, "full"), 0, {})

    # Fill partially, then pulse reset entirely between rising edges.  A
    # synchronous-only reset cannot observe this public asynchronous reset.
    for value in (0x5 & mask, 0xA & mask)[:max(1, min(2, depth - 1))]:
        dut.data_in.value = value
        dut.push.value = 1
        await _tick(dut, "clk")
    await Timer(2, unit="ns")
    dut.reset.value = 1
    await _settle()
    _expect("empty_midrun_async_reset", _read(dut.empty, "empty"), 1, {})
    _expect("full_midrun_async_reset", _read(dut.full, "full"), 0, {})
    dut.reset.value = 0
    dut.push.value = 0
    await _settle()
    _expect("empty_after_async_release", _read(dut.empty, "empty"), 1, {})

    values = [((index * 11) + 5) & mask for index in range(depth)]
    for value in values:
        dut.data_in.value = value
        dut.push.value = 1
        await _tick(dut, "clk")
    dut.push.value = 0
    _expect("full_at_depth", _read(dut.full, "full"), 1, {"depth": depth})

    # Overflow must be ignored: the extra word cannot replace the stack top.
    overflow = ((values[-1] ^ mask) + 1) & mask
    dut.data_in.value = overflow
    dut.push.value = 1
    await _tick(dut, "clk")
    dut.push.value = 0
    _expect("full_after_overflow", _read(dut.full, "full"), 1, {"data_in": overflow})

    for index, expected in enumerate(reversed(values)):
        dut.pop.value = 1
        await _tick(dut, "clk")
        _expect("lifo_pop", _read(dut.data_out, "data_out"), expected, {"pop_index": index})
    dut.pop.value = 0
    _expect("empty_after_drain", _read(dut.empty, "empty"), 1, {})

    # Underflow is likewise a no-op, including for the registered output.
    drained_output = _read(dut.data_out, "data_out")
    dut.pop.value = 1
    await _tick(dut, "clk")
    dut.pop.value = 0
    _expect("empty_after_underflow", _read(dut.empty, "empty"), 1, {})
    _expect("data_out_holds_underflow", _read(dut.data_out, "data_out"), drained_output, {})

    feedthrough = RNG.randrange(mask + 1)
    dut.data_in.value = feedthrough
    dut.push.value = 1
    dut.pop.value = 1
    await _tick(dut, "clk")
    dut.push.value = 0
    dut.pop.value = 0
    _expect("empty_feedthrough", _read(dut.empty, "empty"), 1, {"data_in": feedthrough})
    _expect("data_out_feedthrough", _read(dut.data_out, "data_out"), feedthrough, {"data_in": feedthrough})
'''


_DICE_BODY = r'''
def _unpack_dice(flat, number, bit_width):
    mask = (1 << bit_width) - 1
    # Public mapping: Dice 1 occupies the most-significant segment.
    return [
        (flat >> ((number - 1 - index) * bit_width)) & mask
        for index in range(number)
    ]


async def _roll_and_latch(dut, hold_cycles, number, bit_width, dice_max):
    held = _read(dut.dice_values, "dice_values")
    dut.button.value = 1
    for cycle in range(hold_cycles):
        await _tick(dut, "clk")
        _expect(
            "output_holds_while_rolling",
            _read(dut.dice_values, "dice_values"),
            held,
            {"button_cycles": hold_cycles, "cycle": cycle + 1},
        )
    dut.button.value = 0
    await _tick(dut, "clk")
    latched = _read(dut.dice_values, "dice_values")
    values = tuple(_unpack_dice(latched, number, bit_width))
    assert all(1 <= value <= dice_max for value in values), (
        f"CVDP_PUBLIC_DEV_MISMATCH label=dice_range input={{'button_cycles': {hold_cycles}}} "
        f"expected=1..{dice_max} actual={values} parameters={PARAMETERS} seed={PUBLIC_SEED}"
    )
    await _tick(dut, "clk")
    _expect("output_holds_idle", _read(dut.dice_values, "dice_values"), latched, {})
    return values


@cocotb.test()
async def public_dev_dice(dut):
    dice_max = int(PARAMETERS["DICE_MAX"])
    number = int(PARAMETERS["NUM_DICE"])
    bit_width = math.ceil(math.log2(dice_max)) + 1
    dut.clk.value = 0
    dut.reset.value = 0
    dut.button.value = 0
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await _settle()
    await _tick(dut, "clk")
    dut.reset.value = 1
    await _tick(dut, "clk")

    durations = [3 + (index % 4) for index in range(12)]
    observations = [
        await _roll_and_latch(dut, duration, number, bit_width, dice_max)
        for duration in durations
    ]
    for index in range(number):
        history = {values[index] for values in observations}
        assert len(history) >= 2, (
            f"CVDP_PUBLIC_DEV_MISMATCH label=dice_independent_variation "
            f"input={{'dice_index': {index}, 'rolls': {len(observations)}}} "
            f"expected=at_least_2_values actual={sorted(history)} "
            f"parameters={PARAMETERS} seed={PUBLIC_SEED}"
        )
    histories = {
        tuple(values[index] for values in observations)
        for index in range(number)
    }
    assert len(histories) == number, (
        f"CVDP_PUBLIC_DEV_MISMATCH label=dice_unique_seed_histories "
        f"expected={number} actual={len(histories)} parameters={PARAMETERS} seed={PUBLIC_SEED}"
    )

    # Pulse active-low reset entirely between rising edges twice.  Identical
    # post-reset stimuli must be reproducible because reset reinitializes the
    # public per-die seeds, without requiring one particular LFSR phase/value.
    reset_rolls = []
    for reset_index in range(2):
        await Timer(2, unit="ns")
        dut.reset.value = 0
        await _settle()
        dut.reset.value = 1
        await _settle()
        reset_rolls.append(
            await _roll_and_latch(
                dut, durations[0], number, bit_width, dice_max
            )
        )
    _expect(
        "dice_async_reset_reproducible",
        reset_rolls[1],
        reset_rolls[0],
        {"button_cycles": durations[0]},
    )
'''


_DIVISION_BODY = r'''
async def _divide(dut, dividend, divisor, width):
    is_power_of_two = width > 0 and (width & (width - 1)) == 0
    expected_cycles = width if is_power_of_two else width + 1
    vector = {
        "dividend": dividend,
        "divisor": divisor,
        "latency_cycles": expected_cycles,
    }

    # Public latency convention: the edge accepting start is cycle 1 and the
    # valid edge is included in WIDTH (power-of-two) or WIDTH+1 total cycles.
    dut.dividend.value = dividend
    dut.divisor.value = divisor
    dut.start.value = 1
    await _tick(dut, "clk")
    _expect("valid_not_early", _read(dut.valid, "valid"), 0, {**vector, "cycle": 1})
    dut.start.value = 0
    for cycle in range(2, expected_cycles):
        await _tick(dut, "clk")
        _expect("valid_not_early", _read(dut.valid, "valid"), 0, {**vector, "cycle": cycle})

    await _tick(dut, "clk")
    _expect("valid_exact_latency", _read(dut.valid, "valid"), 1, vector)
    _expect("quotient", _read(dut.quotient, "quotient"), dividend // divisor, vector)
    _expect("remainder", _read(dut.remainder, "remainder"), dividend % divisor, vector)
    await _tick(dut, "clk")
    _expect("valid_one_cycle", _read(dut.valid, "valid"), 0, vector)


@cocotb.test()
async def public_dev_restoring_division(dut):
    width = int(PARAMETERS["WIDTH"])
    maximum = (1 << width) - 1
    dut.clk.value = 0
    dut.rst.value = 0
    dut.start.value = 0
    dut.dividend.value = 0
    dut.divisor.value = 0
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await _settle()
    _expect("valid_async_reset", _read(dut.valid, "valid"), 0, {})
    _expect("quotient_async_reset", _read(dut.quotient, "quotient"), 0, {})
    _expect("remainder_async_reset", _read(dut.remainder, "remainder"), 0, {})
    dut.rst.value = 1
    await _tick(dut, "clk")
    _expect("valid_after_reset", _read(dut.valid, "valid"), 0, {})
    cases = [(maximum, 1), (maximum, maximum), (maximum, max(1, maximum // 2))]
    for _ in range(7):
        divisor = RNG.randint(1, maximum)
        dividend = RNG.randint(divisor, maximum)
        cases.append((dividend, divisor))
    for dividend, divisor in cases:
        await _divide(dut, dividend, divisor, width)
'''


_AXI_BODY = r'''
async def _axi_write(dut, address, data, expected_resp=0):
    dut.axi_awaddr.value = address
    dut.axi_wdata.value = data
    dut.axi_wstrb.value = (1 << (int(PARAMETERS["C_S_AXI_DATA_WIDTH"]) // 8)) - 1
    dut.axi_awvalid.value = 1
    dut.axi_wvalid.value = 1
    address_done = False
    data_done = False
    for _ in range(12):
        # READY may legally drop immediately after the accepting edge.  Sample
        # VALID && READY before the edge, then retire the channel after it.
        await _settle()
        address_fire = (
            not address_done
            and _read(dut.axi_awvalid, "axi_awvalid")
            and _read(dut.axi_awready, "axi_awready")
        )
        data_fire = (
            not data_done
            and _read(dut.axi_wvalid, "axi_wvalid")
            and _read(dut.axi_wready, "axi_wready")
        )
        await _tick(dut, "axi_aclk")
        if address_fire:
            address_done = True
            dut.axi_awvalid.value = 0
        if data_fire:
            data_done = True
            dut.axi_wvalid.value = 0
        if address_done and data_done:
            break
    assert address_done and data_done, (
        f"CVDP_PUBLIC_DEV_MISMATCH label=axi_write_handshake input={{'address': {address}, 'data': {data}}}"
    )
    dut.axi_bready.value = 1
    await _settle()
    for _ in range(12):
        if _read(dut.axi_bvalid, "axi_bvalid"):
            _expect("axi_bresp", _read(dut.axi_bresp, "axi_bresp"), expected_resp, {"address": address})
            await _tick(dut, "axi_aclk")
            dut.axi_bready.value = 0
            return
        await _tick(dut, "axi_aclk")
    raise AssertionError(f"CVDP_PUBLIC_DEV_MISMATCH label=axi_bvalid_timeout input={{'address': {address}}}")


async def _axi_read(dut, address, expected_resp=0):
    dut.axi_araddr.value = address
    dut.axi_arvalid.value = 1
    for _ in range(12):
        await _settle()
        address_fire = (
            _read(dut.axi_arvalid, "axi_arvalid")
            and _read(dut.axi_arready, "axi_arready")
        )
        await _tick(dut, "axi_aclk")
        if address_fire:
            dut.axi_arvalid.value = 0
            break
    else:
        raise AssertionError(f"CVDP_PUBLIC_DEV_MISMATCH label=axi_arready_timeout input={{'address': {address}}}")
    dut.axi_rready.value = 1
    await _settle()
    for _ in range(12):
        if _read(dut.axi_rvalid, "axi_rvalid"):
            _expect("axi_rresp", _read(dut.axi_rresp, "axi_rresp"), expected_resp, {"address": address})
            value = _read(dut.axi_rdata, "axi_rdata")
            await _tick(dut, "axi_aclk")
            dut.axi_rready.value = 0
            return value
        await _tick(dut, "axi_aclk")
    raise AssertionError(f"CVDP_PUBLIC_DEV_MISMATCH label=axi_rvalid_timeout input={{'address': {address}}}")


@cocotb.test()
async def public_dev_axi_counter(dut):
    dut.axi_aclk.value = 0
    dut.axi_aresetn.value = 0
    for name in ("axi_awaddr", "axi_awvalid", "axi_wdata", "axi_wstrb", "axi_wvalid",
                 "axi_bready", "axi_araddr", "axi_arvalid", "axi_rready"):
        getattr(dut, name).value = 0
    cocotb.start_soon(Clock(dut.axi_aclk, 10, unit="ns").start())
    await _settle()
    dut.axi_aresetn.value = 1
    await _tick(dut, "axi_aclk")
    _expect("done_after_reset", _read(dut.axi_ap_done, "axi_ap_done"), 0, {})
    _expect("irq_after_reset", _read(dut.irq, "irq"), 0, {})

    await _axi_write(dut, 0x20, 9)
    _expect("readback_countdown", await _axi_read(dut, 0x20), 9, {})
    await _axi_write(dut, 0x28, 4)
    await _axi_write(dut, 0x24, 1)
    await _axi_write(dut, 0x00, 1)

    saw_irq = False
    saw_done = False
    for _ in range(20):
        await _tick(dut, "axi_aclk")
        saw_irq |= bool(_read(dut.irq, "irq"))
        saw_done |= bool(_read(dut.axi_ap_done, "axi_ap_done"))
        if saw_done:
            break
    _expect("irq_threshold_seen", int(saw_irq), 1, {"countdown": 9, "threshold": 4})
    _expect("countdown_done", int(saw_done), 1, {"countdown": 9})
    _expect("countdown_zero", await _axi_read(dut, 0x20), 0, {})
    await _axi_write(dut, 0xFC, 0x12345678, expected_resp=2)
    await _axi_read(dut, 0xFC, expected_resp=2)
'''


def _p(direction: str, kind: str, name: str) -> Port:
    return direction, kind, name


_TEMPLATES: dict[str, _Template] = {
    "cvdp_copilot_word_reducer_0008": _Template(
        "Bit_Difference_Counter",
        ("Bit_Difference_Counter", "BIT_WIDTH", "bit_difference_count"),
        ({"BIT_WIDTH": 1}, {"BIT_WIDTH": 3}, {"BIT_WIDTH": 4}, {"BIT_WIDTH": 9}),
        (
            _p("input", "logic [BIT_WIDTH-1:0]", "input_A"),
            _p("input", "logic [BIT_WIDTH-1:0]", "input_B"),
            _p("output", "logic [$clog2(BIT_WIDTH + 1)-1:0]", "bit_difference_count"),
        ),
        _WORD_BODY,
    ),
    "cvdp_copilot_nbit_swizzling_0001": _Template(
        "nbit_swizzling",
        ("nbit_swizzling", "DATA_WIDTH", "data_out"),
        ({"DATA_WIDTH": 16}, {"DATA_WIDTH": 24}, {"DATA_WIDTH": 64}),
        (
            _p("input", "logic [DATA_WIDTH-1:0]", "data_in"),
            _p("input", "logic [1:0]", "sel"),
            _p("output", "logic [DATA_WIDTH-1:0]", "data_out"),
        ),
        _SWIZZLE_BODY,
    ),
    "cvdp_copilot_sync_lifo_0001": _Template(
        "sync_lifo",
        ("sync_lifo", "ADDR_WIDTH", "write_en", "read_en"),
        ({"DATA_WIDTH": 5, "ADDR_WIDTH": 2}, {"DATA_WIDTH": 8, "ADDR_WIDTH": 3}),
        (
            _p("input", "logic", "clock"), _p("input", "logic", "reset"),
            _p("input", "logic", "write_en"), _p("input", "logic", "read_en"),
            _p("input", "logic [DATA_WIDTH-1:0]", "data_in"),
            _p("output", "logic", "empty"), _p("output", "logic", "full"),
            _p("output", "logic [DATA_WIDTH-1:0]", "data_out"),
        ),
        _SYNC_LIFO_BODY,
    ),
    "cvdp_copilot_gf_multiplier_0021": _Template(
        "gf_mac",
        ("gf_mac", "error_flag", "valid_result", "0x11B"),
        ({"WIDTH": 8}, {"WIDTH": 16}, {"WIDTH": 32}, {"WIDTH": 14}),
        (
            _p("input", "logic [WIDTH-1:0]", "a"), _p("input", "logic [WIDTH-1:0]", "b"),
            _p("output", "logic [7:0]", "result"), _p("output", "logic", "error_flag"),
            _p("output", "logic", "valid_result"),
        ),
        _GF_BODY,
    ),
    "cvdp_copilot_car_parking_management_0001": _Template(
        "car_parking_system",
        ("car_parking_system", "TOTAL_SPACES", "vehicle_entry_sensor", "available_spaces"),
        ({"TOTAL_SPACES": 5}, {"TOTAL_SPACES": 12}),
        (
            _p("input", "logic", "clk"), _p("input", "logic", "reset"),
            _p("input", "logic", "vehicle_entry_sensor"), _p("input", "logic", "vehicle_exit_sensor"),
            _p("output", "logic [$clog2(TOTAL_SPACES)-1:0]", "available_spaces"),
            _p("output", "logic [$clog2(TOTAL_SPACES)-1:0]", "count_car"),
            _p("output", "logic", "led_status"),
            _p("output", "logic [6:0]", "seven_seg_display_available_tens"),
            _p("output", "logic [6:0]", "seven_seg_display_available_units"),
            _p("output", "logic [6:0]", "seven_seg_display_count_tens"),
            _p("output", "logic [6:0]", "seven_seg_display_count_units"),
        ),
        _PARKING_BODY,
    ),
    "cvdp_copilot_hamming_code_tx_and_rx_0011": _Template(
        "hamming_rx",
        ("hamming_rx", "PARITY_BIT", "corrected data", "data_out"),
        ({"DATA_WIDTH": 4, "PARITY_BIT": 3}, {"DATA_WIDTH": 8, "PARITY_BIT": 4}),
        (
            _p("input", "logic [(DATA_WIDTH + PARITY_BIT + 1)-1:0]", "data_in"),
            _p("output", "logic [DATA_WIDTH-1:0]", "data_out"),
        ),
        _HAMMING_RX_BODY,
    ),
    "cvdp_copilot_square_root_0003": _Template(
        "square_root_seq",
        ("square_root_seq", "subtraction", "final_root", "done"),
        ({"WIDTH": 4}, {"WIDTH": 8}, {"WIDTH": 16}),
        (
            _p("input", "logic [WIDTH-1:0]", "num"), _p("input", "logic", "clk"),
            _p("input", "logic", "rst"), _p("input", "logic", "start"),
            _p("output", "logic [WIDTH/2-1:0]", "final_root"), _p("output", "logic", "done"),
        ),
        _SQRT_BODY,
    ),
    "cvdp_copilot_filo_0005": _Template(
        "FILO_RTL",
        ("FILO_RTL", "FILO_DEPTH", "Feedthrough", "data_out"),
        ({"DATA_WIDTH": 5, "FILO_DEPTH": 3}, {"DATA_WIDTH": 8, "FILO_DEPTH": 8}, {"DATA_WIDTH": 8, "FILO_DEPTH": 16}),
        (
            _p("input", "logic", "clk"), _p("input", "logic", "reset"),
            _p("input", "logic", "push"), _p("input", "logic", "pop"),
            _p("input", "logic [DATA_WIDTH-1:0]", "data_in"),
            _p("output", "logic [DATA_WIDTH-1:0]", "data_out"),
            _p("output", "logic", "full"), _p("output", "logic", "empty"),
        ),
        _FILO_BODY,
    ),
    "cvdp_copilot_digital_dice_roller_0004": _Template(
        "digital_dice_roller",
        ("digital_dice_roller", "DICE_MAX", "NUM_DICE", "dice_values"),
        ({"DICE_MAX": 6, "NUM_DICE": 2}, {"DICE_MAX": 8, "NUM_DICE": 3}),
        (
            _p("input", "logic", "clk"), _p("input", "logic", "reset"),
            _p("input", "logic", "button"),
            _p("output", "logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0]", "dice_values"),
        ),
        _DICE_BODY,
    ),
    "cvdp_copilot_restoring_division_0001": _Template(
        "restoring_division",
        ("restoring_division", "dividend", "divisor", "remainder"),
        ({"WIDTH": 3}, {"WIDTH": 6}, {"WIDTH": 8}),
        (
            _p("input", "logic", "clk"), _p("input", "logic", "rst"),
            _p("input", "logic", "start"), _p("input", "logic [WIDTH-1:0]", "dividend"),
            _p("input", "logic [WIDTH-1:0]", "divisor"),
            _p("output", "logic [WIDTH-1:0]", "quotient"),
            _p("output", "logic [WIDTH-1:0]", "remainder"), _p("output", "logic", "valid"),
        ),
        _DIVISION_BODY,
    ),
    "cvdp_copilot_hamming_code_tx_and_rx_0009": _Template(
        "hamming_tx",
        ("hamming_tx", "PARITY_BIT", "data_out", "redundant bit"),
        ({"DATA_WIDTH": 4, "PARITY_BIT": 3}, {"DATA_WIDTH": 8, "PARITY_BIT": 4}),
        (
            _p("input", "logic [DATA_WIDTH-1:0]", "data_in"),
            _p("output", "logic [(DATA_WIDTH + PARITY_BIT + 1)-1:0]", "data_out"),
        ),
        _HAMMING_TX_BODY,
    ),
    "cvdp_copilot_axil_precision_counter_0001": _Template(
        "precision_counter_axi",
        ("precision_counter_axi", "AXI4-Lite", "slv_reg_irq_thresh", "axi_ap_done"),
        ({"C_S_AXI_DATA_WIDTH": 32, "C_S_AXI_ADDR_WIDTH": 8},),
        (
            _p("input", "logic", "axi_aclk"), _p("input", "logic", "axi_aresetn"),
            _p("input", "logic [C_S_AXI_ADDR_WIDTH-1:0]", "axi_awaddr"),
            _p("input", "logic", "axi_awvalid"),
            _p("input", "logic [C_S_AXI_DATA_WIDTH-1:0]", "axi_wdata"),
            _p("input", "logic [(C_S_AXI_DATA_WIDTH/8)-1:0]", "axi_wstrb"),
            _p("input", "logic", "axi_wvalid"), _p("input", "logic", "axi_bready"),
            _p("input", "logic [C_S_AXI_ADDR_WIDTH-1:0]", "axi_araddr"),
            _p("input", "logic", "axi_arvalid"), _p("input", "logic", "axi_rready"),
            _p("output", "logic", "axi_awready"), _p("output", "logic", "axi_wready"),
            _p("output", "logic [1:0]", "axi_bresp"), _p("output", "logic", "axi_bvalid"),
            _p("output", "logic", "axi_arready"),
            _p("output", "logic [C_S_AXI_DATA_WIDTH-1:0]", "axi_rdata"),
            _p("output", "logic [1:0]", "axi_rresp"), _p("output", "logic", "axi_rvalid"),
            _p("output", "logic", "axi_ap_done"), _p("output", "logic", "irq"),
        ),
        _AXI_BODY,
    ),
}

# Freeze the catalog and its parameter maps: validation treats these values as
# trusted code, never as suite-provided evidence.
_TEMPLATES = MappingProxyType({
    problem_id: _Template(
        template.design_name,
        template.required_prompt_anchors,
        tuple(_FrozenDict(dict(row)) for row in template.parameters),
        template.ports,
        template.body,
    )
    for problem_id, template in _TEMPLATES.items()
})


SUPPORTED_CVDP12_PROBLEMS = tuple(_TEMPLATES)


_AUDITED_PUBLIC_INPUT_SHA256 = {
    "cvdp_copilot_word_reducer_0008": "f8547899d8d3dbec6da3439505c7261752ff9970b39fbd4a364e3fbd24fd1291",
    "cvdp_copilot_nbit_swizzling_0001": "6688e0f02825156c8ede36c8c54586edf5af4022b91a61b2093b53fda003a13e",
    "cvdp_copilot_sync_lifo_0001": "21e676d6055ac9fda453fffe6f673a4cb4c06eb3ea9359de48e324203664e682",
    "cvdp_copilot_gf_multiplier_0021": "de8b45c034b899096b388a832cc87b8d3649b787580c9fd97c7efb8c9de9e482",
    "cvdp_copilot_car_parking_management_0001": "688642d1575f7bf9e12400e3c930e9ef71d84e29d17aee0dcefe08a1c1a1009a",
    "cvdp_copilot_hamming_code_tx_and_rx_0011": "b99fcea4e51927c96eecd2cf4d30967ba52e06538ea6c2521c67605a28eb9650",
    "cvdp_copilot_square_root_0003": "650e8add05c0e636f8560882d52de14de97937c1ff5fc1a85842403b19d582f8",
    "cvdp_copilot_filo_0005": "127e0c4b7ad0db6d9daf1efbdb8f09d45aaa220ee29fbf66c75cb97bf19ab505",
    "cvdp_copilot_digital_dice_roller_0004": "036bc8d765b31b226a23b71d71f2572610ab578d7bbd41f39da8749d655d8f01",
    "cvdp_copilot_restoring_division_0001": "c856d2fee9f543614e6de0febf6882eb9017806e18a504b5607b38a7278a009f",
    "cvdp_copilot_hamming_code_tx_and_rx_0009": "c4d5478152fa1fed9bb778994d8126f187ffabe6a969227e1cbea793db29ca87",
    "cvdp_copilot_axil_precision_counter_0001": "829e4ab8c9145e4836f9e4e37f1fe51750f50b345a0746f3086682a87dbde7e6",
}



_ORACLE_SCOPE = {
    problem_id: "exact public functional oracle"
    for problem_id in SUPPORTED_CVDP12_PROBLEMS
}
_ORACLE_SCOPE["cvdp_copilot_digital_dice_roller_0004"] = (
    "public property oracle: exact LFSR sequence omitted because the prompt's "
    "BIT_WIDTH/example conventions conflict; checks rolling/idle latching, range, "
    "per-die variation, unique seeded histories, and asynchronous-reset replay"
)
_ORACLE_SCOPE["cvdp_copilot_car_parking_management_0001"] = (
    "exact count/capacity/status oracle; seven-segment outputs checked only for "
    "resolved values because the public prompt does not define glyph polarity"
)

def _canonical_sha(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unsupported_digest(prob_id: str, seed: int, reason: str | None) -> str:
    return _canonical_sha({
        "version": CVDP_PUBLIC_DEV_VERSION,
        "source": CVDP_PUBLIC_DEV_SOURCE,
        "prob_id": prob_id,
        "seed": seed,
        "supported": False,
        "reason": reason,
    })


def _supported_digest(
    *,
    prob_id: str,
    design_name: str,
    prompt_text: str,
    seed: int,
    context_hashes: Mapping[str, str],
    compilation_context: Mapping[str, str],
    harness_files: Mapping[str, str],
    verilog_sources: tuple[str, ...],
    benchmark_ports: tuple[Port, ...],
) -> str:
    return _canonical_sha({
        "version": CVDP_PUBLIC_DEV_VERSION,
        "source": CVDP_PUBLIC_DEV_SOURCE,
        "prob_id": prob_id,
        "design_name": design_name,
        "seed": seed,
        "public_prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
        "public_input_context_sha256": dict(context_hashes),
        "public_prompt_text": prompt_text,
        "public_dev_oracle_scope": _ORACLE_SCOPE[prob_id],
        "public_prompt_text": prompt_text,
        "public_dev_oracle_scope": _ORACLE_SCOPE[prob_id],
        "compiled_input_context": dict(compilation_context),
        "harness_files": dict(harness_files),
        "verilog_sources": tuple(verilog_sources),
        "benchmark_ports": tuple(benchmark_ports),
    })


def _unsupported(prob_id: str, seed: int, reason: str) -> GeneratedDevSuite:
    digest = _unsupported_digest(prob_id, seed, reason)
    return GeneratedDevSuite(
        prob_id=prob_id,
        supported=False,
        reason=reason,
        public_info=None,
        harness_files={},
        verilog_sources=(),
        benchmark_ports=(),
        seed=seed,
        version=CVDP_PUBLIC_DEV_VERSION,
        sha256=digest,
    )


def _json_skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _json_skip_value(text: str, index: int) -> int:
    """Lexically skip a JSON value without decoding/materializing it."""
    index = _json_skip_space(text, index)
    if index >= len(text):
        raise ValueError("truncated JSON value")
    first = text[index]
    if first == '"':
        index += 1
        escaped = False
        while index < len(text):
            char = text[index]
            index += 1
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                return index
        raise ValueError("unterminated JSON string")
    if first in "[{":
        stack = ["]" if first == "[" else "}"]
        index += 1
        in_string = False
        escaped = False
        while index < len(text) and stack:
            char = text[index]
            index += 1
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "[":
                stack.append("]")
            elif char == "{":
                stack.append("}")
            elif char in "]}":
                if char != stack.pop():
                    raise ValueError("mismatched JSON delimiter")
        if stack:
            raise ValueError("unterminated JSON container")
        return index
    while index < len(text) and text[index] not in ",}":
        index += 1
    return index


def _select_json_object_fields(text: str, wanted: frozenset[str]) -> dict[str, object]:
    """Decode selected top-level fields; lexically skip every other value."""
    decoder = json.JSONDecoder()
    index = _json_skip_space(text, 0)
    if index >= len(text) or text[index] != "{":
        raise ValueError("CVDP JSONL row is not an object")
    index += 1
    selected: dict[str, object] = {}
    while True:
        index = _json_skip_space(text, index)
        if index < len(text) and text[index] == "}":
            index += 1
            break
        key, index = decoder.raw_decode(text, index)
        if not isinstance(key, str):
            raise ValueError("CVDP JSONL row has a non-string key")
        index = _json_skip_space(text, index)
        if index >= len(text) or text[index] != ":":
            raise ValueError("CVDP JSONL row is missing a colon")
        index = _json_skip_space(text, index + 1)
        if key in wanted:
            if key in selected:
                raise ValueError(f"duplicate selected CVDP field: {key}")
            selected[key], index = decoder.raw_decode(text, index)
        else:
            index = _json_skip_value(text, index)
        index = _json_skip_space(text, index)
        if index < len(text) and text[index] == ",":
            index += 1
            continue
        if index < len(text) and text[index] == "}":
            index += 1
            break
        raise ValueError("CVDP JSONL row has invalid object syntax")
    if _json_skip_space(text, index) != len(text):
        raise ValueError("CVDP JSONL row has trailing data")
    return selected


def load_cvdp_public_input(dataset_path: str | Path, prob_id: str) -> PublicCVDPInput:
    """Load only ``id`` and public ``input`` from a JSONL row.

    All other top-level values are skipped lexically rather than JSON-decoded
    into Python objects, and none are returned.  The selected row still exists
    transiently as raw JSON text; this loader is a decoding boundary, not a
    streaming secrecy primitive.  A two-pass selection avoids decoding public
    inputs for non-target rows.
    """
    path = Path(dataset_path)
    selected_line: str | None = None
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            identity = _select_json_object_fields(line, frozenset({"id"}))
            if identity.get("id") != prob_id:
                continue
            if selected_line is not None:
                raise ValueError(f"duplicate CVDP problem id: {prob_id}")
            selected_line = line
    if selected_line is None:
        raise KeyError(f"CVDP problem not found: {prob_id}")
    fields = _select_json_object_fields(selected_line, frozenset({"id", "input"}))
    if fields.get("id") != prob_id or not isinstance(fields.get("input"), dict):
        raise ValueError("CVDP public input row is malformed")
    input_object = fields["input"]
    assert isinstance(input_object, dict)
    if set(input_object) - {"prompt", "context"}:
        raise ValueError("CVDP input contains unaudited fields")
    prompt = input_object.get("prompt", "")
    context = input_object.get("context", {})
    if not isinstance(prompt, str) or not isinstance(context, dict):
        raise ValueError("CVDP public prompt/context has invalid types")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in context.items()):
        raise ValueError("CVDP public context must map paths to text")
    sanitized, error = _sanitize_context(context)
    if error:
        raise ValueError(error)
    assert sanitized is not None
    return PublicCVDPInput(prob_id=prob_id, prompt_text=prompt, input_context_files=sanitized)


def _sanitize_context(
    input_context_files: Mapping[str, str] | None,
) -> tuple[dict[str, str] | None, str | None]:
    sanitized: dict[str, str] = {}
    for raw_path, raw_content in (input_context_files or {}).items():
        path = PurePosixPath(str(raw_path))
        if path.is_absolute() or not path.parts or ".." in path.parts:
            return None, f"public input context path is unsafe: {raw_path!r}"
        if path.suffix.lower() not in {".v", ".sv", ".vh", ".svh"}:
            return None, f"public input context is not HDL: {raw_path!r}"
        content = str(raw_content)
        if len(content.encode("utf-8")) > 1_000_000:
            return None, f"public input context is too large: {raw_path!r}"
        sanitized[path.as_posix()] = content
    return dict(sorted(sanitized.items())), None


def _runner_source(
    *, design_name: str, parameter_matrix: tuple[dict[str, int], ...], seed: int
) -> str:
    matrix = json.dumps(parameter_matrix, sort_keys=True, separators=(",", ":"))
    header = textwrap.dedent(
        f'''\
        """Generated by {CVDP_PUBLIC_DEV_VERSION}; contains no hidden benchmark data."""
        import json
        import os
        from pathlib import Path

        try:
            from cocotb_tools.runner import get_runner
        except ImportError:  # cocotb < 2
            from cocotb.runner import get_runner


        TOPLEVEL = {design_name!r}
        PARAMETER_MATRIX = json.loads({matrix!r})
        PUBLIC_SEED = {seed}
        '''
    )
    tests = []
    for index, parameters in enumerate(parameter_matrix):
        parameter_literal = json.dumps(
            parameters, sort_keys=True, separators=(", ", ": ")
        )
        tests.append(textwrap.dedent(
            f'''\
            def test_generated_public_dev_{index:03d}(tmp_path):
                simulator = os.environ.get("SIM", "icarus")
                sources = [Path(item) for item in os.environ["VERILOG_SOURCES"].split()]
                build_dir = tmp_path / "sim_build"
                runner = get_runner(simulator)
                runner.build(
                    verilog_sources=sources,
                    hdl_toplevel=TOPLEVEL,
                    parameters={parameter_literal},
                    build_dir=build_dir,
                    always=True,
                    timescale=("1ns", "1ps"),
                )
                runner.test(
                    hdl_toplevel=TOPLEVEL,
                    test_module="test_generated",
                    build_dir=build_dir,
                    test_dir=tmp_path,
                    seed=PUBLIC_SEED,
                    extra_env={{
                        "CVDP_PUBLIC_DEV_PARAMETERS": json.dumps({parameter_literal}, sort_keys=True),
                        "CVDP_PUBLIC_DEV_SEED": str(PUBLIC_SEED),
                    }},
                )
            '''
        ))
    return header + "\n\n" + "\n\n".join(tests)


def _strip_target_module(content: str, design_name: str) -> str:
    """Remove exactly the audited target declaration, preserving public helpers."""
    declaration = re.compile(
        rf"(?im)^[ \t]*module[ \t]+{re.escape(design_name)}\b"
    )
    matches = list(declaration.finditer(content))
    if not matches:
        return content
    if len(matches) != 1:
        raise ValueError("public context has multiple target module declarations")
    ending = re.search(
        r"(?im)\bendmodule\b(?:[ \t]*:[ \t]*[A-Za-z_$][A-Za-z0-9_$]*)?"
        r"[^\S\r\n]*(?:\r?\n|$)",
        content[matches[0].end():],
    )
    if ending is None:
        raise ValueError("public target module declaration has no endmodule")
    end = matches[0].end() + ending.end()
    return content[:matches[0].start()] + content[end:]


def _compilation_context(
    context: Mapping[str, str], design_name: str
) -> dict[str, str]:
    compiled: dict[str, str] = {}
    for path, content in context.items():
        helper_content = _strip_target_module(content, design_name)
        if helper_content.strip():
            compiled[path] = helper_content
    return compiled


def _generated_harness(
    template: _Template,
    compilation_context: Mapping[str, str],
    seed: int,
) -> tuple[dict[str, str], tuple[str, ...]]:
    primary = f"/code/src/{template.design_name}.sv"
    context_sources = tuple(f"/code/{path}" for path in compilation_context)
    verilog_sources = (primary, *context_sources)
    env_text = (
        f"TOPLEVEL={template.design_name}\n"
        "MODULE=test_generated\n"
        f"VERILOG_SOURCES={' '.join(verilog_sources)}\n"
        "SIM=icarus\n"
    )
    harness_files = {
        "src/.env": env_text,
        "src/test_generated.py": textwrap.dedent(
            _COMMON_TEST_HEADER + "\n" + template.body
        ).lstrip(),
        "src/test_runner.py": _runner_source(
            design_name=template.design_name,
            parameter_matrix=template.parameters,
            seed=seed,
        ),
    }
    return harness_files, verilog_sources


def _generated_metadata(
    *,
    prob_id: str,
    prompt_text: str,
    seed: int,
    digest: str,
    context: Mapping[str, str],
    context_hashes: Mapping[str, str],
    compilation_context: Mapping[str, str],
    harness_files: Mapping[str, str],
    verilog_sources: tuple[str, ...],
    benchmark_ports: tuple[Port, ...],
) -> Mapping[str, object]:
    return _deep_freeze({
        "dataset": "cvdp",
        "public_dev_generated": True,
        "public_dev_source": CVDP_PUBLIC_DEV_SOURCE,
        "public_dev_version": CVDP_PUBLIC_DEV_VERSION,
        "public_dev_seed": seed,
        "public_dev_sha256": digest,
        "public_input_context_sha256": dict(context_hashes),
        "public_prompt_text": prompt_text,
        "public_dev_oracle_scope": _ORACLE_SCOPE[prob_id],
        "harness_files": dict(harness_files),
        "verilog_sources": verilog_sources,
        # Keep the original public file for provenance/prompt rendering, while
        # the evaluator stages only target-stripped helper compilation units.
        "public_input_context_files": dict(context),
        "input_context_files": dict(compilation_context),
        "benchmark_ports": benchmark_ports,
    })


def _render_public_prompt(prompt_text: str, context: Mapping[str, str]) -> str:
    blocks = [
        f"### Context file: {path}\n```systemverilog\n{content}\n```"
        for path, content in context.items()
        if content.strip()
    ]
    if not blocks:
        return prompt_text
    return (
        f"{prompt_text.rstrip()}\n\n"
        "The following RTL/context files are part of the task:\n\n"
        + "\n\n".join(blocks)
    )


def build_cvdp_public_dev_suite(
    prob_id: str,
    prompt_text: str,
    input_context_files: Mapping[str, str] | None = None,
    *,
    seed: int = DEFAULT_CVDP_DEV_SEED,
) -> GeneratedDevSuite:
    """Build deterministic public development tests, failing closed on doubt.

    ``prompt_text`` and ``input_context_files`` must be the public input fields,
    not fields recovered from the benchmark harness/output.  The function's
    signature intentionally makes the latter impossible to pass accidentally.
    """
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        return _unsupported(prob_id, 0, "seed must be a non-negative integer")
    template = _TEMPLATES.get(prob_id)
    if template is None:
        return _unsupported(prob_id, seed, "problem is outside the audited CVDP12 public-dev catalog")
    if not isinstance(prompt_text, str) or not prompt_text.strip():
        return _unsupported(prob_id, seed, "public prompt is empty")
    missing = [anchor for anchor in template.required_prompt_anchors if anchor.casefold() not in prompt_text.casefold()]
    if missing:
        return _unsupported(
            prob_id,
            seed,
            "public prompt does not match the audited specification; missing anchor(s): "
            + ", ".join(missing),
        )

    context, context_error = _sanitize_context(input_context_files)
    if context_error:
        return _unsupported(prob_id, seed, context_error)
    assert context is not None
    public_input_sha256 = _canonical_sha({"prompt": prompt_text, "context": context})
    if public_input_sha256 != _AUDITED_PUBLIC_INPUT_SHA256[prob_id]:
        return _unsupported(
            prob_id, seed,
            "public prompt/context fingerprint does not match the audited CVDP12 input",
        )

    try:
        compilation_context = _compilation_context(
            context, template.design_name
        )
    except ValueError as error:
        return _unsupported(prob_id, seed, str(error))
    harness_files, verilog_sources = _generated_harness(
        template, compilation_context, seed
    )

    context_hashes = {
        path: hashlib.sha256(content.encode("utf-8")).hexdigest()
        for path, content in context.items()
    }
    digest = _supported_digest(
        prob_id=prob_id, design_name=template.design_name,
        prompt_text=prompt_text, seed=seed, context_hashes=context_hashes,
        compilation_context=compilation_context, harness_files=harness_files,
        verilog_sources=verilog_sources, benchmark_ports=template.ports,
    )
    metadata = _generated_metadata(
        prob_id=prob_id,
        prompt_text=prompt_text,
        seed=seed,
        digest=digest,
        context=context,
        context_hashes=context_hashes,
        compilation_context=compilation_context,
        harness_files=harness_files,
        verilog_sources=verilog_sources,
        benchmark_ports=template.ports,
    )
    public_info = ProblemInfo(
        prob_id=prob_id,
        design_name=template.design_name,
        prompt_text=_render_public_prompt(prompt_text, context),
        ref_code="",
        testbench_path=Path(f"generated-public-dev/{prob_id}"),
        ref_path=None,
        metadata=metadata,
    )
    return GeneratedDevSuite(
        prob_id=prob_id,
        supported=True,
        reason=None,
        public_info=public_info,
        harness_files=harness_files,
        verilog_sources=verilog_sources,
        benchmark_ports=template.ports,
        seed=seed,
        version=CVDP_PUBLIC_DEV_VERSION,
        sha256=digest,
    )


__all__ = [
    "CVDP_PUBLIC_DEV_SOURCE",
    "PUBLIC_SPEC_SOURCE",
    "CVDP_PUBLIC_DEV_VERSION",
    "DEFAULT_CVDP_DEV_SEED",
    "GeneratedDevSuite",
    "PublicCVDPInput",
    "SUPPORTED_CVDP12_PROBLEMS",
    "build_cvdp_public_dev_suite",
    "load_cvdp_public_input",
]
