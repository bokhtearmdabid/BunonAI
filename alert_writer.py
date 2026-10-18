"""BunonAI - Step 3: Alert Writer"""
import os, sys, json
import pandas as pd
from dotenv import load_dotenv
import production_agent as pa

load_dotenv()                                   # reads GEMINI_API_KEY from .env file
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")

def collect_facts():
    df = pd.read_csv("BunonAI_daily_production_dataset.csv", parse_dates=["date"])
    orders = pd.read_csv("orders.csv")
    as_of, lines = df.date.max(), sorted(df.line.unique())
    facts = []
    for o in orders[orders.status == "open"].itertuples():
        r = pa.analyse_order(df, o, as_of, lines)
        facts.append({
            "order": o.order_id, "style": o.style, "status": r["status"],
            "order_qty": int(o.order_qty), "due_date": o.due_date,
            "produced": r["produced"], "remaining": r["remaining"],
            "working_days_left": r["days_left"],
            "needed_per_day": round(r["need_per_day"]), "current_per_day": round(r["daily_rate"]),
            "efficiency_pct": round(r["efficiency"], 1),
            "projected_total": round(r["projected"]), "shortfall": round(r["shortfall"]),
            "best_case_at_100pct_plan": round(r["best_case"]),
            "overtime_hours_per_day_all_lines": round(r["ot_hours_per_day"], 1),
            "friday_extra_pieces": round(r["daily_rate"]),
            "pieces_lost": {
                "absenteeism": round(r["lost_absent"]),
                "slow_speed_other": round(r["other"]),
                **{f"downtime_{k}": round(v) for k, v in r["by_reason"].items()}},
            "line_efficiency_pct": {k: float(v) for k, v in r["by_line"].items()},
        })
    return as_of.date().isoformat(), facts

def build_prompt(as_of, facts):
    return f"""You are the Production Agent of a small garment factory in Bangladesh.
Write a message for the factory manager based ONLY on the facts below.

RULES
- Use ONLY numbers that appear in FACTS. Never invent or recalculate numbers.
- Language: simple mix of Bangla and English (the way Bangladeshi managers talk). Keep technical words like overtime, shortfall, line in English.
- Style: short WhatsApp message, friendly but clear. Max 150 words for the alert.
- Structure: (1) one-line status with emoji, (2) the problem in 2-3 lines, (3) main reason for loss,
  (4) "Recovery plan" with 3 options, each with its trade-off (cost, risk), (5) end with ONE question asking what the manager decides.
- Do not make the decision for the manager. Do not promise results.
- The loss breakdown is an estimate; say "প্রায়" (approximately) when you mention it.

DATE: {as_of} (factory closed on Fridays)
FACTS:
{json.dumps(facts, indent=2, ensure_ascii=False)}
"""

def main():
    as_of, facts = collect_facts()
    prompt = build_prompt(as_of, facts)

    if "--dry-run" in sys.argv:
        print(prompt); return

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY not found. Put it in the .env file (see guide).")

    from google import genai
    client = genai.Client(api_key=key)

    if "--models" in sys.argv:
        for m in client.models.list(): print(m.name)
        return

    resp = client.models.generate_content(model=MODEL, contents=prompt)
    print(resp.text)

if __name__ == "__main__":
    main()
