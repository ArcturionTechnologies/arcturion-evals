#!/usr/bin/env python3
"""Trend dashboard and weekly digest line. Stdlib only."""
import datetime as dt
import html
import json
import os
import subprocess


def load_history(history_dir):
    """Return [{date, suite, task_id, final}, ...] from all history JSONL files (dry runs excluded)."""
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
            continue  # one unreadable file must never kill dashboard regeneration
        with f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("dry_run"):
                        continue
                    rows.append({"date": r["ts"][:10], "suite": r.get("suite", r.get("agent", "?")),
                                 "task_id": r.get("task_id", "?"), "final": float(r.get("final", 0.0))})
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
    return rows


def daily_means(rows):
    """{suite: [(date, mean), ...] date-sorted}, plus an 'OVERALL' aggregate."""
    buckets = {}
    for r in rows:
        buckets.setdefault(r["suite"], {}).setdefault(r["date"], []).append(r["final"])
        buckets.setdefault("OVERALL", {}).setdefault(r["date"], []).append(r["final"])
    return {name: [(d, round(sum(v) / len(v), 2)) for d, v in sorted(days.items())]
            for name, days in buckets.items()}


def _sparkline(series, width=220, height=36):
    if not series:
        return ""
    vals = [v for _, v in series]
    pts = []
    for i, v in enumerate(vals):
        x = 4 + (width - 8) * (i / max(1, len(vals) - 1))
        y = height - 4 - (height - 8) * (v / 10.0)
        pts.append("%.1f,%.1f" % (x, y))
    return ('<svg width="%d" height="%d" viewBox="0 0 %d %d">'
            '<polyline fill="none" stroke="var(--accent)" stroke-width="2" points="%s"/>'
            '</svg>') % (width, height, width, height, " ".join(pts))


def generate(cfg):
    rows = load_history(cfg.history_dir)
    means = daily_means(rows)
    overall = means.get("OVERALL", [])
    names = sorted(n for n in means if n != "OVERALL")
    cards = []
    for name in names:
        series = means[name]
        latest = series[-1][1] if series else 0.0
        prev = series[-2][1] if len(series) > 1 else latest
        arrow = "&uarr;" if latest > prev + 0.2 else ("&darr;" if latest < prev - 0.2 else "&rarr;")
        cards.append('<div class="card"><div class="name">%s</div><div class="score">%.1f <span class="arrow">%s</span></div>%s</div>'
                     % (html.escape(name), latest, arrow, _sparkline(series)))
    latest_overall = overall[-1][1] if overall else 0.0
    page = """<!doctype html><html><head><meta charset="utf-8">
<title>ArcturionEvals - Quality</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0b0e1a;--fg:#e8eaf2;--dim:#8890a8;--card:#151a2e;--accent:#7aa2ff}
@media (prefers-color-scheme: light){:root{--bg:#f5f6fa;--fg:#1a1d2b;--dim:#5a617a;--card:#fff;--accent:#3b63d8}}
body{margin:0;padding:28px;font:15px/1.5 -apple-system,system-ui,sans-serif;background:var(--bg);color:var(--fg)}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--dim);margin-bottom:20px}
.big{font-size:44px;font-weight:700;margin:8px 0 24px}.big small{font-size:16px;color:var(--dim);font-weight:400}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px}
.card{background:var(--card);border-radius:12px;padding:14px 16px;box-shadow:0 1px 4px rgba(0,0,0,.25)}
.name{color:var(--dim);font-size:12px;letter-spacing:.08em}
.score{font-size:26px;font-weight:600;margin:2px 0 6px}.arrow{color:var(--accent);font-size:18px}
</style></head><body>
<h1>ArcturionEvals - Quality</h1>
<div class="sub">Task scores (0-10) &middot; generated %s &middot; %d results on file</div>
<div class="big">%.1f<small> /10 overall</small>%s</div>
<div class="grid">%s</div>
</body></html>""" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M"), len(rows),
                     latest_overall, _sparkline(overall, 320, 48), "\n".join(cards))
    path = cfg.dashboard_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page)
    return path


def digest(cfg, send=False, today=None):
    """One plain-English weekly line: this week's mean vs last week's, movers, weakest task."""
    rows = load_history(cfg.history_dir)
    today = today or dt.date.today()

    def within(r, lo, hi):
        return lo <= (today - dt.date.fromisoformat(r["date"])).days < hi
    this_wk = [r for r in rows if within(r, 0, 7)]
    last_wk = [r for r in rows if within(r, 7, 14)]
    if not this_wk:
        line = "ArcturionEvals: no eval runs recorded this week."
    else:
        def mean(rs):
            return sum(r["final"] for r in rs) / len(rs)
        movers = []
        for name in sorted({r["suite"] for r in this_wk}):
            now = mean([r for r in this_wk if r["suite"] == name])
            prev_rows = [r for r in last_wk if r["suite"] == name]
            if prev_rows:
                d = now - mean(prev_rows)
                movers.append((name, now, "up" if d > 0.5 else ("down" if d < -0.5 else "flat")))
            else:
                movers.append((name, now, "new"))
        worst = min(this_wk, key=lambda r: r["final"])
        parts = " | ".join("%s %.1f %s" % m for m in movers)
        line = "ArcturionEvals week: overall %.1f/10%s | %s | weakest: %s/%s (%.1f)" % (
            mean(this_wk), (" (was %.1f)" % mean(last_wk)) if last_wk else "", parts,
            worst["suite"], worst["task_id"], worst["final"])
    if send:
        notify(cfg, line)
    return line


def notify(cfg, text):
    """Hand a line to the configured notify command on stdin. Returns True if it was delivered to the command."""
    if not cfg.notify_cmd:
        print("no notify command configured (EVALS_NOTIFY_CMD); line not sent")
        return False
    try:
        proc = subprocess.run(cfg.notify_cmd, input=text, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("notify command failed (line still on disk): %s" % type(exc).__name__)
        return False
    if proc.returncode != 0:
        print("notify command exited %s (line still on disk)" % proc.returncode)
        return False
    return True
