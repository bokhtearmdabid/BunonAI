"""BunonAI - Supplier Agent (v0.1)"""
import sys
import pandas as pd
import production_agent as pa

BUFFER_DAYS = 1   # material should arrive at least 1 day before it is needed

def next_working_day(d):
    while d.weekday() == pa.WEEKLY_OFF:
        d += pd.Timedelta(days=1)
    return d

def analyse_materials(mat, order_id, as_of):
    rows = []
    for m in mat[mat.order_id == order_id].itertuples():
        balance = m.qty_ordered - m.qty_received
        promised = pd.Timestamp(m.promised_date)
        needed = pd.Timestamp(m.needed_by)
        eta = pd.Timestamp(m.latest_eta) if isinstance(m.latest_eta, str) else promised
        eta_eff = next_working_day(eta)          # a Friday delivery really means next working day
        if balance <= 0:
            status, note = "RECEIVED", "complete"
        else:
            days_overdue = max(0, (as_of - promised).days)
            gap = (needed - eta_eff).days        # days of cushion (negative = arrives too late)
            if eta_eff > needed:
                status, note = "LATE", f"ETA {eta_eff.date()} is {-gap} day(s) AFTER it is needed ({needed.date()})"
            elif gap < BUFFER_DAYS:
                status, note = "WATCH", f"ETA {eta_eff.date()} leaves only {gap} day(s) cushion"
            else:
                status, note = "OK", f"ETA {eta_eff.date()}, {gap} day(s) cushion"
            if days_overdue and status != "LATE":
                note += f"; already {days_overdue} day(s) past promised date"
            elif days_overdue:
                note += f"; also {days_overdue} day(s) past promised date"
        rows.append(dict(item=m.item, stage=m.stage, supplier=m.supplier, ordered=m.qty_ordered,
                         received=m.qty_received, balance=balance, unit=m.unit, per_piece=m.per_piece,
                         promised=promised, eta_eff=eta_eff, needed=needed, status=status, note=note))
    return rows

def followup_draft(r, order_id):
    return (f"Dear {r['supplier']} team,\n"
            f"For {order_id}: {r['item']} - ordered {r['ordered']:,} {r['unit']}, received {r['received']:,}, "
            f"balance {r['balance']:,} {r['unit']} was promised on {r['promised'].date()}.\n"
            f"We need it by {r['needed'].date()} to protect our shipment. "
            f"Please confirm today: exact delivery date and quantity, and whether a part-shipment can come earlier.\n"
            f"Thank you.")

def render(rows, order_id, waiting_min):
    icon = {"RECEIVED": "🟢", "OK": "🟢", "WATCH": "🟡", "LATE": "🔴"}
    L = [f"# Supplier check - {order_id}\n", "| Item | Supplier | Received / Ordered | Status | Note |", "|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['item']} | {r['supplier']} | {r['received']:,} / {r['ordered']:,} {r['unit']} | "
                 f"{icon[r['status']]} {r['status']} | {r['note']} |")
    L.append(f"\nSewing lines lost **{waiting_min} min** to 'Waiting for trims/fabric' in the last 7 days (from production data).")
    bad = [r for r in rows if r["status"] in ("LATE", "WATCH")]
    if bad:
        L.append("\n## Follow-up drafts (copy, check, send)")
        for r in bad:
            L.append(f"\n**To {r['supplier']}:**\n```\n{followup_draft(r, order_id)}\n```")
    return "\n".join(L)

def main(data="BunonAI_daily_production_dataset.csv", orders="orders.csv", materials="materials.csv"):
    df = pd.read_csv(data, parse_dates=["date"])
    od, mat = pd.read_csv(orders), pd.read_csv(materials)
    as_of = df.date.max()
    out = []
    for o in od[od.status == "open"].itertuples():
        recent = df[(df.order_id == o.order_id) & (df.date > as_of - pd.Timedelta(days=6))]
        waiting = int(recent[recent.downtime_reason.isin(["Waiting for trims", "Waiting for fabric"])].downtime_min.sum())
        out.append(render(analyse_materials(mat, o.order_id, as_of), o.order_id, waiting))
    return "\n\n---\n\n".join(out)

if __name__ == "__main__":
    print(main(*sys.argv[1:]))
