# 🟢 Quality - PO-2330 - GREEN

## 1. Overall quality
- Target DHU: **3.0** (assumed) | Order so far: **2.98**
- Last 3 full days: **2.69** vs earlier 3.60 -> better
  - ⚠️ 'Earlier' has only 2 day(s) of data, so this trend is weak.

## 2. Line view (last days)
- Line-1: **2.91** (ok) | whole order: 3.02
- Line-2: **2.33** (ok) | whole order: 2.74
- Line-3: **2.83** (ok) | whole order: 3.18

## 3. Spike days (last 7 days, DHU >= 4.0)
- 2026-11-05 Line-3: DHU 4.1 (9 defects / 217 pcs), 4 absent, Machine breakdown
- 2026-11-07 Line-1: DHU 4.0 (8 defects / 200 pcs), 5 absent, no downtime

## 4. Possible links (weak signals - all orders)
- Days with **Power cut**: DHU 3.47 vs 2.80 normal (+0.67), n=8 -> small sample - don't trust yet
- Days with **Changeover**: DHU 3.45 vs 2.80 normal (+0.65), n=7 -> small sample - don't trust yet

## 5. Rework coming
- 3,319 pcs still to make at recent DHU -> about **89 defects** to fix before shipment
- (Counts defects, not defective pieces; one piece can have several defects.)

## 6. What this agent cannot see yet
- ❗ Data has **no defect type** (open seam, skipped stitch, stain...). Without it the agent can tell *where/when* quality drops, but **not what the recurring problem is**. Add a `defect_type` column to the daily sheet.