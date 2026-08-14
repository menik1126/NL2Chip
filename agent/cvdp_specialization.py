"""Finite parameter-family discovery for the CVDP P0 specialization path.

The public CVDP runner files are benchmark integration metadata.  This module
extracts only build-time parameter combinations; it never reads cocotb expected
values or behavioral assertions.
"""
from __future__ import annotations

import ast
import itertools
import math
import re
import textwrap
from dataclasses import dataclass
from typing import Any, Iterable


_MAX_COMBINATIONS = 256


def lean_identifier(name: str) -> str:
    ident = re.sub(r"\W+", "_", str(name).strip())
    ident = re.sub(r"_+", "_", ident).strip("_") or "generated_design"
    if ident[0].isdigit():
        ident = f"design_{ident}"
    return ident


def _value_identifier(value: object) -> str:
    text = str(value).strip()
    if re.fullmatch(r"-\d+", text):
        return f"neg_{text[1:]}"
    ident = re.sub(r"\W+", "_", text)
    ident = re.sub(r"_+", "_", ident).strip("_")
    # This fragment is always preceded by ``<parameter>_`` in the complete
    # module name, so a leading digit is legal and should stay readable.
    return ident or "value"


def specialization_module_name(
    design_name: str,
    values: dict[str, int],
    parameter_names: Iterable[str] | None = None,
) -> str:
    names = tuple(parameter_names or sorted(values))
    suffix = "__".join(
        f"{lean_identifier(name)}_{_value_identifier(values[name])}" for name in names
    )
    return f"{lean_identifier(design_name)}__p0__{suffix}"


@dataclass(frozen=True)
class SpecializationCase:
    parameters: tuple[tuple[str, int], ...]
    module_name: str

    @property
    def values(self) -> dict[str, int]:
        return dict(self.parameters)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameters": self.values,
            "module_name": self.module_name,
        }


@dataclass(frozen=True)
class FiniteParameterPlan:
    design_name: str
    parameter_names: tuple[str, ...]
    cases: tuple[SpecializationCase, ...]
    diagnostics: tuple[str, ...] = ()
    source: str = "public_cvdp_harness"

    @property
    def supported(self) -> bool:
        return bool(self.parameter_names and self.cases) and not self.diagnostics

    @property
    def expected_modules(self) -> tuple[str, ...]:
        return tuple(case.module_name for case in self.cases)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "mode": "finite_parameter_specialization",
            "source": self.source,
            "design_name": self.design_name,
            "parameter_names": list(self.parameter_names),
            "specialization_count": len(self.cases),
            "cases": [case.to_dict() for case in self.cases],
            "diagnostics": list(self.diagnostics),
            "supported": self.supported,
        }


class _UnknownValue(Exception):
    pass


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _assignment_nodes(tree: ast.AST) -> dict[str, list[ast.AST]]:
    assignments: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and value is not None:
                    assignments.setdefault(target.id, []).append(value)
    return assignments


def _function_nodes(tree: ast.AST) -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _eval_power_helper(function: ast.FunctionDef, iterations: int) -> list[Any]:
    """Interpret CVDP's two small get_powers_of_two_pairs helper variants."""
    append_args = [
        call.args[0]
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "append"
        and call.args
    ]
    if not append_args:
        raise _UnknownValue("helper has no append")
    if isinstance(append_args[0], ast.Tuple):
        value = 4
        pairs: list[tuple[int, int]] = []
        for _ in range(iterations):
            parity = 0
            while 2**parity < parity + value + 1:
                parity += 1
            pairs.append((value, parity))
            value *= 2
        return pairs
    value = 2
    return [value * (2**index) for index in range(iterations)]


def _eval_expr(
    node: ast.AST,
    context: dict[str, Any],
    assignments: dict[str, list[ast.AST]],
    functions: dict[str, ast.FunctionDef],
    seen: set[str] | None = None,
) -> Any:
    seen = set(seen or ())
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in context:
            return context[node.id]
        if node.id in seen:
            raise _UnknownValue(f"cyclic assignment for {node.id}")
        candidates = assignments.get(node.id, [])
        # ast.walk visits the module-level binding used by a pytest decorator
        # before a helper's local temporary with the same name (for example
        # ``pairs = get_powers...`` before ``pairs = []``). Prefer that public
        # runner binding, while falling through if it is dynamic.
        for candidate in candidates:
            try:
                return _eval_expr(
                    candidate,
                    context,
                    assignments,
                    functions,
                    seen | {node.id},
                )
            except _UnknownValue:
                continue
        raise _UnknownValue(f"unknown name {node.id}")
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [
            _eval_expr(item, context, assignments, functions, seen)
            for item in node.elts
        ]
        return tuple(values) if isinstance(node, ast.Tuple) else values
    if isinstance(node, ast.Dict):
        result = {}
        for key, value in zip(node.keys, node.values):
            if key is None:
                continue
            result[_eval_expr(key, context, assignments, functions, seen)] = _eval_expr(
                value, context, assignments, functions, seen
            )
        return result
    if isinstance(node, ast.UnaryOp):
        value = _eval_expr(node.operand, context, assignments, functions, seen)
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return +value
        if isinstance(node.op, ast.Not):
            return not value
        if isinstance(node.op, ast.Invert):
            return ~value
    if isinstance(node, ast.BinOp):
        left = _eval_expr(node.left, context, assignments, functions, seen)
        right = _eval_expr(node.right, context, assignments, functions, seen)
        operations = {
            ast.Add: lambda: left + right,
            ast.Sub: lambda: left - right,
            ast.Mult: lambda: left * right,
            ast.FloorDiv: lambda: left // right,
            ast.Div: lambda: left / right,
            ast.Mod: lambda: left % right,
            ast.Pow: lambda: left**right,
            ast.LShift: lambda: left << right,
            ast.RShift: lambda: left >> right,
            ast.BitOr: lambda: left | right,
            ast.BitAnd: lambda: left & right,
            ast.BitXor: lambda: left ^ right,
        }
        for typ, operation in operations.items():
            if isinstance(node.op, typ):
                return operation()
    if isinstance(node, ast.Call):
        name = _call_name(node.func)
        args = [
            _eval_expr(arg, context, assignments, functions, seen) for arg in node.args
        ]
        if name == "range":
            return list(range(*[int(value) for value in args]))
        if name in {"int", "builtins.int"} and args:
            return int(args[0])
        if name in {"math.log2", "log2"} and args:
            return math.log2(args[0])
        if name in {"math.ceil", "ceil"} and args:
            return math.ceil(args[0])
        if name == "get_powers_of_two_pairs" and args:
            function = functions.get(name)
            if function is None:
                raise _UnknownValue("missing get_powers_of_two_pairs definition")
            return _eval_power_helper(function, int(args[0]))
    raise _UnknownValue(f"unsupported expression {ast.dump(node, include_attributes=False)[:160]}")


def _parameter_dict_nodes(
    tree: ast.AST,
    assignments: dict[str, list[ast.AST]],
) -> list[ast.Dict]:
    nodes: list[ast.Dict] = []
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if not _call_name(call.func).endswith(".build"):
            continue
        for keyword in call.keywords:
            if keyword.arg != "parameters":
                continue
            if isinstance(keyword.value, ast.Dict):
                nodes.append(keyword.value)
            elif isinstance(keyword.value, ast.Name):
                nodes.extend(
                    candidate
                    for candidate in assignments.get(keyword.value.id, [])
                    if isinstance(candidate, ast.Dict)
                )
    return nodes


def _dict_entries(node: ast.Dict) -> dict[str, ast.AST]:
    entries: dict[str, ast.AST] = {}
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            entries[key.value] = value
    return entries


def _decorator_group(
    decorator: ast.AST,
    assignments: dict[str, list[ast.AST]],
    functions: dict[str, ast.FunctionDef],
) -> tuple[tuple[str, ...], list[tuple[Any, ...]]] | None:
    if not isinstance(decorator, ast.Call) or len(decorator.args) < 2:
        return None
    if not _call_name(decorator.func).endswith("parametrize"):
        return None
    try:
        raw_names = _eval_expr(decorator.args[0], {}, assignments, functions)
        raw_values = _eval_expr(decorator.args[1], {}, assignments, functions)
    except _UnknownValue:
        return None
    names = tuple(name.strip() for name in str(raw_names).split(",") if name.strip())
    if not names or not isinstance(raw_values, (list, tuple, range)):
        return None
    rows: list[tuple[Any, ...]] = []
    for raw in raw_values:
        if len(names) == 1:
            value = raw[0] if isinstance(raw, tuple) and len(raw) == 1 else raw
            rows.append((value,))
        elif isinstance(raw, (list, tuple)) and len(raw) == len(names):
            rows.append(tuple(raw))
    return (names, rows) if rows else None


def _decorated_contexts(
    tree: ast.AST,
    assignments: dict[str, list[ast.AST]],
    functions: dict[str, ast.FunctionDef],
) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for function in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        groups = [
            group
            for decorator in function.decorator_list
            if (group := _decorator_group(decorator, assignments, functions)) is not None
        ]
        if not groups:
            continue
        group_contexts: list[list[dict[str, Any]]] = []
        for names, rows in groups:
            group_contexts.append(
                [dict(zip(names, row)) for row in rows]
            )
        for selected in itertools.product(*group_contexts):
            merged: dict[str, Any] = {}
            compatible = True
            for row in selected:
                for name, value in row.items():
                    if name in merged and merged[name] != value:
                        compatible = False
                    merged[name] = value
            if compatible:
                contexts.append(merged)
    return contexts


def _default_pytest_contexts(
    tree: ast.Module,
    parameter_names: set[str],
    assignments: dict[str, list[ast.AST]],
    functions: dict[str, ast.FunctionDef],
) -> list[dict[str, Any]]:
    """Recover independently collected pytest tests that use argument defaults.

    A module-level ``test_runner(WIDTH=8)`` is collected once by pytest even
    when another parametrized test calls it with sweep values. CVDP uses this
    pattern in a few public runners, so the defaults are part of the evaluated
    parameter family too.
    """
    contexts: list[dict[str, Any]] = []
    for function in tree.body:
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not function.name.startswith("test"):
            continue
        if any(
            isinstance(decorator, ast.Call)
            and _call_name(decorator.func).endswith("parametrize")
            for decorator in function.decorator_list
        ):
            continue
        if not any(
            isinstance(node, ast.Call) and _call_name(node.func).endswith(".build")
            for node in ast.walk(function)
        ):
            continue

        positional = [*function.args.posonlyargs, *function.args.args]
        defaults: dict[str, ast.AST] = {}
        if function.args.defaults:
            for argument, default in zip(
                positional[-len(function.args.defaults):],
                function.args.defaults,
            ):
                defaults[argument.arg] = default
        for argument, default in zip(
            function.args.kwonlyargs,
            function.args.kw_defaults,
        ):
            if default is not None:
                defaults[argument.arg] = default

        relevant = parameter_names.intersection(defaults)
        if relevant != parameter_names:
            continue
        context: dict[str, Any] = {}
        try:
            for name in parameter_names:
                context[name] = _eval_expr(
                    defaults[name], context, assignments, functions
                )
        except _UnknownValue:
            continue
        contexts.append(context)
    return contexts


def _loop_contexts(
    tree: ast.AST,
    assignments: dict[str, list[ast.AST]],
    functions: dict[str, ast.FunctionDef],
) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name):
            continue
        try:
            values = _eval_expr(node.iter, {}, assignments, functions)
        except _UnknownValue:
            continue
        if isinstance(values, (list, tuple, range)):
            contexts.extend({node.target.id: value} for value in values)
    return contexts


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"[-+]?\d+", value.strip()):
        return int(value)
    raise _UnknownValue(f"parameter value is not an integer: {value!r}")


def _expand_parameter_context(
    seed: dict[str, Any],
    parameter_entries: dict[str, ast.AST],
    assignments: dict[str, list[ast.AST]],
    functions: dict[str, ast.FunctionDef],
) -> dict[str, int] | None:
    context = dict(seed)
    result: dict[str, int] = {}
    pending = dict(parameter_entries)
    for _ in range(len(pending) + 1):
        progressed = False
        for name, expression in list(pending.items()):
            try:
                value = _eval_expr(expression, context, assignments, functions)
                value = _coerce_int(value)
            except _UnknownValue:
                if name in context:
                    try:
                        value = _coerce_int(context[name])
                    except _UnknownValue:
                        continue
                else:
                    continue
            result[name] = value
            context[name] = value
            pending.pop(name)
            progressed = True
        if not pending or not progressed:
            break
    return result if not pending else None


def _deduplicate_combinations(
    combinations: Iterable[dict[str, int]],
    parameter_names: tuple[str, ...],
) -> list[dict[str, int]]:
    seen: set[tuple[int, ...]] = set()
    result: list[dict[str, int]] = []
    for combo in combinations:
        if any(name not in combo for name in parameter_names):
            continue
        key = tuple(combo[name] for name in parameter_names)
        if key in seen:
            continue
        seen.add(key)
        result.append({name: combo[name] for name in parameter_names})
    result.sort(key=lambda combo: tuple(combo[name] for name in parameter_names))
    return result


def discover_finite_parameter_plan(
    *,
    design_name: str,
    harness_files: dict[str, Any],
) -> FiniteParameterPlan:
    parameter_entries: dict[str, ast.AST] = {}
    all_contexts: list[dict[str, Any]] = []
    fallback_values: dict[str, set[int]] = {}
    diagnostics: list[str] = []
    parsed_files = 0

    for path, raw_content in harness_files.items():
        if not str(path).endswith(".py"):
            continue
        try:
            tree = ast.parse(textwrap.dedent(str(raw_content)))
        except SyntaxError as exc:
            diagnostics.append(f"could not parse public runner {path}: {exc.msg}")
            continue
        assignments = _assignment_nodes(tree)
        functions = _function_nodes(tree)
        dict_nodes = _parameter_dict_nodes(tree, assignments)
        if not dict_nodes:
            continue
        parsed_files += 1
        local_entries: dict[str, ast.AST] = {}
        for node in dict_nodes:
            local_entries.update(_dict_entries(node))
        parameter_entries.update(local_entries)

        contexts = _decorated_contexts(tree, assignments, functions)
        contexts.extend(
            _default_pytest_contexts(
                tree,
                set(local_entries),
                assignments,
                functions,
            )
        )
        contexts.extend(_loop_contexts(tree, assignments, functions))
        if not contexts:
            contexts = [{}]
        for context in contexts:
            expanded = _expand_parameter_context(
                context, local_entries, assignments, functions
            )
            if expanded:
                all_contexts.append(expanded)
                for name, value in expanded.items():
                    fallback_values.setdefault(name, set()).add(value)

        # Record direct static value families even when a test context also
        # contains an unrelated decorator such as ``test = range(2)``.
        for name, expression in local_entries.items():
            try:
                raw_value = _eval_expr(expression, {}, assignments, functions)
                value = _coerce_int(raw_value)
            except _UnknownValue:
                continue
            fallback_values.setdefault(name, set()).add(value)

    parameter_names = tuple(sorted(parameter_entries))
    if not parsed_files or not parameter_names:
        return FiniteParameterPlan(
            design_name=design_name,
            parameter_names=parameter_names,
            cases=(),
            diagnostics=("no statically discoverable CVDP build parameters",),
        )

    combinations = _deduplicate_combinations(all_contexts, parameter_names)
    if not combinations and all(fallback_values.get(name) for name in parameter_names):
        product_size = math.prod(len(fallback_values[name]) for name in parameter_names)
        if product_size <= _MAX_COMBINATIONS:
            combinations = [
                dict(zip(parameter_names, row))
                for row in itertools.product(
                    *(sorted(fallback_values[name]) for name in parameter_names)
                )
            ]

    if len(combinations) > _MAX_COMBINATIONS:
        diagnostics.append(
            f"public parameter family has {len(combinations)} combinations; "
            f"limit is {_MAX_COMBINATIONS}"
        )
        combinations = []
    if not combinations:
        diagnostics.append(
            "could not resolve every build parameter to a finite public combination"
        )

    cases = tuple(
        SpecializationCase(
            parameters=tuple((name, combo[name]) for name in parameter_names),
            module_name=specialization_module_name(
                design_name, combo, parameter_names
            ),
        )
        for combo in combinations
    )
    return FiniteParameterPlan(
        design_name=design_name,
        parameter_names=parameter_names,
        cases=cases,
        diagnostics=tuple(dict.fromkeys(diagnostics)),
    )


def parameter_values_and_combinations(
    harness_files: dict[str, Any],
    design_name: str = "dut",
) -> tuple[dict[str, list[str]], list[dict[str, str]]]:
    plan = discover_finite_parameter_plan(
        design_name=design_name,
        harness_files=harness_files,
    )
    values: dict[str, set[int]] = {name: set() for name in plan.parameter_names}
    combinations: list[dict[str, str]] = []
    for case in plan.cases:
        combo = case.values
        combinations.append({name: str(value) for name, value in combo.items()})
        for name, value in combo.items():
            values[name].add(value)
    return (
        {name: [str(value) for value in sorted(items)] for name, items in values.items()},
        combinations,
    )


def plan_from_dict(payload: dict[str, Any] | None) -> FiniteParameterPlan | None:
    if not payload or payload.get("mode") != "finite_parameter_specialization":
        return None
    names = tuple(str(name) for name in payload.get("parameter_names", []))
    cases = []
    for row in payload.get("cases", []):
        raw = row.get("parameters", {})
        values = {name: int(raw[name]) for name in names}
        cases.append(
            SpecializationCase(
                parameters=tuple((name, values[name]) for name in names),
                module_name=str(row["module_name"]),
            )
        )
    return FiniteParameterPlan(
        design_name=str(payload.get("design_name", "dut")),
        parameter_names=names,
        cases=tuple(cases),
        diagnostics=tuple(str(item) for item in payload.get("diagnostics", [])),
        source=str(payload.get("source", "public_cvdp_harness")),
    )


def format_specialization_contract(plan: FiniteParameterPlan) -> str:
    lines = [
        "### Finite Parameter Specialization (P0)",
        "",
        "Sparkle does not preserve symbolic SystemVerilog parameters. For this run, "
        "implement one generic Lean core and synthesize every listed public parameter "
        "combination as a concrete module. The evaluator will generate the parameterized "
        "SystemVerilog selector; do not implement behavior in a Verilog wrapper.",
        "",
        f"- Generic core suggested name: `{lean_identifier(plan.design_name)}_core`",
        f"- Public selector top (generated by evaluator): `{plan.design_name}`",
        f"- Required concrete modules: {len(plan.cases)}",
    ]
    for case in plan.cases:
        values = ", ".join(f"{name}={value}" for name, value in case.parameters)
        lines.append(f"- `{case.module_name}` for {values}")
    lines.extend([
        "",
        "For every concrete module, define an eta-expanded alias that explicitly accepts "
        "all signal arguments, binds every Nat parameter to the listed literal, and calls "
        "the shared core. A partial-application alias without `fun <ports> => ...` is invalid: "
        "Sparkle turns those missing arguments into internal undriven wires.",
        "Add one `#synthesizeVerilog <concrete_module>` command for every required module. "
        "Do not synthesize the unresolved generic core and do not synthesize a module named "
        f"`{plan.design_name}`, because the evaluator owns that public selector name.",
    ])
    return "\n".join(lines)
