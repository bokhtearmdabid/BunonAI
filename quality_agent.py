"""BunonAI - Quality Agent (v0.1)"""
import sys
import pandas as pd

# ---------- CONFIG ----------
TARGET_DHU = 3.0      # ASSUMPTION: replace with your buyer's / factory's inline DHU target
SPIKE_DHU = 4.0       # a single line-day above this = "spike"
RECENT_DAYS = 3       # last 3 full days = "recent"
MIN_SAMPLE = 10       # fewer events than this = "weak signal, small sample"

def dhu(x):
    return x.defects_found.sum() / x.actual_output.sum() * 100

def analyse_quality(df, order_id, order_qty, as_of, all_lines):
    d = df[df.order_id == order_id].copy()
    d["dhu"] = d.defects_found / d.actual_output * 100

    full = d.groupby("date").line.nunique()
    full_days = sorted(full[full == len(all_lines)].index)
    recent_days = full_days[-RECENT_DAYS:]
    earlier = d[~d.date.isin(recent_days)]
    recent = d[d.date.isin(recent_days)]

    order_dhu, recent_dhu = dhu(d), dhu(recent)
    earlier_dhu = dhu(earlier) if len(earlier) else float("nan")

    by_line_recent = recent.groupby("line").apply(dhu).round(2)
    by_line_all = d.groupby("line").apply(dhu).round(2)

    spikes = d[(d.dhu >= SPIKE_DHU) & (d.date >= as_of - pd.Timedelta(days=6))]
    spikes = spikes[["date", "line", "actual_output", "defects_found", "dhu", "downtime_reason", "absent"]]

    # weak signals: whole-history DHU by downtime reason (all orders, more data)
    a = df.copy()
    a["reason"] = a.downtime_reason.fillna("No downtime")
    sig = a.groupby("reason").apply(lambda x: pd.Series({"dhu": dhu(x), "n": len(x)})).round(2)
    base = sig.loc["No downtime", "dhu"] if "No downtime" in sig.index else float("nan")
    sig["vs_normal"] = (sig.dhu - base).round(2)
    sig = sig.drop("No downtime", errors="ignore").sort_values("vs_normal", ascending=False)

    # rework forecast on the remaining pieces
    produced = int(d.actual_output.sum())
    remaining = max(0, order_qty - produced)
    expected_defects = remaining * recent_dhu / 100

    if recent_dhu > TARGET_DHU * 1.2:
        status = "RED"
    elif recent_dhu > TARGET_DHU:
        status = "AMBER"
    else:
        status = "GREEN"

    return dict(order_id=order_id, status=status, order_dhu=order_dhu, recent_dhu=recent_dhu,
                earlier_dhu=earlier_dhu, by_line_recent=by_line_recent, by_line_all=by_line_all,
                spikes=spikes, signals=sig, remaining=remaining, expected_defects=expected_defects,
                recent_days=recent_days, earlier_n=int(earlier.date.nunique()), has_defect_type="defect_type" in df.columns)

def render(r):
    icon = {"GREEN": "🟢", "AMBER": "🟡", "RED": "🔴"}[r["status"]]
    L = [f"# {icon} Quality - {r['order_id']} - {r['status']}\n"]
    L.append("## 1. Overall quality")
    L.append(f"- Target DHU: **{TARGET_DHU:.1f}** (assumed) | Order so far: **{r['order_dhu']:.2f}**")
    trend = r["recent_dhu"] - r["earlier_dhu"]
    word = "worse" if trend > 0.1 else "better" if trend < -0.1 else "about the same"
    L.append(f"- Last {RECENT_DAYS} full days: **{r['recent_dhu']:.2f}** vs earlier {r['earlier_dhu']:.2f} -> {word}")
    if r["earlier_n"] < 3:
        L.append(f"  - ⚠️ 'Earlier' has only {r['earlier_n']} day(s) of data, so this trend is weak.")
    L.append("\n## 2. Line view (last days)")
    for line, v in r["by_line_recent"].items():
        flag = "⚠️ above target" if v > TARGET_DHU else "ok"
        L.append(f"- {line}: **{v}** ({flag}) | whole order: {r['by_line_all'][line]}")
    L.append("\n## 3. Spike days (last 7 days, DHU >= %.1f)" % SPIKE_DHU)
    if r["spikes"].empty:
        L.append("- None")
    for s in r["spikes"].itertuples():
        why = s.downtime_reason if isinstance(s.downtime_reason, str) else "no downtime"
        L.append(f"- {s.date.date()} {s.line}: DHU {s.dhu:.1f} ({s.defects_found} defects / {s.actual_output} pcs), {s.absent} absent, {why}")
    L.append("\n## 4. Possible links (weak signals - all orders)")
    for reason, s in r["signals"].iterrows():
        tag = "small sample - don't trust yet" if s.n < MIN_SAMPLE else "more data"
        if s.vs_normal > 0.3:
            L.append(f"- Days with **{reason}**: DHU {s.dhu} vs {s.dhu - s.vs_normal:.2f} normal (+{s.vs_normal}), n={int(s.n)} -> {tag}")
    L.append("\n## 5. Rework coming")
    L.append(f"- {r['remaining']:,} pcs still to make at recent DHU -> about **{r['expected_defects']:.0f} defects** to fix before shipment")
    L.append("- (Counts defects, not defective pieces; one piece can have several defects.)")
    L.append("\n## 6. What this agent cannot see yet")
    if not r["has_defect_type"]:
        L.append("- ❗ Data has **no defect type** (open seam, skipped stitch, stain...). Without it the agent can tell "
                 "*where/when* quality drops, but **not what the recurring problem is**. Add a `defect_type` column to the daily sheet.")
    return "\n".join(L)

def main(data="BunonAI_daily_production_dataset.csv", orders="orders.csv"):
    df = pd.read_csv(data, parse_dates=["date"])
    od = pd.read_csv(orders)
    as_of, lines = df.date.max(), sorted(df.line.unique())
    return "\n\n---\n\n".join(
        render(analyse_quality(df, o.order_id, o.order_qty, as_of, lines))
        for o in od[od.status == "open"].itertuples())

if __name__ == "__main__":
    print(main(*sys.argv[1:]))
