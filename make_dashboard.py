"""BunonAI - Make Deshboard"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "dashboard_template.html"
REPORTS_DIR = Path("reports")
DATASET_PATH = Path("BunonAI_daily_production_dataset.csv")
OUTPUT_PATH = Path("dashboard.html")
PLACEHOLDER = "__DATA__"


def _json_default(value):
    """Convert numpy/pandas scalars to plain Python values for JSON."""
    return value.item() if hasattr(value, "item") else str(value)


def load_reports() -> tuple[list[dict], list[Path]]:
    """Load every facts.json (oldest to newest) and return it with the file paths."""
    runs = sorted(REPORTS_DIR.glob("*/facts.json"))
    if not runs:
        sys.exit("No reports yet. Run: python run_all.py")
    return [json.loads(p.read_text(encoding="utf-8")) for p in runs], runs


def load_brief(report_dir: Path) -> str:
    """Return the manager's message for the latest report, if it exists."""
    brief_file = report_dir / "brief.txt"
    return brief_file.read_text(encoding="utf-8") if brief_file.exists() else ""


def build_order_series(df: pd.DataFrame, latest: dict):
    """Build daily series and per-line efficiency for each order."""
    series, line_eff = {}, {}
    for order in latest["orders"]:
        order_df = df[df.order_id == order["order"]]

        daily = (
            order_df.groupby("date")
            .agg(
                planned=("planned_output", "sum"),
                actual=("actual_output", "sum"),
                defects=("defects_found", "sum"),
                lines=("line", "nunique"),
            )
            .reset_index()
        )
        daily["date"] = daily.date.dt.strftime("%m-%d")
        series[order["order"]] = daily.to_dict("records")

        per_line = order_df.groupby("line")[["actual_output", "planned_output"]].sum()
        line_eff[order["order"]] = (
            (per_line.actual_output / per_line.planned_output * 100).round(1).to_dict()
        )
    return series, line_eff


def build_shortfall_history(history: list[dict], latest: dict) -> dict:
    """Track the shortfall of each order across all daily reports."""
    return {
        order["order"]: [
            {"date": report["as_of"], "shortfall": item["shortfall"]}
            for report in history
            for item in report["orders"]
            if item["order"] == order["order"]
        ]
        for order in latest["orders"]
    }


def render(data: dict) -> str:
    """Inject the data into the HTML template."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    # Escape "</" so the JSON cannot close the <script> tag early.
    payload = json.dumps(data, ensure_ascii=False, default=_json_default).replace("</", "<\\/")
    return template.replace(PLACEHOLDER, payload)


def main() -> None:
    history, runs = load_reports()
    latest, latest_dir = history[-1], runs[-1].parent

    df = pd.read_csv(DATASET_PATH, parse_dates=["date"])
    df = df[df.date <= pd.Timestamp(latest["as_of"])]

    series, line_eff = build_order_series(df, latest)

    data = {
        "latest": latest,
        "brief": load_brief(latest_dir),
        "series": series,
        "lineEff": line_eff,
        "history": build_shortfall_history(history, latest),
        "generated": datetime.now().strftime("%d %b %Y, %H:%M"),
        "total_lines": int(df.line.nunique()),
    }

    OUTPUT_PATH.write_text(render(data), encoding="utf-8")
    print("✅ dashboard.html created - double-click it to open")


if __name__ == "__main__":
    main()
