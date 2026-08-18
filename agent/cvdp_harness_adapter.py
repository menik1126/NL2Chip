from __future__ import annotations

import ast
import hashlib
from typing import Any


EDGE_TRIGGERS = {"ClockCycles", "Edge", "FallingEdge", "RisingEdge"}
CVDP_HARNESS_PROFILE_OFFICIAL = "official"
CVDP_HARNESS_PROFILE_RACE_SAFE = "race-safe-v1"
CVDP_HARNESS_PROFILES: dict[str, dict[str, bool]] = {
    CVDP_HARNESS_PROFILE_OFFICIAL: {
        "normalize_reset_helpers": False,
        "stabilize_cocotb_edges": False,
        "initialize_cocotb_inputs": False,
        "align_reset_release": False,
        "emit_progress_monitor": False,
    },
    CVDP_HARNESS_PROFILE_RACE_SAFE: {
        "normalize_reset_helpers": False,
        "stabilize_cocotb_edges": True,
        "initialize_cocotb_inputs": False,
        "align_reset_release": True,
        "emit_progress_monitor": False,
    },
}
# Ten-nanosecond early sampling catches one-cycle valid/ready pulses; the later
# samples distinguish a delayed response from a permanently stalled DUT.
PROGRESS_SNAPSHOT_DELAYS_NS = (
    10, 10, 10, 10, 10, 10, 10, 10, 10, 10,
    20, 30, 50, 100, 500, 9000,
)
PROGRESS_MONITOR_NAME = "__cvdp_progress_monitor"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def cvdp_harness_profile_defaults(profile: str) -> dict[str, bool]:
    """Return an independent option map for a versioned harness profile."""

    normalized = str(profile or "").strip().lower()
    if normalized not in CVDP_HARNESS_PROFILES:
        choices = ", ".join(sorted(CVDP_HARNESS_PROFILES))
        raise ValueError(
            f"Unknown CVDP harness profile {profile!r}; choose one of: {choices}"
        )
    return dict(CVDP_HARNESS_PROFILES[normalized])


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_edge_await(node: ast.Expr) -> bool:
    value = node.value
    return (
        isinstance(value, ast.Await)
        and isinstance(value.value, ast.Call)
        and _call_name(value.value.func) in EDGE_TRIGGERS
    )


def _settle_await(template: ast.AST) -> ast.Expr:
    node = ast.Expr(
        value=ast.Await(
            value=ast.Call(
                func=ast.Name(id="Timer", ctx=ast.Load()),
                args=[ast.Constant(value=1)],
                keywords=[
                    ast.keyword(arg="unit", value=ast.Constant(value="step"))
                ],
            )
        )
    )
    return ast.copy_location(node, template)


class _CocotbPhaseTransformer(ast.NodeTransformer):
    def __init__(self) -> None:
        self.insertions = 0

    def visit_Expr(self, node: ast.Expr) -> ast.AST | list[ast.AST]:
        node = self.generic_visit(node)
        if not isinstance(node, ast.Expr) or not _is_edge_await(node):
            return node
        self.insertions += 1
        return [node, _settle_await(node)]


def _is_cocotb_test_decorator(node: ast.AST) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "test"
        and isinstance(target.value, ast.Name)
        and target.value.id == "cocotb"
    )


def _is_clock_or_reset_port(name: str) -> bool:
    lower = str(name).lower()
    return bool(
        "clock" in lower
        or "clk" in lower
        or "reset" in lower
        or "rst" in lower
        or "areset" in lower
    )


def _is_clock_port(name: str) -> bool:
    tokens = str(name).lower().replace("-", "_").split("_")
    return any(token in {"clk", "clock", "aclk", "pclk"} for token in tokens)


def infer_cvdp_clock_ports(
    harness_files: dict[str, Any],
    input_ports: list[str] | tuple[str, ...] | set[str],
) -> list[str]:
    """Prefer clocks actually passed to cocotb ``Clock`` over name guesses."""

    inputs = {str(name) for name in input_ports}
    exercised: set[str] = set()
    for raw_path, raw_content in harness_files.items():
        if not str(raw_path).endswith(".py"):
            continue
        try:
            tree = ast.parse(str(raw_content))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and _call_name(node.func) == "Clock"
                and node.args
                and isinstance(node.args[0], ast.Attribute)
                and isinstance(node.args[0].value, ast.Name)
                and node.args[0].value.id == "dut"
            ):
                continue
            exercised.add(node.args[0].attr)
    concrete = sorted(exercised & inputs)
    if concrete:
        return concrete
    return sorted(name for name in inputs if _is_clock_port(name))


def _input_zero_assignment(
    *,
    dut_name: str,
    port_name: str,
    template: ast.AST,
) -> ast.Assign:
    node = ast.Assign(
        targets=[
            ast.Attribute(
                value=ast.Attribute(
                    value=ast.Name(id=dut_name, ctx=ast.Load()),
                    attr=port_name,
                    ctx=ast.Load(),
                ),
                attr="value",
                ctx=ast.Store(),
            )
        ],
        value=ast.Constant(value=0),
    )
    return ast.copy_location(node, template)


class _CocotbInputInitializer(ast.NodeTransformer):
    """Give public data/control inputs deterministic values at time zero."""

    def __init__(self, input_ports: set[str]) -> None:
        self.input_ports = {
            name
            for name in input_ports
            if name.isidentifier() and not _is_clock_or_reset_port(name)
        }
        self.functions: list[dict[str, Any]] = []

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        node = self.generic_visit(node)
        if not any(_is_cocotb_test_decorator(item) for item in node.decorator_list):
            return node
        arguments = [*node.args.posonlyargs, *node.args.args]
        dut_name = next((item.arg for item in arguments if item.arg == "dut"), None)
        if dut_name is None or not self.input_ports:
            return node

        insert_at = 1 if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ) else 0
        template = node.body[insert_at] if insert_at < len(node.body) else node
        assignments = [
            _input_zero_assignment(
                dut_name=dut_name,
                port_name=name,
                template=template,
            )
            for name in sorted(self.input_ports)
        ]
        node.body[insert_at:insert_at] = assignments
        self.functions.append({
            "function": node.name,
            "inputs": sorted(self.input_ports),
        })
        return node


def _assignment_value(node: ast.AST) -> ast.AST | None:
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        return node.value
    return None


def _assignment_targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, ast.AnnAssign):
        return [node.target]
    return []


def _targets_signal_value(node: ast.AST, signal_name: str) -> bool:
    return any(
        isinstance(target, ast.Attribute)
        and target.attr == "value"
        and isinstance(target.value, ast.Name)
        and target.value.id == signal_name
        for target in _assignment_targets(node)
    )


def _eval_active_expression(
    node: ast.AST,
    *,
    active_name: str,
    active: bool,
) -> int | None:
    if isinstance(node, ast.Constant) and node.value in {0, 1, False, True}:
        return int(bool(node.value))
    if isinstance(node, ast.Name) and node.id == active_name:
        return int(active)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = _eval_active_expression(
            node.operand, active_name=active_name, active=active
        )
        return None if value is None else int(not value)
    if isinstance(node, ast.IfExp):
        condition = _eval_active_expression(
            node.test, active_name=active_name, active=active
        )
        if condition is None:
            return None
        return _eval_active_expression(
            node.body if condition else node.orelse,
            active_name=active_name,
            active=active,
        )
    return None


def _active_truth_table(node: ast.AST, active_name: str) -> tuple[int | None, int | None]:
    return (
        _eval_active_expression(node, active_name=active_name, active=False),
        _eval_active_expression(node, active_name=active_name, active=True),
    )


def _module_stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _literal_bool(node: ast.AST | None) -> bool | None:
    if isinstance(node, ast.Constant) and node.value in {0, 1, False, True}:
        return bool(node.value)
    return None


def _reset_definition_record(
    path: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, Any] | None:
    positional = [*node.args.posonlyargs, *node.args.args]
    names = [arg.arg for arg in positional]
    kwonly = [arg.arg for arg in node.args.kwonlyargs]
    if "reset" not in node.name.lower() or not names:
        return None
    active_name = next(
        (name for name in [*names, *kwonly] if name.lower() == "active"),
        None,
    )
    if active_name is None:
        return None
    signal_name = names[0]
    assignments = sorted(
        (
            item
            for item in ast.walk(node)
            if _targets_signal_value(item, signal_name)
            and _assignment_value(item) is not None
        ),
        key=lambda item: (getattr(item, "lineno", 0), getattr(item, "col_offset", 0)),
    )
    if len(assignments) < 2:
        return None
    first_value = _assignment_value(assignments[0])
    last_value = _assignment_value(assignments[-1])
    assert first_value is not None and last_value is not None

    defaults: dict[str, bool | None] = {}
    default_offset = len(names) - len(node.args.defaults)
    for index, default in enumerate(node.args.defaults, start=default_offset):
        defaults[names[index]] = _literal_bool(default)
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        defaults[arg.arg] = _literal_bool(default)
    return {
        "path": path,
        "module": _module_stem(path),
        "function": node.name,
        "active_name": active_name,
        "active_index": [*names, *kwonly].index(active_name),
        "active_default": defaults.get(active_name),
        "assert_table": _active_truth_table(first_value, active_name),
        "deassert_table": _active_truth_table(last_value, active_name),
    }


def _call_reset_signal(call: ast.Call) -> str | None:
    argument = call.args[0] if call.args else next(
        (keyword.value for keyword in call.keywords if keyword.arg in {"reset", "reset_n", "reset_signal"}),
        None,
    )
    if (
        isinstance(argument, ast.Attribute)
        and isinstance(argument.value, ast.Name)
        and argument.value.id == "dut"
    ):
        return argument.attr
    return None


def _call_active_value(call: ast.Call, definition: dict[str, Any]) -> bool | None:
    active_name = definition["active_name"]
    keyword_value = next(
        (keyword.value for keyword in call.keywords if keyword.arg == active_name),
        None,
    )
    if keyword_value is not None:
        return _literal_bool(keyword_value)
    index = int(definition["active_index"])
    if index < len(call.args):
        return _literal_bool(call.args[index])
    return definition["active_default"]


def _reset_call_records(
    parsed_files: dict[str, ast.Module],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve reset-helper calls to their concrete assert/deassert levels."""

    definitions: list[dict[str, Any]] = []
    for path, tree in parsed_files.items():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                record = _reset_definition_record(path, node)
                if record is not None:
                    definitions.append(record)

    by_path_name = {
        (record["path"], record["function"]): record for record in definitions
    }
    by_module_name: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in definitions:
        by_module_name.setdefault(
            (record["module"], record["function"]), []
        ).append(record)

    calls: list[dict[str, Any]] = []
    for path, tree in parsed_files.items():
        module_aliases: dict[str, str] = {}
        function_aliases: dict[str, tuple[str, str]] = {}
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    module_aliases[alias.asname or alias.name] = (
                        alias.name.rsplit(".", 1)[-1]
                    )
            elif isinstance(statement, ast.ImportFrom) and statement.module:
                module = statement.module.rsplit(".", 1)[-1]
                for alias in statement.names:
                    function_aliases[alias.asname or alias.name] = (
                        module,
                        alias.name,
                    )

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            definition = None
            if isinstance(node.func, ast.Name):
                definition = by_path_name.get((path, node.func.id))
                if definition is None and node.func.id in function_aliases:
                    candidates = by_module_name.get(
                        function_aliases[node.func.id], []
                    )
                    definition = candidates[0] if len(candidates) == 1 else None
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
            ):
                module = module_aliases.get(
                    node.func.value.id, node.func.value.id
                )
                candidates = by_module_name.get((module, node.func.attr), [])
                definition = candidates[0] if len(candidates) == 1 else None
            if definition is None:
                continue
            reset_name = _call_reset_signal(node)
            active = _call_active_value(node, definition)
            if reset_name is None or active is None:
                continue
            active_index = int(active)
            calls.append({
                "file": path,
                "line": getattr(node, "lineno", None),
                "helper_file": definition["path"],
                "helper": definition["function"],
                "reset": reset_name,
                "active_argument": active,
                "assert_level": definition["assert_table"][active_index],
                "deassert_level": definition["deassert_table"][active_index],
            })
    return calls, definitions


def _direct_reset_records(
    parsed_files: dict[str, ast.Module],
    reset_names: set[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path, tree in parsed_files.items():
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            by_reset: dict[str, list[tuple[int, int]]] = {}
            for node in ast.walk(function):
                value = _assignment_value(node)
                if value is None:
                    continue
                level = _literal_bool(value)
                if level is None:
                    continue
                for target in _assignment_targets(node):
                    if not (
                        isinstance(target, ast.Attribute)
                        and target.attr == "value"
                        and isinstance(target.value, ast.Attribute)
                        and isinstance(target.value.value, ast.Name)
                        and target.value.value.id == "dut"
                        and target.value.attr in reset_names
                    ):
                        continue
                    by_reset.setdefault(target.value.attr, []).append(
                        (getattr(node, "lineno", 0), int(level))
                    )
            for reset_name, sequence in by_reset.items():
                levels = [level for _, level in sorted(sequence)]
                if len(levels) < 2 or levels[0] == levels[-1]:
                    continue
                records.append({
                    "file": path,
                    "function": function.name,
                    "reset": reset_name,
                    "assert_level": levels[0],
                    "deassert_level": levels[-1],
                    "source": "direct_dut_assignment",
                })
    return records


def infer_cvdp_reset_polarities(
    harness_files: dict[str, Any],
    reset_names: list[str] | tuple[str, ...] | set[str],
) -> dict[str, str]:
    """Infer polarity from the concrete reset sequence exercised by cocotb.

    Conditional helpers are evaluated with each call site's actual ``active``
    argument. This avoids treating the first branch of ``0 if active else 1``
    as the driven value when the helper is called with ``active=False``.
    """

    wanted = {str(name) for name in reset_names}
    parsed_files: dict[str, ast.Module] = {}
    for raw_path, raw_content in harness_files.items():
        path = str(raw_path)
        if not path.endswith(".py"):
            continue
        try:
            parsed_files[path] = ast.parse(str(raw_content))
        except SyntaxError:
            continue

    evidence: dict[str, set[str]] = {name: set() for name in wanted}
    call_records, _ = _reset_call_records(parsed_files)
    records = [
        *call_records,
        *_direct_reset_records(parsed_files, wanted),
    ]
    for record in records:
        reset_name = str(record.get("reset", ""))
        asserted = record.get("assert_level")
        deasserted = record.get("deassert_level")
        if reset_name not in wanted or {asserted, deasserted} != {0, 1}:
            continue
        evidence[reset_name].add(
            "active-low" if asserted == 0 else "active-high"
        )
    return {
        name: next(iter(polarities))
        for name, polarities in evidence.items()
        if len(polarities) == 1
    }


def _reset_helper_repair_plan(
    parsed_files: dict[str, ast.Module],
    reset_polarities: dict[str, str],
) -> tuple[dict[str, set[str]], list[dict[str, Any]], list[dict[str, Any]]]:
    checks_by_definition: dict[tuple[str, str], list[dict[str, Any]]] = {}
    call_records, _ = _reset_call_records(parsed_files)
    for record in call_records:
        reset_name = str(record["reset"])
        polarity = reset_polarities.get(reset_name)
        if polarity not in {
                "active-low", "active-high"
            }:
            continue
        assert_level = record["assert_level"]
        deassert_level = record["deassert_level"]
        expected_assert = 0 if polarity == "active-low" else 1
        expected_deassert = 1 - expected_assert
        check = {
            **record,
            "polarity": polarity,
            "contract_match": (
                assert_level == expected_assert
                and deassert_level == expected_deassert
            ),
            "contract_match_after_swap": (
                deassert_level == expected_assert
                and assert_level == expected_deassert
            ),
        }
        checks_by_definition.setdefault(
            (str(record["helper_file"]), str(record["helper"])), []
        ).append(check)

    repair_plan: dict[str, set[str]] = {}
    checks: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for key, helper_checks in checks_by_definition.items():
        checks.extend(helper_checks)
        if all(check["contract_match"] for check in helper_checks):
            continue
        if all(check["contract_match_after_swap"] for check in helper_checks):
            repair_plan.setdefault(key[0], set()).add(key[1])
            continue
        warnings.append({
            "code": "reset_helper_contract_conflict",
            "helper_file": key[0],
            "helper": key[1],
            "calls": helper_checks,
        })
    return repair_plan, checks, warnings


class _ResetHelperTransformer(ast.NodeTransformer):
    """Swap helper phases only after call-site contract validation."""

    def __init__(self, path: str, repair_functions: set[str]) -> None:
        self.path = path
        self.repair_functions = repair_functions
        self.repairs: list[dict[str, Any]] = []

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> ast.FunctionDef | ast.AsyncFunctionDef:
        positional = [*node.args.posonlyargs, *node.args.args]
        names = [arg.arg for arg in positional]
        kwonly = [arg.arg for arg in node.args.kwonlyargs]
        if "reset" not in node.name.lower() or not names:
            return self.generic_visit(node)
        active_name = next(
            (name for name in [*names, *kwonly] if name.lower() == "active"),
            None,
        )
        if active_name is None:
            return self.generic_visit(node)

        signal_name = names[0]
        assignments = sorted(
            (
                item
                for item in ast.walk(node)
                if _targets_signal_value(item, signal_name)
                and _assignment_value(item) is not None
            ),
            key=lambda item: (getattr(item, "lineno", 0), getattr(item, "col_offset", 0)),
        )
        if len(assignments) < 2:
            return self.generic_visit(node)

        first = assignments[0]
        last = assignments[-1]
        first_value = _assignment_value(first)
        last_value = _assignment_value(last)
        assert first_value is not None and last_value is not None
        first_table = _active_truth_table(first_value, active_name)
        last_table = _active_truth_table(last_value, active_name)
        if node.name in self.repair_functions:
            first.value, last.value = last.value, first.value
            self.repairs.append({
                "code": "reset_helper_assert_deassert_reversed",
                "file": self.path,
                "function": node.name,
                "signal_parameter": signal_name,
                "active_parameter": active_name,
                "before": {
                    "assert_when_active_false_true": list(first_table),
                    "deassert_when_active_false_true": list(last_table),
                },
                "after": {
                    "assert_when_active_false_true": list(last_table),
                    "deassert_when_active_false_true": list(first_table),
                },
            })
        return self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._visit_function(node)


def _clock_for_reset(reset_name: str, clock_ports: set[str]) -> str | None:
    if len(clock_ports) == 1:
        return next(iter(clock_ports))
    if not clock_ports:
        return None

    reset_lower = reset_name.lower()
    candidates: list[tuple[int, str]] = []
    for clock in sorted(clock_ports):
        clock_lower = clock.lower()
        shared = 0
        for left, right in zip(reset_lower, clock_lower):
            if left != right:
                break
            shared += 1
        candidates.append((shared, clock))
    best = max(score for score, _ in candidates)
    winners = [clock for score, clock in candidates if score == best]
    return winners[0] if best > 0 and len(winners) == 1 else None


def _falling_edge_await(clock_name: str, template: ast.AST) -> ast.Expr:
    node = ast.Expr(
        value=ast.Await(
            value=ast.Call(
                func=ast.Name(id="FallingEdge", ctx=ast.Load()),
                args=[
                    ast.Attribute(
                        value=ast.Name(id="dut", ctx=ast.Load()),
                        attr=clock_name,
                        ctx=ast.Load(),
                    )
                ],
                keywords=[],
            )
        )
    )
    return ast.copy_location(node, template)


class _ResetReleaseAligner(ast.NodeTransformer):
    """Move post-reset stimulus into the clock's low phase.

    Timer-based reset helpers can return on the same simulator timestamp as a
    rising clock edge. Driving request inputs immediately afterward is then
    scheduler-order dependent: the DUT may already have sampled that edge.
    """

    def __init__(
        self,
        path: str,
        call_records: list[dict[str, Any]],
        clock_ports: set[str],
    ) -> None:
        self.path = path
        self.by_line: dict[int, dict[str, Any]] = {}
        self.alignments: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []
        for record in call_records:
            if record.get("file") != path or not isinstance(record.get("line"), int):
                continue
            clock = _clock_for_reset(str(record["reset"]), clock_ports)
            enriched = {**record, "clock": clock}
            self.by_line[int(record["line"])] = enriched
            if clock is None:
                self.warnings.append({
                    "code": "reset_release_clock_ambiguous",
                    "file": path,
                    "line": record["line"],
                    "reset": record["reset"],
                    "clock_ports": sorted(clock_ports),
                })

    def visit_Expr(self, node: ast.Expr) -> ast.AST | list[ast.AST]:
        node = self.generic_visit(node)
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Await)
        ):
            return node
        matching = next(
            (
                self.by_line[getattr(call, "lineno", -1)]
                for call in ast.walk(node.value)
                if isinstance(call, ast.Call)
                and getattr(call, "lineno", -1) in self.by_line
            ),
            None,
        )
        if matching is None or matching["clock"] is None:
            return node
        self.alignments.append({
            "line": matching["line"],
            "reset": matching["reset"],
            "clock": matching["clock"],
        })
        return [node, _falling_edge_await(str(matching["clock"]), node)]


def _ensure_trigger_import(tree: ast.Module, trigger: str) -> None:
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module == "cocotb.triggers":
            if not any(alias.name == trigger for alias in statement.names):
                statement.names.append(ast.alias(name=trigger))
            return
    insertion = 0
    while insertion < len(tree.body) and isinstance(
        tree.body[insertion], (ast.Import, ast.ImportFrom)
    ):
        insertion += 1
    tree.body.insert(
        insertion,
        ast.ImportFrom(
            module="cocotb.triggers",
            names=[ast.alias(name=trigger)],
            level=0,
        ),
    )


def _progress_monitor_definition(port_names: list[str]) -> ast.AsyncFunctionDef:
    source = f"""
async def {PROGRESS_MONITOR_NAME}(dut):
    elapsed_ns = 0
    for delay_ns in {PROGRESS_SNAPSHOT_DELAYS_NS!r}:
        await Timer(delay_ns, unit="ns")
        elapsed_ns += delay_ns
        values = []
        for name in {tuple(port_names)!r}:
            try:
                values.append(f"{{name}}={{getattr(dut, name).value}}")
            except Exception as exc:
                values.append(f"{{name}}=<unavailable:{{type(exc).__name__}}>")
        print(
            f"[CVDP_PROGRESS after={{elapsed_ns}}ns] " + " ".join(values),
            flush=True,
        )
"""
    definition = ast.parse(source).body[0]
    assert isinstance(definition, ast.AsyncFunctionDef)
    return definition


def _progress_monitor_start(template: ast.AST) -> ast.Expr:
    node = ast.Expr(
        value=ast.Call(
            func=ast.Attribute(
                value=ast.Name(id="cocotb", ctx=ast.Load()),
                attr="start_soon",
                ctx=ast.Load(),
            ),
            args=[
                ast.Call(
                    func=ast.Name(id=PROGRESS_MONITOR_NAME, ctx=ast.Load()),
                    args=[ast.Name(id="dut", ctx=ast.Load())],
                    keywords=[],
                )
            ],
            keywords=[],
        )
    )
    return ast.copy_location(node, template)


class _CocotbProgressMonitorInjector(ast.NodeTransformer):
    """Start a read-only public-port sampler in each cocotb test."""

    def __init__(self) -> None:
        self.functions: list[str] = []

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        node = self.generic_visit(node)
        if not any(_is_cocotb_test_decorator(item) for item in node.decorator_list):
            return node
        if not any(arg.arg == "dut" for arg in [*node.args.posonlyargs, *node.args.args]):
            return node
        insert_at = 1 if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ) else 0
        template = node.body[insert_at] if insert_at < len(node.body) else node
        node.body.insert(insert_at, _progress_monitor_start(template))
        self.functions.append(node.name)
        return node


def adapt_python_harness(
    source: str,
    *,
    path: str,
    normalize_reset_helpers: bool,
    stabilize_cocotb_edges: bool,
    initialize_cocotb_inputs: bool,
    align_reset_release: bool,
    emit_progress_monitor: bool,
    input_ports: set[str] | None = None,
    observed_ports: set[str] | None = None,
    clock_ports: set[str] | None = None,
    reset_call_records: list[dict[str, Any]] | None = None,
    reset_helper_repairs: set[str] | None = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return source, [], [{
            "code": "harness_python_parse_failed",
            "file": path,
            "line": exc.lineno,
            "message": exc.msg,
        }]

    transformations: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    changed = False
    if normalize_reset_helpers:
        reset_transformer = _ResetHelperTransformer(
            path,
            set(reset_helper_repairs or ()),
        )
        tree = reset_transformer.visit(tree)
        transformations.extend(reset_transformer.repairs)
        changed = changed or bool(reset_transformer.repairs)

    if align_reset_release:
        release_aligner = _ResetReleaseAligner(
            path,
            list(reset_call_records or ()),
            set(clock_ports or ()),
        )
        tree = release_aligner.visit(tree)
        if release_aligner.alignments:
            _ensure_trigger_import(tree, "FallingEdge")
            transformations.append({
                "code": "cocotb_reset_release_aligned",
                "file": path,
                "alignments": release_aligner.alignments,
            })
            changed = True
        warnings.extend(release_aligner.warnings)

    if initialize_cocotb_inputs:
        input_transformer = _CocotbInputInitializer(set(input_ports or ()))
        tree = input_transformer.visit(tree)
        if input_transformer.functions:
            transformations.append({
                "code": "cocotb_public_inputs_initialized",
                "file": path,
                "functions": input_transformer.functions,
            })
            changed = True

    progress_ports = sorted(
        name for name in set(observed_ports or ()) if name.isidentifier()
    )
    existing_names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if (
        emit_progress_monitor
        and progress_ports
        and PROGRESS_MONITOR_NAME not in existing_names
    ):
        monitor_injector = _CocotbProgressMonitorInjector()
        tree = monitor_injector.visit(tree)
        if monitor_injector.functions:
            tree.body.append(_progress_monitor_definition(progress_ports))
            _ensure_trigger_import(tree, "Timer")
            transformations.append({
                "code": "cocotb_public_port_progress_monitor_inserted",
                "file": path,
                "functions": monitor_injector.functions,
                "ports": progress_ports,
                "snapshot_delays_ns": list(PROGRESS_SNAPSHOT_DELAYS_NS),
            })
            changed = True

    if stabilize_cocotb_edges:
        phase_transformer = _CocotbPhaseTransformer()
        tree = phase_transformer.visit(tree)
        if phase_transformer.insertions:
            _ensure_trigger_import(tree, "Timer")
            transformations.append({
                "code": "cocotb_post_edge_settle_inserted",
                "file": path,
                "edge_waits": phase_transformer.insertions,
            })
            changed = True

    if not changed:
        return source, transformations, warnings
    ast.fix_missing_locations(tree)
    return ast.unparse(tree) + "\n", transformations, warnings


def adapt_cvdp_harness_files(
    harness_files: dict[str, Any],
    *,
    reset_polarities: dict[str, str] | None = None,
    input_ports: list[str] | tuple[str, ...] | set[str] | None = None,
    normalize_reset_helpers: bool = False,
    stabilize_cocotb_edges: bool = False,
    initialize_cocotb_inputs: bool = False,
    align_reset_release: bool = False,
    emit_progress_monitor: bool = False,
    clock_ports: list[str] | tuple[str, ...] | set[str] | None = None,
    observed_ports: list[str] | tuple[str, ...] | set[str] | None = None,
    harness_profile: str = "custom",
    harness_profile_overrides: dict[str, bool] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Return a faithful harness copy plus an auditable opt-in transform manifest."""

    adapted: dict[str, str] = {}
    sources: list[dict[str, Any]] = []
    transformations: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    parsed_files: dict[str, ast.Module] = {}
    for raw_path, raw_content in harness_files.items():
        path = str(raw_path)
        if not path.endswith(".py"):
            continue
        try:
            parsed_files[path] = ast.parse(str(raw_content))
        except SyntaxError:
            pass
    repair_plan: dict[str, set[str]] = {}
    reset_contract_checks: list[dict[str, Any]] = []
    reset_call_records, _ = _reset_call_records(parsed_files)
    if normalize_reset_helpers:
        repair_plan, reset_contract_checks, reset_warnings = (
            _reset_helper_repair_plan(
                parsed_files,
                dict(reset_polarities or {}),
            )
        )
        warnings.extend(reset_warnings)

    resolved_clock_ports = sorted(
        str(name) for name in (
            clock_ports
            if clock_ports is not None
            else infer_cvdp_clock_ports(harness_files, input_ports or ())
        )
    )

    for raw_path, raw_content in harness_files.items():
        path = str(raw_path)
        content = str(raw_content)
        rewritten = content
        file_transformations: list[dict[str, Any]] = []
        file_warnings: list[dict[str, Any]] = []
        if path.endswith(".py"):
            rewritten, file_transformations, file_warnings = adapt_python_harness(
                content,
                path=path,
                normalize_reset_helpers=normalize_reset_helpers,
                stabilize_cocotb_edges=stabilize_cocotb_edges,
                initialize_cocotb_inputs=initialize_cocotb_inputs,
                align_reset_release=align_reset_release,
                emit_progress_monitor=emit_progress_monitor,
                input_ports=set(input_ports or ()),
                observed_ports=set(observed_ports or ()),
                clock_ports=set(resolved_clock_ports),
                reset_call_records=reset_call_records,
                reset_helper_repairs=repair_plan.get(path, set()),
            )
        adapted[path] = rewritten
        sources.append({
            "file": path,
            "original_sha256": _sha256(content),
            "adapted_sha256": _sha256(rewritten),
            "changed": rewritten != content,
        })
        transformations.extend(file_transformations)
        warnings.extend(file_warnings)

    manifest = {
        "schema_version": 1,
        "adapter": "cvdp_race_safe_harness",
        "harness_profile": str(harness_profile),
        "harness_profile_overrides": dict(harness_profile_overrides or {}),
        "effective_options": {
            "normalize_reset_helpers": bool(normalize_reset_helpers),
            "stabilize_cocotb_edges": bool(stabilize_cocotb_edges),
            "initialize_cocotb_inputs": bool(initialize_cocotb_inputs),
            "align_reset_release": bool(align_reset_release),
            "emit_progress_monitor": bool(emit_progress_monitor),
        },
        "reset_helper_normalization_enabled": bool(normalize_reset_helpers),
        "cocotb_phase_stabilization_enabled": bool(stabilize_cocotb_edges),
        "cocotb_input_initialization_enabled": bool(initialize_cocotb_inputs),
        "cocotb_reset_release_alignment_enabled": bool(align_reset_release),
        "cocotb_progress_monitor_enabled": bool(emit_progress_monitor),
        "public_input_ports": sorted(str(name) for name in (input_ports or ())),
        "public_observed_ports": sorted(str(name) for name in (observed_ports or ())),
        "clock_ports": resolved_clock_ports,
        "reset_polarities": dict(reset_polarities or {}),
        "reset_contract_checks": reset_contract_checks,
        "sources": sources,
        "transformations": transformations,
        "warnings": warnings,
        "changed_file_count": sum(row["changed"] for row in sources),
    }
    return adapted, manifest
