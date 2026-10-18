"""BunonAI - WhatsApp sender (v0.1)  -  Meta WhatsApp Business Cloud API (official)"""
import json, os, re, sys, time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v25.0")
TEMPLATE_DAILY = os.getenv("WHATSAPP_TEMPLATE_DAILY", "bunon_daily_brief")
TEMPLATE_ALERT = os.getenv("WHATSAPP_TEMPLATE_ALERT", "bunon_data_alert")
LANG = os.getenv("WHATSAPP_LANG", "en")

OPTION_NAMES = {"2h_overtime_per_day": "2 hours overtime per day", "work_friday": "Working on Friday",
                "friday_plus_2h_overtime": "Friday work plus 2 hours overtime per day",
                "friday_plus_3h_overtime": "Friday work plus 3 hours overtime per day"}
ERROR_HINTS = {190: "Access token is invalid or expired. Create a new token.",
               131030: "This number is not in your allowed recipient list (test mode: add and verify it, max 5).",
               131047: "Outside the 24-hour window: only approved templates can be sent.",
               132000: "Number of template variables does not match the template.",
               132001: "Template name/language not found or not approved yet. Check WhatsApp Manager.",
               132018: "A variable has a line break, tab, or too many spaces."}

# ---------- text rules (Meta rejects variables with line breaks, tabs, >4 spaces, or empty text) ----------
def clean(text, limit=160):
    t = re.sub(r"\s+", " ", str(text)).strip()
    if len(t) > limit:
        t = t[:limit - 3].rstrip() + "..."
    return t or "-"

def normalize_phone(raw):
    d = re.sub(r"\D", "", str(raw))
    if d.startswith("00"):
        d = d[2:]
    if len(d) == 11 and d.startswith("01"):        # Bangladesh local format 01XXXXXXXXX
        d = "880" + d[1:]
    return d if 10 <= len(d) <= 15 else None

def mask(to):
    return to[:3] + "***" + to[-3:]

def recipients():
    out = []
    for raw in os.getenv("WHATSAPP_TO", "").split(","):
        if raw.strip():
            n = normalize_phone(raw)
            if n: out.append(n)
            else: print(f"⚠️ Ignored invalid number in WHATSAPP_TO: {raw.strip()[:4]}***")
    return out

# ---------- build the template variables from facts (pure code, no AI) ----------
def nice(k):
    return k.replace("downtime_", "downtime: ").replace("_", " ")

def daily_params(f, dq_status="OK", dq_warnings=()):
    risk = re.sub(r"[^\x00-\x7F]", "", f["ship_risk"]).strip() or f["ship_risk"]
    mp = f.get("material_problems") or []
    if f["shortfall"] <= 0:
        issue = "none, the order is on track"
    elif mp:
        m = mp[0]
        issue = f"{m['item']} from {m['supplier']} is {m['status'].lower()} ({m['balance']:,} {m['unit']} missing)"
    else:
        loss = sorted(f["main_loss_reasons_pieces"].items(), key=lambda kv: -kv[1])
        issue = f"biggest loss is {nice(loss[0][0])}" if loss and loss[0][1] > 0 else "production is slower than plan"
    if dq_status == "WARN" and dq_warnings:
        issue += f". Data note: {dq_warnings[0]}"
    if f["shortfall"] <= 0:
        step = "keep monitoring, no action needed"
    else:
        opts = f["options_remaining_shortfall"]
        step = next((f"{OPTION_NAMES[k]} would close the gap" for k in OPTION_NAMES if opts.get(k) == 0),
                    "no single option closes the gap, so combine overtime and Friday work and talk to the buyer early")
    deadline = datetime.fromisoformat(f["sewing_deadline"]).strftime("%d %b")
    return [clean(f["order"], 40), clean(risk, 20), clean(f"{f['shortfall']:,}", 20), clean(deadline, 20),
            clean(issue, 220), clean(step, 160)]

def template_payload(to, name, params, lang=LANG):
    t = {"name": name, "language": {"code": lang}}
    if params:
        t["components"] = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}]
    return {"messaging_product": "whatsapp", "to": to, "type": "template", "template": t}

# ---------- sending ----------
def configured():
    return bool(os.getenv("WHATSAPP_TOKEN") and os.getenv("WHATSAPP_PHONE_NUMBER_ID") and recipients())

def _post(payload):
    import requests
    url = f"https://graph.facebook.com/{API_VERSION}/{os.getenv('WHATSAPP_PHONE_NUMBER_ID')}/messages"
    headers = {"Authorization": f"Bearer {os.getenv('WHATSAPP_TOKEN')}"}
    for attempt in (1, 2):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=20)
        except requests.RequestException as e:
            if attempt == 1:
                time.sleep(2); continue
            return dict(ok=False, error=f"network error ({type(e).__name__})")
        if r.status_code < 300:
            return dict(ok=True, id=((r.json().get("messages") or [{}])[0]).get("id"))
        try:
            e = r.json().get("error", {}); code = e.get("code")
            err = f"HTTP {r.status_code}, code {code}: {e.get('message')}"
            if code in ERROR_HINTS: err += f"  -> {ERROR_HINTS[code]}"
        except ValueError:
            err = f"HTTP {r.status_code}"
        if r.status_code >= 500 and attempt == 1:
            time.sleep(2); continue
        return dict(ok=False, error=err)

def _deliver(items, out_dir, force=False, dry=False):
    """items: [{kind, order, template, params}]. Sends to every recipient once; records what was sent."""
    log_file = Path(out_dir) / "whatsapp_sent.json"
    log = json.loads(log_file.read_text(encoding="utf-8")) if log_file.exists() else []
    done = {(x["kind"], x["order"], x["to"]) for x in log if x.get("ok")}
    live = configured() and not dry
    to_list = recipients() or ["8801000000000"]
    if not live:
        why = "dry-run" if dry else "WhatsApp not configured in .env"
        print(f"📵 Not sending ({why}). Preview:")
    sent = failed = skipped = 0
    for it in items:
        for to in to_list:
            if (it["kind"], it["order"], to) in done and not force:
                skipped += 1; print(f"⏭️  Already sent {it['kind']} {it['order']} to {mask(to)} (use --force to resend)"); continue
            payload = template_payload(to, it["template"], it["params"])
            if not live:
                print(f"--- to {mask(to)} | template {it['template']}")
                for i, p in enumerate(it["params"], 1): print(f"   {{{{{i}}}}} = {p}")
                continue
            res = _post(payload)
            log.append(dict(kind=it["kind"], order=it["order"], to=to, ok=res["ok"], id=res.get("id"),
                            error=res.get("error"), at=datetime.now().isoformat(timespec="seconds")))
            if res["ok"]: sent += 1; print(f"✅ WhatsApp sent: {it['kind']} {it['order']} -> {mask(to)}")
            else: failed += 1; print(f"❌ WhatsApp failed -> {mask(to)}: {res['error']}")
    if live:
        log_file.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"WhatsApp summary: {sent} sent, {failed} failed, {skipped} skipped")
    return dict(sent=sent, failed=failed, skipped=skipped)

def send_daily(facts, out_dir, force=False, dry=False):
    dq = facts.get("data_quality", {})
    items = [dict(kind="daily", order=f["order"], template=TEMPLATE_DAILY,
                  params=daily_params(f, dq.get("status", "OK"), dq.get("warnings", []))) for f in facts["orders"]]
    return _deliver(items, out_dir, force, dry)

def send_blocked(dq, out_dir, force=False, dry=False):
    """Data is missing/broken: tell the manager what to fix (most important message of all)."""
    rank = {"BLOCK": 0, "ERROR": 1, "WARN": 2}
    top = sorted(dq["issues"], key=lambda i: rank[i["level"]])[0] if dq["issues"] else dict(msg="unknown data problem", fix="check the data file")
    items = [dict(kind="data_alert", order="-", template=TEMPLATE_ALERT, params=[clean(top["msg"], 220), clean(top["fix"], 160)])]
    return _deliver(items, out_dir, force, dry)

def main():
    dry, force = "--dry-run" in sys.argv, "--force" in sys.argv
    if "--test" in sys.argv:
        if not configured() and not dry:
            sys.exit("Set WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_TO in .env first (or add --dry-run).")
        for to in (recipients() or ["8801000000000"]):
            p = template_payload(to, "hello_world", [], "en_US")
            if dry or not configured(): print("Would send:", json.dumps(p)[:200].replace(to, mask(to))); continue
            r = _post(p); print(("✅ hello_world sent to " + mask(to)) if r["ok"] else f"❌ {r['error']}")
        return
    if "--send" in sys.argv:
        runs = sorted(Path("reports").glob("*/facts.json"))
        if not runs: sys.exit("No report found. Run: python run_all.py")
        _ = send_daily(json.loads(runs[-1].read_text(encoding="utf-8")), runs[-1].parent, force, dry); return
    print(__doc__)

if __name__ == "__main__":
    main()
