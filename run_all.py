"""
BunonAI - run_all.py

Usage:
  python run_all.py                       # as of the latest date in the data
  python run_all.py --as-of 2026-11-09    # pretend it is that day (to test history/comparison)
  python run_all.py --dry-run             # build everything, print the Gemini prompt, don't call Gemini
"""
import os, sys, json
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
import production_agent as pa
import quality_agent as qa
import supplier_agent as sa
import export_agent as ea

load_dotenv()
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")
DATA, ORDERS, MATERIALS = "BunonAI_daily_production_dataset.csv", "orders.csv", "materials.csv"

def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default

def previous_facts(as_of):
    """Find the most recent saved run before as_of."""
    root = Path("reports")
    if not root.exists(): return None
    days = sorted(d.name for d in root.iterdir() if d.is_dir() and d.name < as_of.date().isoformat()
                  and (d / "facts.json").exists())
    return json.loads((root / days[-1] / "facts.json").read_text()) if days else None

def run():
    df = pd.read_csv(DATA, parse_dates=["date"])
    as_of = pd.Timestamp(arg("--as-of")) if arg("--as-of") else df.date.max()
    df = df[df.date <= as_of]                      # never look past "today"
    orders, mat = pd.read_csv(ORDERS), pd.read_csv(MATERIALS)
    lines = sorted(df.line.unique())
    prev = previous_facts(as_of)
    out_dir = Path("reports") / as_of.date().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)

    parts = {"production": [], "quality": [], "supplier": [], "export": []}
    facts = {"as_of": as_of.date().isoformat(), "orders": []}

    for o in orders[orders.status == "open"].itertuples():
        p = pa.analyse_order(df, o, as_of, lines)
        q = qa.analyse_quality(df, o.order_id, o.order_qty, as_of, lines)
        a = ea.assess(df, o, mat, as_of, lines, ea.FINISH_BUFFER_WD)
        recent = df[(df.order_id == o.order_id) & (df.date > as_of - pd.Timedelta(days=6))]
        waiting = int(recent[recent.downtime_reason.isin(["Waiting for trims", "Waiting for fabric"])].downtime_min.sum())

        parts["production"].append(pa.render(p, as_of))
        parts["quality"].append(qa.render(q))
        parts["supplier"].append(sa.render(a["mrows"], o.order_id, waiting))
        parts["export"].append(ea.render(df, o, mat, as_of, lines))

        f = {
            "order": o.order_id, "style": o.style, "order_qty": int(o.order_qty),
            "ship_date": o.due_date, "sewing_deadline": a["prod_deadline"].date().isoformat(),
            "ship_risk": ea.risk_label(a, o), "projected_ready": round(a["projected"]),
            "shortfall": round(a["shortfall"]), "produced": p["produced"],
            "needed_per_day": round(p["need_per_day"]), "current_per_day": round(p["daily_rate"]),
            "efficiency_pct": round(p["efficiency"], 1),
            "main_loss_reasons_pieces": {"absenteeism": round(p["lost_absent"]), "slow_speed_other": round(p["other"]),
                                          **{f"downtime_{k}": round(v) for k, v in p["by_reason"].items()}},
            "quality": {"status": q["status"], "recent_dhu": round(q["recent_dhu"], 2), "target_dhu": qa.TARGET_DHU,
                        "expected_defects_in_remaining": round(q["expected_defects"])},
            "material_problems": [{"item": m["item"], "supplier": m["supplier"], "status": m["status"],
                                   "balance": int(m["balance"]), "unit": m["unit"], "note": m["note"]}
                                  for m in a["mrows"] if m["status"] in ("LATE", "WATCH")],
            "pieces_lost_waiting_material": round(a["blocked"]),
            "options_remaining_shortfall": {
                "2h_overtime_per_day": round(ea.scenario(a, o, 2, False)),
                "work_friday": round(ea.scenario(a, o, 0, True)),
                "friday_plus_2h_overtime": round(ea.scenario(a, o, 2, True)),
                "friday_plus_3h_overtime": round(ea.scenario(a, o, 3, True))},
        }
        if prev:
            old = next((x for x in prev["orders"] if x["order"] == o.order_id), None)
            if old:
                f["vs_previous_report"] = {"previous_date": prev["as_of"], "previous_shortfall": old["shortfall"],
                                           "shortfall_change": f["shortfall"] - old["shortfall"],
                                           "previous_risk": old["ship_risk"]}
        facts["orders"].append(f)

    for name, items in parts.items():
        (out_dir / f"{name}.md").write_text("\n\n---\n\n".join(items), encoding="utf-8")
    (out_dir / "facts.json").write_text(json.dumps(facts, indent=2, ensure_ascii=False), encoding="utf-8")
    return facts, out_dir

def build_prompt(facts):
    return f"""You are BunonAI, the virtual operations manager of a small garment factory in Bangladesh.
Write ONE daily brief for the factory manager, based ONLY on the FACTS below.

RULES
- Use ONLY numbers that appear in FACTS. Never invent, estimate or recalculate numbers.
- Language: natural mix of Bangla and English, like a Bangladeshi manager talks. Keep words like overtime, shortfall, line, supplier in English.
- Format: WhatsApp message, max 180 words, a few emoji, no tables, no markdown headings.
- Order: (1) one-line ship risk, (2) what changed since the previous report (only if "vs_previous_report" exists), (3) the 2-3 real reasons (production, material, quality),
  (4) TOP 3 ACTIONS for today, each with its trade-off, (5) ONE closing question to the manager.
- The loss reasons are estimates: say "প্রায়" when you quote them.
- Do not decide for the manager. Do not promise results. If a fact is missing, say it is missing; never guess.

FACTS:
{json.dumps(facts, indent=2, ensure_ascii=False)}
"""

def fallback_brief(facts):
    """Plain brief with no AI - so the system still works without internet / API key."""
    L = [f"BunonAI daily brief - {facts['as_of']}"]
    for f in facts["orders"]:
        L.append(f"\n{f['order']} ({f['style']}): ship risk {f['ship_risk']}")
        L.append(f"- Ready by sewing deadline ({f['sewing_deadline']}): ~{f['projected_ready']:,} of {f['order_qty']:,} -> shortfall ~{f['shortfall']:,}")
        L.append(f"- Need {f['needed_per_day']}/day, running {f['current_per_day']}/day ({f['efficiency_pct']}% of plan)")
        for m in f["material_problems"]:
            L.append(f"- Material: {m['item']} ({m['supplier']}) {m['status']} - balance {m['balance']:,} {m['unit']}")
        L.append(f"- Quality: DHU {f['quality']['recent_dhu']} (target {f['quality']['target_dhu']})")
        if "vs_previous_report" in f:
            v = f["vs_previous_report"]
            L.append(f"- Since {v['previous_date']}: shortfall changed by {v['shortfall_change']:+,}")
        o = f["options_remaining_shortfall"]
        L.append(f"- Options (remaining shortfall): 2h OT {o['2h_overtime_per_day']:,} | Friday {o['work_friday']:,} | "
                 f"Friday+2h OT {o['friday_plus_2h_overtime']:,} | Friday+3h OT {o['friday_plus_3h_overtime']:,}")
    return "\n".join(L)

def main():
    facts, out_dir = run()
    prompt = build_prompt(facts)
    (out_dir / "gemini_prompt.txt").write_text(prompt, encoding="utf-8")
    print(f"✅ Reports saved in {out_dir}/")

    if "--dry-run" in sys.argv:
        print("\n----- PROMPT (not sent) -----\n" + prompt); return

    key = os.getenv("GEMINI_API_KEY")
    brief = None
    if key:
        try:
            from google import genai
            client = genai.Client(api_key=key)     
            brief = client.models.generate_content(model=MODEL, contents=prompt).text
            source = f"Gemini ({MODEL})"
        except Exception as e:
            print(f"⚠️ Gemini call failed ({type(e).__name__}): {str(e)[:200]}\n   -> using plain brief instead.")
    else:
        print("⚠️ No GEMINI_API_KEY in .env -> using plain brief.")
    if brief is None:
        brief, source = fallback_brief(facts), "plain template (no AI)"
    (out_dir / "brief.txt").write_text(brief, encoding="utf-8")
    print(f"\n----- DAILY BRIEF  [{source}] -----\n{brief}")

if __name__ == "__main__":
    main()
