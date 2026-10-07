#!/usr/bin/env python3
"""Deterministic output checks for ArcturionEvals tasks. Stdlib only."""
import json
import re


def _flags(spec):
    return re.IGNORECASE if "i" in spec.get("flags", "") else 0


def _regex_hit(output, spec):
    return re.search(spec["pattern"], output, _flags(spec)) is not None


def _extract_numbers(text):
    out = []
    for raw in re.findall(r"-?\$?[\d,]+(?:\.\d+)?%?", text):
        cleaned = raw.replace(",", "").replace("$", "").rstrip("%")
        try:
            out.append(float(cleaned))
        except ValueError:
            pass
    return out


def _last_json_object(text):
    # Collect top-level balanced {...} spans left-to-right; return the last parseable one.
    # NOTE: matches bare objects only — array-wrapped JSON like [{...}] yields the inner
    # object(s), so json_field paths must address object keys, never array indices.
    result, depth, start = None, 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    result = json.loads(text[start:i + 1])
                except (json.JSONDecodeError, ValueError):
                    pass
                start = None
    return result


def run_check(output, spec):
    """Return True/False for one check spec against the task output."""
    t = spec["type"]
    if t == "regex":
        return _regex_hit(output, spec)
    if t == "not_regex":
        return not _regex_hit(output, spec)
    if t == "contains":
        hay, needle = output, spec["value"]
        if "i" in spec.get("flags", ""):
            hay, needle = hay.lower(), needle.lower()
        return needle in hay
    if t == "not_contains":
        hay, needle = output, spec["value"]
        if "i" in spec.get("flags", ""):
            hay, needle = hay.lower(), needle.lower()
        return needle not in hay
    if t == "number_close":
        expect = float(spec["expect"])
        tol = float(spec.get("tol", 0.01))
        return any(abs(v - expect) <= tol for v in _extract_numbers(output))
    if t == "max_words":
        return len(output.split()) <= int(spec["value"])
    if t == "min_words":
        return len(output.split()) >= int(spec["value"])
    if t == "json_field":
        obj = _last_json_object(output)
        if obj is None:
            return False
        node = obj
        for key in spec["path"].split("."):
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return False
        return node == spec["equals"]
    raise ValueError("unknown check type: %s" % t)


def deterministic_score(output, checks):
    """0-10 pass-fraction score, or None when the task has no deterministic checks."""
    if not checks:
        return None
    results = []
    for spec in checks:
        try:
            results.append(run_check(output, spec))
        except (re.error, KeyError, ValueError):
            results.append(False)  # a malformed check counts as failed, never crashes the run
    return round(10.0 * sum(results) / len(results), 2)
