"""BunonAI - Export Deadline Agent (v0.1)"""
import sys
import pandas as pd
import production_agent as pa
import quality_agent as qa
import supplier_agent as sa

FINISH_BUFFER_WD = 1     # sewing must finish 1 working day before ship date (final inspection + packing)
REWORK_MIN_PER_DEFECT = 6  # minutes to repair one defect
OT_REALISTIC_MAX = 3     # more than this many OT hours/day = flagged "hard to sustain"

def back_working_days(date, n):
    d = date
    while n > 0:
        d -= pd.Timedelta(days=1)
        if d.weekday() != pa.WEEKLY_OFF:
            n -= 1
    return d

def assess(df, o, mat, as_of, lines, buffer_wd):
    due = pd.Timestamp(o.due_date)
    prod_deadline = back_working_days(due, buffer_wd)
    p = pa.analyse_order(df, o._replace(due_date=prod_deadline.date().isoformat()), as_of, lines)

    # ---- material cap: can sewing keep running until missing material arrives? ----
    mrows = sa.analyse_materials(mat, o.order_id, as_of)
    blocked, blocker = 0.0, None
    hard_cap = None
    for m in mrows:
        if m["stage"] != "sewing" or m["balance"] <= 0:
            continue
        cap = max(0, m["received"] / m["per_piece"] - p["produced"])     # pieces we can still sew
        if m["eta_eff"] > prod_deadline:                                 # arrives after sewing must end
            hard_cap = cap if hard_cap is None else min(hard_cap, cap)
            blocker = m["item"]
            continue
        days_before = pa.working_days(as_of, m["eta_eff"] - pd.Timedelta(days=1))
        output_before = p["pending_today"] + p["daily_rate"] * days_before
        lost = max(0, output_before - cap)
        if lost > blocked:
            blocked, blocker = lost, m["item"]
    projected = p["projected"] - blocked
    if hard_cap is not None:
        projected = min(projected, p["produced"] + hard_cap)
    shortfall = max(0, o.order_qty - projected)

    pcs_hr = p["daily_rate"] / pa.SHIFT_HOURS
    return dict(p=p, due=due, prod_deadline=prod_deadline, mrows=mrows, blocked=blocked, blocker=blocker,
                hard_cap=hard_cap, projected=projected, shortfall=shortfall, pcs_hr=pcs_hr,
                best_case=p["best_case"] - blocked)

def scenario(a, o, ot_hours, friday):
    p = a["p"]
    days = p["days_left"] + (1 if friday else 0)
    proj = p["produced"] + p["pending_today"] + (p["daily_rate"] + ot_hours * a["pcs_hr"]) * days - a["blocked"]
    if a["hard_cap"] is not None:
        proj = min(proj, p["produced"] + a["hard_cap"])
    return max(0, o.order_qty - proj)

def risk_label(a, o):
    s = a["shortfall"]
    if s <= 0: return "🟢 LOW"
    if s <= 0.05 * o.order_qty: return "🟡 MEDIUM"
    if a["best_case"] < o.order_qty: return "🚨 VERY HIGH"
    return "🔴 HIGH"

def render(df, o, mat, as_of, lines):
    a = assess(df, o, mat, as_of, lines, FINISH_BUFFER_WD)
    a0 = assess(df, o, mat, as_of, lines, 0)
    q = qa.analyse_quality(df, o.order_id, o.order_qty, as_of, lines)
    p = a["p"]
    rework_hr = q["expected_defects"] * REWORK_MIN_PER_DEFECT / 60
    L = [f"# 🚢 Export Deadline - {o.order_id} ({o.style})",
         f"_As of {as_of.date()} | Ship date **{a['due'].date()}** | Sewing must finish **{a['prod_deadline'].date()}** "
         f"(ship date minus {FINISH_BUFFER_WD} working day for inspection/packing)_\n",
         f"## Ship risk: {risk_label(a, o)}\n",
         "## Why (3 agents combined)",
         f"- **Production:** {p['produced']:,} of {o.order_qty:,} done; running {p['daily_rate']:.0f}/day "
         f"({p['efficiency']:.0f}% of plan); {p['days_left']} working days left before sewing deadline",
         f"- **Supplier:** " + ("; ".join(f"{m['item']} {m['status']}" for m in a["mrows"] if m["status"] in ("LATE", "WATCH"))
                                or "no material problem"),
         f"- **Material effect:** ~{a['blocked']:.0f} pcs of sewing capacity lost waiting for **{a['blocker']}**"
         if a["blocker"] else "- **Material effect:** none",
         f"- **Quality:** DHU {q['recent_dhu']:.2f} -> ~{q['expected_defects']:.0f} defects to repair, "
         f"about {rework_hr:.0f} labour-hours of rework",
         f"\n**Forecast:** {a['projected']:,.0f} pcs ready vs {o.order_qty:,} ordered -> shortfall **~{a['shortfall']:,.0f} pcs** "
         f"({a['shortfall'] / o.order_qty * 100:.0f}%)",
         f"_(Sensitivity: with no inspection/packing buffer the shortfall would be ~{a0['shortfall']:,.0f} pcs.)_",
         "\n## What each recovery option achieves (remaining shortfall, pcs)",
         "| Option | Remaining shortfall |", "|---|---|",
         f"| Do nothing | {a['shortfall']:,.0f} |"]
    for ot, fri, name in [(2, False, "2 hrs overtime/day, all lines"), (0, True, "Work Friday"),
                          (2, True, "Friday + 2 hrs overtime/day"), (3, True, "Friday + 3 hrs overtime/day")]:
        L.append(f"| {name} | {scenario(a, o, ot, fri):,.0f} |")
    need_ot = a["shortfall"] / p["days_left"] / a["pcs_hr"] if p["days_left"] else 0
    L.append(f"\nOvertime alone would need ~{need_ot:.1f} hrs/day"
             + (" - **too heavy to sustain**." if need_ot > OT_REALISTIC_MAX else "."))
    L.append("\n## Suggested actions (manager decides)")
    n = 1
    if a["blocker"]:
        L.append(f"{n}. **Chase {a['blocker']} today** - ask for a part-shipment now (draft in Supplier report)."); n += 1
    L.append(f"{n}. **Decide overtime / Friday work today** - every day of delay removes ~{p['daily_rate']:.0f} pcs of options."); n += 1
    if a["shortfall"] > 0:
        L.append(f"{n}. **Talk to the buyer now** about a partial shipment (~{max(0, o.order_qty - a['shortfall']):,.0f} pcs on time, "
                 f"balance later) or a later date. Early notice protects the relationship."); n += 1
    return "\n".join(L)

def main(data="BunonAI_daily_production_dataset.csv", orders="orders.csv", materials="materials.csv"):
    df = pd.read_csv(data, parse_dates=["date"])
    od, mat = pd.read_csv(orders), pd.read_csv(materials)
    as_of, lines = df.date.max(), sorted(df.line.unique())
    return "\n\n---\n\n".join(render(df, o, mat, as_of, lines) for o in od[od.status == "open"].itertuples())

if __name__ == "__main__":
    print(main(*sys.argv[1:]))
