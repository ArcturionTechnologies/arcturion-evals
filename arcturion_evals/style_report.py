#!/usr/bin/env python3
"""Response-style report. Stdlib only.

Reads the history JSONL files and prints per-suite and per-model means for the four style
dimensions the judge scores alongside quality (brevity, plain_english, summary_format,
decisiveness). Rows without a complete `style` object (no judge, judge failure, older rows)
are skipped, never counted as zero.

This is a REPORT, not a verdict. It lists REVIEW CANDIDATES (a model whose mean on a dimension
is below the threshold over enough samples) for a person to look at. It changes nothing and
always exits 0.

Usage:
  python3 -m arcturion_evals report [--threshold 7.0] [--min-samples 10]
"""
import json
import os

from .judge import STYLE_DIMENSIONS as DIMENSIONS

DEFAULT_THRESHOLD = 7.0
DEFAULT_MIN_SAMPLES = 10


def load_style_rows(history_dir):
    """Return [{suite, model, style: {...}}, ...] for real (non dry-run) rows with complete style data."""
    rows = []
    history_dir = str(history_dir)
    if not os.path.isdir(history_dir):
        return rows
    for fname in sorted(os.listdir(history_dir)):
        if not fname.endswith(".jsonl"):
            continue
        try:
            f = open(os.path.join(history_dir, fname))
        except OSError:
            continue
        with f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(r, dict) or r.get("dry_run"):
                    continue
                style = r.get("style")
                if not isinstance(style, dict) or not all(isinstance(style.get(d), (int, float)) for d in DIMENSIONS):
                    continue
                rows.append({"suite": r.get("suite", r.get("agent", "?")), "model": r.get("model") or "unlabeled",
                             "style": {d: float(style[d]) for d in DIMENSIONS}})
    return rows


def group_means(rows, key):
    """{group: {dimension: (mean, n)}}."""
    buckets = {}
    for r in rows:
        group = r.get(key) or "unknown"
        buckets.setdefault(group, {d: [] for d in DIMENSIONS})
        for d in DIMENSIONS:
            buckets[group][d].append(r["style"][d])
    return {g: {d: (round(sum(v) / len(v), 2), len(v)) for d, v in dims.items()} for g, dims in buckets.items()}


def find_candidates(means, threshold, min_samples):
    """[(group, dimension, mean, n), ...] where mean < threshold over >= min_samples."""
    return [(g, d, means[g][d][0], means[g][d][1]) for g in sorted(means) for d in DIMENSIONS
            if means[g][d][1] >= min_samples and means[g][d][0] < threshold]


def _print_table(title, means):
    print("\n%s" % title)
    header = "%-22s %10s %10s %10s %12s %14s" % ("GROUP", "BREVITY", "PLAIN_EN", "SUMMARY", "DECISIVE", "SAMPLES(min)")
    print(header)
    print("-" * len(header))
    for group in sorted(means):
        dims = means[group]
        print("%-22s %10.2f %10.2f %10.2f %12.2f %14d" % (
            group, dims["brevity"][0], dims["plain_english"][0], dims["summary_format"][0],
            dims["decisiveness"][0], min(n for _, n in dims.values())))


def run_report(history_dir, threshold=DEFAULT_THRESHOLD, min_samples=DEFAULT_MIN_SAMPLES):
    rows = load_style_rows(history_dir)
    print("ArcturionEvals - Response-Style Report")
    print("history dir: %s" % history_dir)
    print("%d record(s) with style data (dry-run and unjudged rows excluded)" % len(rows))
    if not rows:
        print("\nNo style data on file yet. Nothing to report. (Style scores need a judge.)")
        return 0
    _print_table("Per-suite style means (0-10):", group_means(rows, "suite"))
    by_model = group_means(rows, "model")
    _print_table("Per-model style means (0-10):", by_model)
    candidates = find_candidates(by_model, threshold, min_samples)
    print("\nReview screen (dimension mean < %.1f over >= %d samples):" % (threshold, min_samples))
    if not candidates:
        print("  none - no model/dimension crossed the threshold on current data.")
    else:
        for group, dim, mean, n in candidates:
            print("  REVIEW CANDIDATE: model=%s dimension=%s mean=%.2f n=%d" % (group, dim, mean, n))
        print("\n  A surfaced list, not a verdict. Decide what, if anything, to change.")
    return 0  # a report asserts only what it measured and never fails a build
