#!/usr/bin/env python3
"""CPU probe: does structured context modulate metrics for smell labels?"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

FE = Path(
    "/data/HQ/code-smell-detection/code_smell_dataset_enrichment/output/"
    "fetruth_context_contract_projection_v2_lcom_exact_v5/training_instances.jsonl"
)
MLCQ = Path(
    "/data/HQ/code-smell-detection/code_smell_dataset_enrichment/output/"
    "mlcq_metrics25_final/training_instances.jsonl"
)

FE_METRICS = [
    "candidate_target_access_ratio",
    "candidate_target_affinity_distance",
    "source_affinity_distance",
    "foreign_method_call_count",
    "foreign_field_access_count",
    "external_call_ratio",
    "entity_loc",
    "entity_cc",
    "own_field_access_count",
]
GC_METRICS = [
    "class_lcom",
    "class_wmc",
    "class_cbo",
    "class_loc",
    "class_method_count",
    "class_field_count",
    "class_responsibility_cluster_count",
    "foreign_class_count",
]
LM_METRICS = [
    "entity_loc",
    "entity_cc",
    "entity_statement_count",
    "entity_parameter_count",
    "entity_nesting_depth",
    "entity_branch_count",
]


def sha_mod(text: str, n: int = 10) -> int:
    digest = hashlib.sha1(str(text).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % n


def split_of(project_id: str) -> str:
    bucket = sha_mod(project_id or "unknown", 10)
    if bucket <= 1:
        return "test"
    if bucket == 2:
        return "validation"
    return "train"


def label_of(rec: dict) -> int | None:
    w = rec.get("weak_label") or {}
    lab = w.get("label")
    if lab is True or lab == 1:
        return 1
    if lab is False or lab == 0:
        return 0
    pol = str(w.get("polarity") or w.get("label_status") or "").lower()
    if pol in {"positive"}:
        return 1
    if pol in {"negative"}:
        return 0
    return None


def smell_of(rec: dict) -> str:
    q = rec.get("smell_query") or {}
    return str(q.get("canonical_smell_type") or "")


def metric_vec(rec: dict, names: list[str]) -> np.ndarray:
    values = (rec.get("metrics") or {}).get("values") or {}
    row = []
    for name in names:
        v = values.get(name)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            row.append(0.0)
        else:
            row.append(float(v))
    return np.asarray(row, dtype=np.float64)


def n_callees(inter) -> int:
    raw = (inter or {}).get("direct_callees") or []
    return len(raw) if isinstance(raw, list) else 0


def fe_bits(rec: dict) -> dict[str, float]:
    ctx = rec.get("context") or {}
    sr = ctx.get("structural_role") or {}
    inter = ctx.get("interaction") or {}
    rel = rec.get("fe_target_relation_context") or {}
    st = rel.get("source_target_relation") or {}
    kind = str(rel.get("target_class_kind") or "")
    return {
        "is_static": float(bool(sr.get("is_static"))),
        "is_override": float(bool(sr.get("is_override"))),
        "is_test": float(bool(sr.get("is_test"))),
        "is_constructor": float(bool(sr.get("is_constructor"))),
        "public": float(sr.get("visibility") == "public"),
        "same_package": float(st.get("same_package") is True),
        "same_class": float(st.get("same_class") is True),
        "accessed": float(st.get("target_accessed_in_method") is True),
        "target_class": float(kind == "class"),
        "target_interface": float(kind == "interface"),
        "target_enum": float(kind == "enum"),
        "has_callees": float(n_callees(inter) > 0),
        "many_callees": float(n_callees(inter) >= 8),
    }


def mlcq_bits(rec: dict) -> dict[str, float]:
    ctx = rec.get("context") or {}
    sr = ctx.get("structural_role_context") or {}
    inter = ctx.get("interaction_context") or {}
    return {
        "is_static": float(bool(sr.get("is_static"))),
        "is_test": float(bool(sr.get("is_test"))),
        "is_override": float(bool(sr.get("is_override"))),
        "is_constructor": float(bool(sr.get("is_constructor"))),
        "is_abstract": float(bool(sr.get("is_abstract"))),
        "is_interface": float(bool(sr.get("is_interface"))),
        "is_enum": float(bool(sr.get("is_enum"))),
        "public": float(sr.get("visibility") == "public"),
        "has_callees": float(n_callees(inter) > 0 or float(inter.get("direct_callee_count") or 0) > 0),
        "many_callees": float(n_callees(inter) >= 8 or float(inter.get("direct_callee_count") or 0) >= 8),
        "has_target": float(bool(inter.get("candidate_target_class"))),
    }


def auc(y, s):
    y = np.asarray(y)
    s = np.asarray(s, dtype=float)
    if len(y) < 25 or len(set(y.tolist())) < 2:
        return None
    try:
        return float(roc_auc_score(y, s))
    except ValueError:
        return None


def ap_score(y, s):
    y = np.asarray(y)
    s = np.asarray(s, dtype=float)
    if len(y) < 25 or len(set(y.tolist())) < 2:
        return None
    try:
        return float(average_precision_score(y, s))
    except ValueError:
        return None


def load_fe():
    rows = []
    with FE.open() as handle:
        for line in handle:
            rec = json.loads(line)
            y = label_of(rec)
            if y is None:
                continue
            rows.append(
                {
                    "project": rec.get("project_id") or "unknown",
                    "split": split_of(rec.get("project_id") or "unknown"),
                    "smell": "feature_envy",
                    "y": y,
                    "m": metric_vec(rec, FE_METRICS),
                    "bits": fe_bits(rec),
                    "metrics": (rec.get("metrics") or {}).get("values") or {},
                }
            )
    return rows


def load_mlcq():
    rows = []
    with MLCQ.open() as handle:
        for line in handle:
            rec = json.loads(line)
            y = label_of(rec)
            smell = smell_of(rec)
            if y is None or smell not in {"god_class", "long_method"}:
                continue
            names = GC_METRICS if smell == "god_class" else LM_METRICS
            rows.append(
                {
                    "project": rec.get("project_id") or "unknown",
                    "split": split_of(rec.get("project_id") or "unknown"),
                    "smell": smell,
                    "y": y,
                    "m": metric_vec(rec, names),
                    "bits": mlcq_bits(rec),
                    "metrics": (rec.get("metrics") or {}).get("values") or {},
                }
            )
    return rows


def report_stratified(rows, metric, bits, title):
    print(f"\n===== A  {title} / {metric} =====")
    print(f"{'bit':<18} {'val':<6} {'n':>6} {'pos':>6} {'auc':>8} {'ap':>8} {'mean_m':>8}")

    def slice_auc(items):
        ys, xs = [], []
        for row in items:
            v = row["metrics"].get(metric)
            if v is None:
                continue
            ys.append(row["y"])
            xs.append(float(v))
        if len(ys) < 30:
            return None
        return {
            "n": len(ys),
            "pos": sum(ys) / len(ys),
            "auc": auc(ys, xs),
            "ap": ap_score(ys, xs),
            "mean": float(np.mean(xs)),
        }

    def fmt(v):
        return "NA" if v is None else f"{v:.4f}"

    base = slice_auc(rows)
    if not base:
        print("insufficient")
        return
    print(
        f"{'ALL':<18} {'':<6} {base['n']:>6} {base['pos']:>6.3f} "
        f"{fmt(base['auc']):>8} {fmt(base['ap']):>8} {base['mean']:>8.3f}"
    )
    for bit in bits:
        for val in (1.0, 0.0):
            sl = [r for r in rows if r["bits"].get(bit, 0.0) == val]
            st = slice_auc(sl)
            if not st:
                continue
            print(
                f"{bit:<18} {str(bool(val)):<6} {st['n']:>6} {st['pos']:>6.3f} "
                f"{fmt(st['auc']):>8} {fmt(st['ap']):>8} {st['mean']:>8.3f}"
            )


def fit_lr(X, y, C=1.0):
    if len(set(y.tolist())) < 2 or X.shape[0] < 80:
        return None
    scaler = StandardScaler()
    xs = scaler.fit_transform(X)
    clf = LogisticRegression(max_iter=400, solver="lbfgs", class_weight="balanced", C=C)
    clf.fit(xs, y)
    return scaler, clf


def predict(model, X):
    scaler, clf = model
    return clf.predict_proba(scaler.transform(X))[:, 1]


def interaction_matrix(metrics, bits):
    n, n_metrics = metrics.shape
    n_bits = bits.shape[1]
    inter = np.einsum("nd,nk->ndk", metrics, bits).reshape(n, n_metrics * n_bits)
    return np.hstack([metrics, inter])


def eval_block(name, rows, rng):
    print(f"\n===== B  {name}  n={len(rows)} =====")
    print(
        "split",
        dict(Counter(r["split"] for r in rows)),
        "label",
        dict(Counter(r["y"] for r in rows)),
        "projects",
        len({r["project"] for r in rows}),
    )
    train = [r for r in rows if r["split"] == "train"]
    test = [r for r in rows if r["split"] == "test"]
    if len(train) < 200 or len(test) < 80:
        print("split too small, abort", len(train), len(test))
        return
    bit_names = list(train[0]["bits"])
    train_metrics = np.stack([r["m"] for r in train])
    train_bits = np.stack([[r["bits"][b] for b in bit_names] for r in train])
    y_train = np.array([r["y"] for r in train], dtype=int)
    test_metrics = np.stack([r["m"] for r in test])
    test_bits = np.stack([[r["bits"][b] for b in bit_names] for r in test])
    y_test = np.array([r["y"] for r in test], dtype=int)
    perm_bits = rng.permutation(test_bits)

    specs = {
        "metrics_only": (train_metrics, test_metrics, test_metrics),
        "metrics_plus_bits": (
            np.hstack([train_metrics, train_bits]),
            np.hstack([test_metrics, test_bits]),
            np.hstack([test_metrics, perm_bits]),
        ),
        "metrics_modulate_no_bit_main": (
            interaction_matrix(train_metrics, train_bits),
            interaction_matrix(test_metrics, test_bits),
            interaction_matrix(test_metrics, perm_bits),
        ),
        "metrics_plus_bits_and_modulate": (
            np.hstack([train_metrics, train_bits, interaction_matrix(train_metrics, train_bits)[:, train_metrics.shape[1] :]]),
            np.hstack([test_metrics, test_bits, interaction_matrix(test_metrics, test_bits)[:, test_metrics.shape[1] :]]),
            np.hstack([test_metrics, perm_bits, interaction_matrix(test_metrics, perm_bits)[:, test_metrics.shape[1] :]]),
        ),
    }
    print(f"{'model':<32} {'auc':>7} {'ap':>7} {'perm_auc':>8} {'d_auc':>7} {'n_feat':>6}")
    for key, (x_train, x_test, x_perm) in specs.items():
        model = fit_lr(x_train, y_train)
        if model is None:
            print(key, "fit failed")
            continue
        pred = predict(model, x_test)
        pred_perm = predict(model, x_perm)
        a = auc(y_test, pred)
        apv = ap_score(y_test, pred)
        a_perm = auc(y_test, pred_perm)
        delta = None if a is None or a_perm is None else a - a_perm

        def fmt(v):
            return "NA" if v is None else f"{v:.4f}"

        print(f"{key:<32} {fmt(a):>7} {fmt(apv):>7} {fmt(a_perm):>8} {fmt(delta):>7} {x_train.shape[1]:>6}")


def main():
    rng = np.random.default_rng(20260918)
    print("loading FE")
    fe = load_fe()
    print("FE", len(fe), Counter(r["split"] for r in fe), Counter(r["y"] for r in fe))
    print("loading MLCQ")
    mlcq = load_mlcq()
    print("MLCQ", len(mlcq), Counter((r["smell"], r["split"]) for r in mlcq))

    fe_test = [r for r in fe if r["split"] == "test"]
    report_stratified(
        fe_test,
        "candidate_target_access_ratio",
        ["same_package", "accessed", "is_static", "is_override", "target_interface", "has_callees"],
        "FE test",
    )
    report_stratified(fe_test, "foreign_method_call_count", ["same_package", "accessed", "is_static"], "FE test")
    report_stratified(fe_test, "source_affinity_distance", ["same_package", "accessed"], "FE test")

    gc_test = [r for r in mlcq if r["smell"] == "god_class" and r["split"] == "test"]
    lm_test = [r for r in mlcq if r["smell"] == "long_method" and r["split"] == "test"]
    report_stratified(
        gc_test,
        "class_lcom",
        ["is_interface", "is_abstract", "is_enum", "public", "many_callees"],
        "MLCQ GC test",
    )
    report_stratified(
        lm_test,
        "entity_loc",
        ["is_static", "is_test", "is_override", "is_constructor", "has_callees"],
        "MLCQ LM test",
    )
    report_stratified(lm_test, "entity_cc", ["is_static", "is_test", "has_callees"], "MLCQ LM test")

    eval_block("FE feature_envy", fe, rng)
    eval_block("MLCQ god_class", [r for r in mlcq if r["smell"] == "god_class"], rng)
    eval_block("MLCQ long_method", [r for r in mlcq if r["smell"] == "long_method"], rng)
    print("\nDONE")


if __name__ == "__main__":
    main()
