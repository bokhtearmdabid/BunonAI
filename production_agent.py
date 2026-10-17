""" BunonAI - Production Agent (v0.1) """

import sys
import pandas as pd

# ---------- CONFIG (factory-specific rules) ----------
WEEKLY_OFF = 4        # 0=Mon ... 4=Fri  -> factory closes on Friday
SHIFT_HOURS = 8       # ASSUMPTION: 8-hour normal shift 
RATE_WINDOW = 3       # use last 3 *complete* days to measure current speed
AMBER_PCT = 5         # shortfall up to 5% of order qty = Amber

def working_days(after, upto):
    """Count working days in (after, upto]  - skips the weekly off day."""
    days = pd.date_range(after + pd.Timedelta(days=1), upto)
    return int(sum(d.weekday() != WEEKLY_OFF for d in days))

def analyse_order(df, order, as_of, all_lines):
    d = df[df.order_id == order.order_id]
    produced = int(d.actual_output.sum())
    remaining = int(order.order_qty - produced)

    # complete days = days when every line reported 
    lines_per_day = d.groupby("date").line.nunique()
    full_days = lines_per_day[lines_per_day == len(all_lines)].index
    recent = d[d.date.isin(sorted(full_days)[-RATE_WINDOW:])]
    daily_rate = recent.groupby("date").actual_output.sum().mean()
    daily_plan = recent.groupby("date").planned_output.sum().mean()

    # lines that have not reported yet for as_of day -> assume their recent average
    reported_today = set(d[d.date == as_of].line)
    pending_lines = [l for l in all_lines if l not in reported_today]
    pending_today = sum(recent[recent.line == l].actual_output.mean() for l in pending_lines)
    pending_today_plan = sum(recent[recent.line == l].planned_output.mean() for l in pending_lines)

    due = pd.Timestamp(order.due_date)
    days_left = working_days(as_of, due)
    projected = produced + pending_today + daily_rate * days_left
    best_case = produced + pending_today_plan + daily_plan * days_left   # even at 100% of plan
    shortfall = max(0, order.order_qty - projected)
    need_per_day = (remaining - pending_today) / days_left if days_left else float("inf")

    if shortfall == 0:
        status = "GREEN"
    elif best_case < order.order_qty:
        status = "CRITICAL"          # cannot be saved by just hitting plan
    elif shortfall <= AMBER_PCT / 100 * order.order_qty:
        status = "AMBER"
    else:
        status = "RED"

    # ---- why are we losing pieces? (estimate, last RATE_WINDOW full days) ----
    r = recent.copy()
    shift_min = SHIFT_HOURS * 60
    r["lost_downtime"] = r.downtime_min / shift_min * r.planned_output
    r["lost_absent"] = r.absent / r.operators_planned * r.planned_output
    gap = (r.planned_output - r.actual_output).sum()
    by_reason = (r.groupby(r.downtime_reason.fillna("-")).lost_downtime.sum()
                   .drop("-", errors="ignore").sort_values(ascending=False))
    lost_absent = r.lost_absent.sum()
    other = max(0, gap - r.lost_downtime.sum() - lost_absent)

    by_line = (r.groupby("line").apply(
        lambda x: x.actual_output.sum() / x.planned_output.sum() * 100)).round(1)

    # ---- overtime needed to close the gap ----
    pcs_per_factory_hour = daily_rate / SHIFT_HOURS
    ot_hours_per_day = (shortfall / days_left / pcs_per_factory_hour) if days_left and shortfall else 0

    return dict(order=order, produced=produced, remaining=remaining, daily_rate=daily_rate,
                daily_plan=daily_plan, pending_today=pending_today, pending_lines=pending_lines,
                days_left=days_left, projected=projected, best_case=best_case,
                shortfall=shortfall, need_per_day=need_per_day, status=status,
                gap=gap, by_reason=by_reason, lost_absent=lost_absent, other=other,
                by_line=by_line, ot_hours_per_day=ot_hours_per_day,
                efficiency=daily_rate / daily_plan * 100,
                full_days=sorted(full_days)[-RATE_WINDOW:])

def render(res, as_of):
    o = res["order"]
    icon = {"GREEN": "🟢", "AMBER": "🟡", "RED": "🔴", "CRITICAL": "🚨"}[res["status"]]
    L = []
    L.append(f"# {icon} {o.order_id} ({o.style}) - {res['status']}")
    L.append(f"_Report as of {as_of.date()} (Fridays off)_\n")
    L.append("## 1. Where we stand")
    L.append(f"- Order qty: **{o.order_qty:,}** | Due: **{o.due_date}** | Working days left: **{res['days_left']}**")
    L.append(f"- Produced so far: **{res['produced']:,}** | Remaining: **{res['remaining']:,}**")
    L.append(f"- Current speed (last {RATE_WINDOW} full days): **{res['daily_rate']:.0f} pcs/day** "
             f"= {res['efficiency']:.1f}% of plan ({res['daily_plan']:.0f}/day)")
    if res["pending_lines"]:
        L.append(f"- {', '.join(res['pending_lines'])} haven't reported for {as_of.date()} yet; "
                 f"assumed ~{res['pending_today']:.0f} pcs from them today")
    L.append("\n## 2. Forecast")
    L.append(f"- Need **{res['need_per_day']:.0f} pcs/day** to finish on time; running at **{res['daily_rate']:.0f}**")
    L.append(f"- Projected total by due date: **{res['projected']:,.0f}** -> shortfall **~{res['shortfall']:,.0f} pcs**")
    L.append(f"- Even at 100% of plan we'd reach only {res['best_case']:,.0f}"
             + (" -> **plan alone cannot save this order**" if res["best_case"] < o.order_qty else ""))
    L.append("\n## 3. Why we are losing pieces (estimate, same window)")
    L.append(f"Total gap vs plan: **{res['gap']:.0f} pcs**")
    for k, v in res["by_reason"].items():
        L.append(f"- Downtime - {k}: ~{v:.0f} pcs")
    L.append(f"- Absenteeism: ~{res['lost_absent']:.0f} pcs")
    L.append(f"- Slow speed / other: ~{res['other']:.0f} pcs")
    L.append("\nLine efficiency: " + ", ".join(f"{k} {v}%" for k, v in res["by_line"].items()))
    L.append("\n## 4. Options to recover (numbers only - manager decides)")
    if res["shortfall"] > 0:
        L.append(f"- **Overtime:** ~{res['ot_hours_per_day']:.1f} extra hrs/day on all lines for {res['days_left']} days closes the gap")
        top = res["by_reason"].index[0] if len(res["by_reason"]) else "downtime"
        L.append(f"- **Cut top loss:** '{top}' is the biggest downtime cause - fixing it is the cheapest pieces")
        L.append("- **Work Friday:** adds one more day (~"
                 f"{res['daily_rate']:.0f} pcs) - check labour cost/legal")
        L.append("- **Partial shipment / talk to buyer early:** negotiate before the deadline, not after")
    else:
        L.append("- No action needed. Keep monitoring.")
    return "\n".join(L)

def main(data="BunonAI_daily_production_dataset.csv", orders="orders.csv"):
    df = pd.read_csv(data, parse_dates=["date"])
    od = pd.read_csv(orders)
    as_of = df.date.max()
    all_lines = sorted(df.line.unique())
    out = []
    for o in od[od.status == "open"].itertuples():
        out.append(render(analyse_order(df, o, as_of, all_lines), as_of))
    return "\n\n---\n\n".join(out)

if __name__ == "__main__":
    print(main(*sys.argv[1:]))
