"""BunonAI - Data Validator (v0.1)  -  the guard at the gate."""
import sys
import pandas as pd
import production_agent as pa

REQUIRED = ["date", "line", "order_id", "planned_output", "actual_output", "operators_planned",
            "absent", "downtime_min", "downtime_reason", "defects_found"]
NUMERIC = ["planned_output", "actual_output", "operators_planned", "absent", "downtime_min", "defects_found"]
MAX_OVER_PLAN_WARN, MAX_OVER_PLAN_ERROR = 1.5, 3.0   # actual / planned
DHU_WARN = 15.0                                        # defects per 100 pcs that looks suspicious
GAP_LOOKBACK_DAYS = 14

def is_workday(d):
    return d.weekday() != pa.WEEKLY_OFF

def workdays_between(after, upto):
    """Working days in (after, upto]."""
    return [d for d in pd.date_range(after + pd.Timedelta(days=1), upto) if is_workday(d)]

def last_workday(d):
    while not is_workday(d):
        d -= pd.Timedelta(days=1)
    return d

def validate(path="BunonAI_daily_production_dataset.csv", today=None, orders_path="orders.csv"):
    today = pd.Timestamp(today).normalize() if today is not None else pd.Timestamp.today().normalize()
    issues, blocked = [], False

    def add(level, code, msg, fix, rows=None):
        issues.append(dict(level=level, code=code, msg=msg, fix=fix, rows=sorted(set(rows))[:8] if rows is not None else []))

    # ---------- 1. can we even read it? ----------
    try:
        raw = pd.read_csv(path)
    except Exception as e:
        add("BLOCK", "file", f"Cannot read '{path}' ({type(e).__name__}).", "Check the file name/location and that it is a CSV.")
        return _result(issues, None, True, 0, 0)
    missing = [c for c in REQUIRED if c not in raw.columns]
    if missing:
        add("BLOCK", "columns", f"Missing column(s): {', '.join(missing)}.", "Restore the column names exactly as in the template.")
        return _result(issues, None, True, len(raw), 0)
    if raw.empty:
        add("BLOCK", "empty", "File has no data rows.", "Enter at least today's production rows.")
        return _result(issues, None, True, 0, 0)

    df = raw.copy()
    df["_row"] = df.index + 2                       # row number as the supervisor sees it in Excel
    bad = pd.Series(False, index=df.index)          # rows to quarantine

    def flag(mask, level, code, msg, fix, quarantine):
        nonlocal bad
        if mask.any():
            add(level, code, f"{int(mask.sum())} row(s): {msg}", fix, df.loc[mask, "_row"])
            if quarantine:
                bad |= mask

    # ---------- 2. types ----------
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    flag(df["date"].isna(), "ERROR", "date", "date missing or not a valid date.", "Use YYYY-MM-DD, e.g. 2026-11-10.", True)
    for c in NUMERIC:
        before = df[c].isna() | df[c].astype(str).str.strip().eq("")
        df[c] = pd.to_numeric(df[c], errors="coerce")
        flag(df[c].isna(), "ERROR", f"num_{c}", f"'{c}' is empty or not a number.", "Enter a number (0 if none).", True)
    for c in ["line", "order_id"]:
        flag(df[c].isna() | df[c].astype(str).str.strip().eq(""), "ERROR", f"blank_{c}", f"'{c}' is blank.", f"Fill in {c}.", True)

    # ---------- 3. impossible / suspicious values ----------
    ok = ~bad
    flag(ok & (df[NUMERIC] < 0).any(axis=1), "ERROR", "negative", "negative number.", "Quantities cannot be below 0.", True)
    flag(ok & (df.absent > df.operators_planned), "ERROR", "absent_gt_planned", "absent is more than planned operators.", "Check absent / operators_planned.", True)
    flag(ok & (df.downtime_min > pa.SHIFT_HOURS * 60), "ERROR", "downtime_gt_shift",
         f"downtime is longer than a {pa.SHIFT_HOURS}-hour shift.", "Downtime is in MINUTES per day.", True)
    ratio = df.actual_output / df.planned_output.where(df.planned_output > 0)
    flag(ok & (ratio > MAX_OVER_PLAN_ERROR), "ERROR", "output_way_over_plan",
         f"actual output is more than {MAX_OVER_PLAN_ERROR:g}x plan (typo?).", "Check for an extra zero or a swapped column.", True)
    ok = ~bad
    flag(ok & (ratio > MAX_OVER_PLAN_WARN) & (ratio <= MAX_OVER_PLAN_ERROR), "WARN", "output_over_plan",
         f"actual output is more than {MAX_OVER_PLAN_WARN:g}x plan.", "Confirm the number.", False)
    flag(ok & (df.planned_output == 0), "WARN", "plan_zero", "planned output is 0.", "Is the plan missing?", False)
    flag(ok & (df.actual_output == 0) & (df.planned_output > 0), "WARN", "zero_output", "line reported zero output.", "Real stoppage, or not entered yet?", False)
    flag(ok & (df.operators_planned - df.absent < 0), "WARN", "staff", "staffing numbers look wrong.", "Check operators.", False)
    has_reason = df.downtime_reason.notna() & df.downtime_reason.astype(str).str.strip().ne("")
    flag(ok & (df.downtime_min > 0) & ~has_reason, "WARN", "no_reason", "downtime minutes entered but no reason.", "Add the downtime reason - the agent needs it to explain losses.", False)
    flag(ok & (df.downtime_min == 0) & has_reason, "WARN", "reason_no_minutes", "downtime reason entered but 0 minutes.", "Add the minutes or remove the reason.", False)
    dhu = df.defects_found / df.actual_output.where(df.actual_output > 0) * 100
    flag(ok & (dhu > DHU_WARN), "WARN", "dhu_high", f"defects above {DHU_WARN:g} per 100 pieces.", "Confirm, or it may be a typo.", False)

    # ---------- 4. duplicates (same day + line + order) ----------
    dup = df.duplicated(subset=["date", "line", "order_id"], keep="first") & ~bad
    flag(dup, "ERROR", "duplicate", "duplicate of an earlier row (same date, line, order). Kept the first.", "Delete the repeated row.", True)

    clean = df[~bad].drop(columns="_row").copy()
    if clean.empty:
        add("BLOCK", "all_bad", "No usable rows left after removing errors.", "Fix the errors above.")
        return _result(issues, None, True, len(raw), len(raw))

    # ---------- 5. calendar / completeness ----------
    dmax = clean.date.max()
    future = clean.date > today
    if future.any():
        add("WARN", "future", f"{int(future.sum())} row(s) are dated after today ({today.date()}). Test/sample data?",
            "Check the date column. Staleness check is skipped for these.", df.loc[~bad & (df.date > today), "_row"])
    else:
        expected = last_workday(today)
        late = workdays_between(dmax, expected)
        if late:
            blocked = True
            ds = ", ".join(d.strftime("%d %b") for d in late[:5])
            add("BLOCK", "stale", f"No data since {dmax.date()}: {len(late)} working day(s) missing ({ds}).",
                "Enter the missing days. Until then any forecast would be out of date.")
    window = clean[clean.date > dmax - pd.Timedelta(days=GAP_LOOKBACK_DAYS)]
    have = set(window.date.dt.normalize())
    gaps = [d for d in pd.date_range(window.date.min(), dmax) if is_workday(d) and d not in have]
    if gaps:
        add("WARN", "gaps", f"No data for working day(s): {', '.join(d.strftime('%d %b') for d in gaps[:6])}.",
            "Enter them, or confirm the factory was closed.")
    lines_seen = set(window.line)
    reported = set(clean[clean.date == dmax].line)
    if lines_seen - reported:
        add("WARN", "partial_day", f"On {dmax.date()} these lines have not reported: {', '.join(sorted(lines_seen - reported))}.",
            "Wait for them, or enter their numbers. The agents estimate them meanwhile.")

    # ---------- 6. open orders ----------
    try:
        od = pd.read_csv(orders_path)
        for oid in od[od.status == "open"].order_id:
            if oid not in set(clean.order_id):
                add("WARN", "order_no_data", f"Open order {oid} has no production rows.", "Is the order id spelled the same in both files?")
    except Exception:
        add("WARN", "orders_file", f"Could not read {orders_path}.", "Check the orders file.")

    return _result(issues, clean, blocked, len(raw), int(bad.sum()))

def _result(issues, clean, blocked, n_rows, n_removed):
    status = "BLOCKED" if blocked or any(i["level"] == "BLOCK" for i in issues) else ("WARN" if issues else "OK")
    return dict(status=status, issues=issues, df=clean, rows=n_rows, removed=n_removed)

def render(r):
    icon = {"OK": "✅", "WARN": "⚠️", "BLOCKED": "⛔"}[r["status"]]
    head = {"OK": "Data looks good. Agents can run.",
            "WARN": "Agents can run, but numbers may be less reliable.",
            "BLOCKED": "Agents will NOT run until this is fixed."}[r["status"]]
    L = [f"# {icon} Data check: {r['status']}", head,
         f"\nRows read: {r['rows']} | removed as unusable: {r['removed']} | issues: {len(r['issues'])}\n"]
    if r["issues"]:
        L += ["| Level | Problem | Rows | How to fix |", "|---|---|---|---|"]
        for i in sorted(r["issues"], key=lambda x: {"BLOCK": 0, "ERROR": 1, "WARN": 2}[x["level"]]):
            L.append(f"| {i['level']} | {i['msg']} | {', '.join(map(str, i['rows'])) or '-'} | {i['fix']} |")
        L.append("\nRow numbers match the row numbers in Excel (row 1 = header).")
    return "\n".join(L)

def summary(r, limit=4):
    """Short lines for the brief."""
    order = {"BLOCK": 0, "ERROR": 1, "WARN": 2}
    return [i["msg"] for i in sorted(r["issues"], key=lambda x: order[x["level"]])][:limit]

if __name__ == "__main__":
    args = sys.argv[1:]
    today = args[args.index("--today") + 1] if "--today" in args else None
    files = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--today")]
    res = validate(files[0] if files else "BunonAI_daily_production_dataset.csv", today)
    print(render(res))
    sys.exit(2 if res["status"] == "BLOCKED" else 0)
