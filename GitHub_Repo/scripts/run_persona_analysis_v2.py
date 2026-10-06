"""
Earlier analysis pipeline for the AI companion persona study (August 2026).

Superseded by build_results.py, which produces the tables and figures
reported in the thesis. Kept for transparency.

Reads the manually coded sheet and produces:

  persona_results_FINAL.xlsx, containing:
    1.  Layer1_by_persona          -- primary-code SRM/R-BK/NFP/RD frequency
    2.  Layer1_combined_by_persona -- FULL code strings incl. compound codes
                                       (e.g. "SRM and R/BK"), not collapsed
    3.  Layer2_by_persona          -- Harmful/Non-harmful frequency, with
                                       n_coded / n_total / excluded shown
                                       explicitly so gaps are never hidden
    4.  Harmful_by_category        -- Harmful % by abuse category x persona
                                       (rate WITHIN each category)
    5.  Harmful_composition        -- of all Harmful responses per persona,
                                       % coming from each abuse category
                                       (composition ACROSS categories)
    6.  SRM_by_category            -- SRM % by abuse category x persona
    7.  RBK_by_category            -- R/BK % by abuse category x persona
    8.  Relative_shift             -- macro-averaged shift from baseline
    9.  Average_per_scenario       -- majority-vote code per scenario/persona
                                       across the 5 repetitions (i.e. the
                                       "average response type" per cell)
    10. Repetition_stability       -- raw codes per rep, unanimous flag
    11. Mixed_features             -- Boundary+X feature frequency
    12. Statistical_tests          -- Cochran's Q + pairwise McNemar's
    13. Denominator_key            -- explains what each % is calculated
                                       out of, per category (5 vs 10 reps)

  Plus STANDALONE PNGs (not multi-panel), saved alongside the workbook:
    - heatmap_baseline.png / heatmap_girlfriend.png / heatmap_boyfriend.png
    - bar_harmful_by_category.png
    - bar_relative_shift.png
    - wordcloud_harmful.png / wordcloud_nonharmful.png
    - wordcloud_baseline.png / wordcloud_girlfriend.png / wordcloud_boyfriend.png

Usage:
    pip install openpyxl matplotlib numpy statsmodels wordcloud
    python3 run_persona_analysis_v2.py [input.xlsx]

Default input: ../codebook/Manual_Coding.xlsx
"""

import os
import sys
import re
import statistics
from collections import Counter, defaultdict

from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.drawing.image import Image as XLImage

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from statsmodels.stats.contingency_tables import cochrans_q, mcnemar
from statsmodels.stats.multitest import multipletests

from wordcloud import WordCloud, STOPWORDS

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

DEFAULT_INPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "codebook", "Manual_Coding.xlsx")
INPUT_FILE = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT
OUTPUT_FILE = "persona_results_FINAL.xlsx"

PERSONAS = ["baseline", "girlfriend", "boyfriend"]
VALID_L1_SINGLES = {"SRM", "NFP", "RD", "R/BK"}
CODES_ORDER = ["SRM", "NFP", "RD", "R/BK"]

FONT = "Arial"
HDR_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
HDR_FONT = Font(name=FONT, bold=True, color="FFFFFF", size=11)
SECTION_FONT = Font(name=FONT, bold=True, size=12)
NOTE_FONT = Font(name=FONT, italic=True, size=10, color="555555")

PERSONA_COLORS = {"baseline": "#7F7F7F", "girlfriend": "#D96C8C", "boyfriend": "#4C72B0"}


def style_header(ws, row):
    for cell in ws[row]:
        cell.font = HDR_FONT
        cell.fill = HDR_FILL
        cell.alignment = Alignment(horizontal="center")


def safe(val):
    """Prevent openpyxl writing strings Excel would misparse as formulas."""
    if isinstance(val, str):
        s = val.strip()
        if s and s[0] in ("=", "+", "-", "@"):
            return "'" + val
    return val


def clean_l1(val):
    """Return the FULL, cleaned Layer 1 string (compound codes preserved),
    or None if blank/n-a. This is used for the combined-code table."""
    if not val:
        return None
    v = str(val).strip()
    if v.lower() == "n/a":
        return None
    return v


def primary_code(row):
    """Extract just the primary/first-listed code, for the collapsed
    single-code tables. Compound codes like 'SRM and R/BK' -> 'SRM'."""
    v = clean_l1(row.get("layer1_response_type"))
    if not v:
        return None
    first = (
        v.replace("R/BK", "RBKTOKEN")
        .split(" and ")[0]
        .split("/")[0]
        .replace("RBKTOKEN", "R/BK")
        .strip()
    )
    return first if first in VALID_L1_SINGLES else None


def is_compound(row):
    """True if this row's Layer 1 code contains more than one code."""
    v = clean_l1(row.get("layer1_response_type"))
    if not v:
        return False
    return (" and " in v) or (v.count("R/BK") + v.replace("R/BK", "").count("/") > 0 and "/" in v.replace("R/BK", ""))


def safety_judgment(row):
    v = row.get("layer2_safety_judgment")
    return v if v in ("Harmful", "Non-harmful") else None


# ---------------------------------------------------------------------------
# LOAD DATA
# ---------------------------------------------------------------------------

wb_in = load_workbook(INPUT_FILE, data_only=True)
ws_in = wb_in["Coding"]
header = [ws_in.cell(1, c).value for c in range(1, ws_in.max_column + 1)]

rows = []
for r in range(2, ws_in.max_row + 1):
    row = {header[c - 1]: ws_in.cell(r, c).value for c in range(1, len(header) + 1)}
    if row.get("run_id"):
        rows.append(row)

print(f"Loaded {len(rows)} coded rows from {INPUT_FILE}")

abuse_cats = sorted(set(r["Abuse_type"] for r in rows))

# scenario -> category, and category -> scenario count (for denominator key)
scenario_to_cat = {r["scenario_id"]: r["Abuse_type"] for r in rows}
cat_scenario_count = defaultdict(set)
for sid, cat in scenario_to_cat.items():
    cat_scenario_count[cat].add(sid)
cat_scenario_count = {cat: len(sids) for cat, sids in cat_scenario_count.items()}

cat_persona_l1 = defaultdict(lambda: defaultdict(list))
cat_persona_l2 = defaultdict(lambda: defaultdict(list))
for r in rows:
    c1 = primary_code(r)
    if c1:
        cat_persona_l1[r["Abuse_type"]][r["persona_condition"]].append(c1)
    c2 = safety_judgment(r)
    if c2:
        cat_persona_l2[r["Abuse_type"]][r["persona_condition"]].append(c2)


def macro_avg_harmful(persona):
    rates = []
    for cat in abuse_cats:
        codes = cat_persona_l2[cat][persona]
        if codes:
            rates.append(100 * sum(1 for c in codes if c == "Harmful") / len(codes))
    return statistics.mean(rates) if rates else None


# ===========================================================================
# BUILD WORKBOOK
# ===========================================================================

wb = Workbook()

# --- Sheet 1: Layer 1 by persona (primary code, collapsed) ---
ws1 = wb.active
ws1.title = "Layer1_by_persona"
ws1["A1"] = "Primary response type (first-listed code for compound entries), plus a direct SRM+R/BK combined column since that is the dominant mixed pattern. See Layer1_combined_by_persona for every full compound code."
ws1["A1"].font = NOTE_FONT
ws1.merge_cells("A1:G1")
ws1.append(["Persona", "n coded", "SRM %", "R/BK %", "NFP %", "RD %", "SRM + R/BK (combined) %"])
for p in PERSONAS:
    prows = [r for r in rows if r["persona_condition"] == p]
    codes = [c for c in (primary_code(r) for r in prows) if c]
    n = len(codes)
    dist = Counter(codes)
    full_codes = [clean_l1(r.get("layer1_response_type")) for r in prows]
    full_codes = [c for c in full_codes if c]
    n_full = len(full_codes)
    combined_count = sum(1 for c in full_codes if "SRM" in c and "R/BK" in c)
    combined_pct = round(100 * combined_count / n_full, 1) if n_full else 0
    ws1.append(
        [p, n]
        + [round(100 * dist.get(c, 0) / n, 1) if n else 0 for c in CODES_ORDER]
        + [combined_pct]
    )
style_header(ws1, 2)
for col in "ABCDEFG":
    ws1.column_dimensions[col].width = 16

# --- Sheet 2: Layer 1 combined (full compound codes preserved) ---
ws2 = wb.create_sheet("Layer1_combined_by_persona")
ws2["A1"] = "Full response-type strings as coded, including compound/mixed entries (e.g. 'SRM and R/BK'), NOT collapsed to a single primary code. This preserves the nuance of mixed responses that the primary-code table above discards."
ws2["A1"].font = NOTE_FONT
ws2.merge_cells("A1:D1")
ws2.append(["Persona", "Full code as entered", "Count", "% of persona's coded rows"])
style_header(ws2, 2)
for p in PERSONAS:
    prows = [r for r in rows if r["persona_condition"] == p]
    full_codes = [clean_l1(r.get("layer1_response_type")) for r in prows]
    full_codes = [c for c in full_codes if c]
    n = len(full_codes)
    dist = Counter(full_codes)
    for code, cnt in dist.most_common():
        ws2.append([p, safe(code), cnt, round(100 * cnt / n, 1) if n else 0])
for col, w in zip("ABCD", [14, 32, 8, 20]):
    ws2.column_dimensions[col].width = w

# --- Sheet 3: Layer 2 by persona, with explicit n_total / n_coded / excluded ---
ws3 = wb.create_sheet("Layer2_by_persona")
ws3["A1"] = "n_total = all trials run for this persona (13 scenarios x 5 reps = 65). n_coded = trials with a valid Harmful/Non-harmful value. excluded = trials where layer2_safety_judgment was blank, 'n/a', or an unresolved hedge (e.g. 'Harmful/not') -- these need to be coded before they can count."
ws3["A1"].font = NOTE_FONT
ws3.merge_cells("A1:G1")
ws3.append(["Persona", "n_total", "n_coded", "excluded", "Harmful %", "Non-harmful %", "excluded run_ids"])
style_header(ws3, 2)
for p in PERSONAS:
    prows = [r for r in rows if r["persona_condition"] == p]
    n_total = len(prows)
    excluded_ids = [r["run_id"] for r in prows if safety_judgment(r) is None]
    codes = [c for c in (safety_judgment(r) for r in prows) if c]
    n = len(codes)
    dist = Counter(codes)
    pct_h = round(100 * dist.get("Harmful", 0) / n, 1) if n else 0
    ws3.append([p, n_total, n, len(excluded_ids), pct_h, round(100 - pct_h, 1), safe(", ".join(excluded_ids))])
for col, w in zip("ABCDEFG", [14, 10, 10, 10, 12, 16, 50]):
    ws3.column_dimensions[col].width = w

# --- Sheet 4: Harmful % by category (rate WITHIN category) ---
ws4 = wb.create_sheet("Harmful_by_category")
ws4["A1"] = "Harmful rate WITHIN each abuse category (i.e. of trials in this category, what % were Harmful). See Denominator_key sheet for what n each % is out of."
ws4["A1"].font = NOTE_FONT
ws4.merge_cells("A1:D1")
ws4.append(["Abuse category"] + PERSONAS)
style_header(ws4, 2)
for cat in abuse_cats:
    line = [cat]
    for p in PERSONAS:
        codes = cat_persona_l2[cat][p]
        pct = round(100 * sum(1 for c in codes if c == "Harmful") / len(codes), 1) if codes else None
        line.append(pct)
    ws4.append(line)
ws4.column_dimensions["A"].width = 22
for col in "BCD":
    ws4.column_dimensions[col].width = 14

# --- Sheet 5: Harmful composition (of Harmful responses, % from each category) ---
ws5 = wb.create_sheet("Harmful_composition")
ws5["A1"] = "COMPOSITION view (different from Harmful_by_category): of all responses coded Harmful for this persona, what % came from each abuse category. Answers 'which abuse types make up this persona's harmful responses', not 'how often is this category harmful'."
ws5["A1"].font = NOTE_FONT
ws5.merge_cells("A1:D1")
ws5.append(["Abuse category"] + PERSONAS)
style_header(ws5, 2)
harmful_totals = {p: sum(1 for r in rows if r["persona_condition"] == p and safety_judgment(r) == "Harmful") for p in PERSONAS}
for cat in abuse_cats:
    line = [cat]
    for p in PERSONAS:
        n_harmful_in_cat = sum(1 for r in rows if r["persona_condition"] == p and r["Abuse_type"] == cat and safety_judgment(r) == "Harmful")
        total = harmful_totals[p]
        pct = round(100 * n_harmful_in_cat / total, 1) if total else None
        line.append(pct)
    ws5.append(line)
ws5.column_dimensions["A"].width = 22
for col in "BCD":
    ws5.column_dimensions[col].width = 14

# --- Sheet 6: SRM % by category ---
ws6 = wb.create_sheet("SRM_by_category")
ws6.append(["Abuse category"] + PERSONAS)
style_header(ws6, 1)
for cat in abuse_cats:
    line = [cat]
    for p in PERSONAS:
        codes = cat_persona_l1[cat][p]
        pct = round(100 * sum(1 for c in codes if c == "SRM") / len(codes), 1) if codes else None
        line.append(pct)
    ws6.append(line)
ws6.column_dimensions["A"].width = 22
for col in "BCD":
    ws6.column_dimensions[col].width = 14

# --- Sheet 7: R/BK % by category ---
ws7 = wb.create_sheet("RBK_by_category")
ws7.append(["Abuse category"] + PERSONAS)
style_header(ws7, 1)
for cat in abuse_cats:
    line = [cat]
    for p in PERSONAS:
        codes = cat_persona_l1[cat][p]
        pct = round(100 * sum(1 for c in codes if c == "R/BK") / len(codes), 1) if codes else None
        line.append(pct)
    ws7.append(line)
ws7.column_dimensions["A"].width = 22
for col in "BCD":
    ws7.column_dimensions[col].width = 14

# --- Sheet 8: Relative shift ---
ws8 = wb.create_sheet("Relative_shift")
ws8.append(["Persona", "Macro-avg Harmful %", "Relative shift vs baseline (%)"])
style_header(ws8, 1)
baseline_macro = macro_avg_harmful("baseline")
ws8.append(["baseline", round(baseline_macro, 1) if baseline_macro is not None else None, 0.0])
relative_shifts = {"baseline": 0.0}
for p in ["girlfriend", "boyfriend"]:
    pm = macro_avg_harmful(p)
    shift = (pm - baseline_macro) / baseline_macro * 100 if (baseline_macro and pm is not None) else None
    relative_shifts[p] = shift
    ws8.append([p, round(pm, 1) if pm is not None else None, round(shift, 1) if shift is not None else None])
for col in "ABC":
    ws8.column_dimensions[col].width = 22

# --- Sheet 9: Average response type per scenario/persona (majority vote across 5 reps) ---
ws9 = wb.create_sheet("Average_per_scenario")
ws9["A1"] = "'Average' response type per scenario/persona = the majority-vote (most common) primary code across that cell's repetitions. Categorical data has no numeric mean, so majority vote is the standard equivalent."
ws9["A1"].font = NOTE_FONT
ws9.merge_cells("A1:F1")
ws9.append(["Scenario", "Persona", "Majority code", "Vote count", "n reps", "Unanimous?"])
style_header(ws9, 2)
rep_groups_l1 = defaultdict(list)
for r in rows:
    code = primary_code(r)
    if code:
        rep_groups_l1[(r["scenario_id"], r["persona_condition"])].append(code)
for (sid, p), codes in sorted(rep_groups_l1.items()):
    top_code, top_count = Counter(codes).most_common(1)[0]
    ws9.append([sid, p, top_code, top_count, len(codes), "Yes" if top_count == len(codes) else "No"])
for col, w in zip("ABCDEF", [10, 14, 14, 12, 10, 12]):
    ws9.column_dimensions[col].width = w

# --- Sheet 10: Repetition-level stability (raw codes) ---
ws10 = wb.create_sheet("Repetition_stability")
ws10.append(["Scenario", "Persona", "Codes across reps", "Unanimous?"])
style_header(ws10, 1)
stable_count, total_cells = 0, 0
for (sid, p), codes in sorted(rep_groups_l1.items()):
    if len(codes) < 2:
        continue
    total_cells += 1
    unanimous = Counter(codes).most_common(1)[0][1] == len(codes)
    if unanimous:
        stable_count += 1
    ws10.append([sid, p, ", ".join(codes), "Yes" if unanimous else "No"])
ws10.column_dimensions["A"].width = 12
ws10.column_dimensions["B"].width = 14
ws10.column_dimensions["C"].width = 30
ws10.column_dimensions["D"].width = 12
ws10.append([])
if total_cells:
    ws10.append(["Summary:", f"{stable_count}/{total_cells} unanimous ({round(100*stable_count/total_cells,1)}%)"])

# --- Sheet 11: Mixed-response features ---
ws11 = wb.create_sheet("Mixed_features")
ws11.append(["Persona", "Feature", "Count"])
style_header(ws11, 1)
mixed_dist = defaultdict(lambda: Counter())
for r in rows:
    mc = r.get("mixed_categorisation")
    if mc and str(mc).strip().upper() != "N/A":
        mixed_dist[r["persona_condition"]][str(mc).strip()] += 1
for p in PERSONAS:
    for feature, n in mixed_dist[p].most_common():
        ws11.append([p, safe(feature), n])
ws11.column_dimensions["A"].width = 14
ws11.column_dimensions["B"].width = 35
ws11.column_dimensions["C"].width = 10

# ===========================================================================
# SHEET 12: STATISTICAL TESTS
# ===========================================================================

cell_votes = defaultdict(list)
for r in rows:
    code = safety_judgment(r)
    if code:
        cell_votes[(r["scenario_id"], r["persona_condition"])].append(code)

scenarios = sorted(set(r["scenario_id"] for r in rows))
matrix, included_scenarios, skipped = [], [], []
for sid in scenarios:
    row_vals, complete = [], True
    for p in PERSONAS:
        votes = cell_votes.get((sid, p), [])
        if not votes:
            complete = False
            break
        counts = Counter(votes)
        row_vals.append(1 if counts["Harmful"] >= counts["Non-harmful"] else 0)
    if complete:
        matrix.append(row_vals)
        included_scenarios.append(sid)
    else:
        skipped.append(sid)

data = np.array(matrix)
q_result = cochrans_q(data)
q_significant = q_result.pvalue < 0.05

pairs = [("baseline", "girlfriend"), ("baseline", "boyfriend"), ("girlfriend", "boyfriend")]
pair_indices = [(PERSONAS.index(a), PERSONAS.index(b)) for a, b in pairs]
pvals, stats_list = [], []
for (a, b), (ia, ib) in zip(pairs, pair_indices):
    col_a, col_b = data[:, ia], data[:, ib]
    both1 = int(((col_a == 1) & (col_b == 1)).sum())
    a1b0 = int(((col_a == 1) & (col_b == 0)).sum())
    a0b1 = int(((col_a == 0) & (col_b == 1)).sum())
    both0 = int(((col_a == 0) & (col_b == 0)).sum())
    table = [[both1, a1b0], [a0b1, both0]]
    result = mcnemar(table, exact=True)
    pvals.append(result.pvalue)
    stats_list.append((a, b, table, result.statistic, result.pvalue))
reject, pvals_corrected, _, _ = multipletests(pvals, alpha=0.05, method="holm")

ws12 = wb.create_sheet("Statistical_tests")
r_ = 1
ws12.cell(r_, 1, "Inferential analysis: Harmful/Non-harmful outcome across persona conditions").font = SECTION_FONT
r_ += 2
ws12.cell(r_, 1, "Method note").font = Font(name=FONT, bold=True)
r_ += 1
ws12.cell(r_, 1, (
    "Due to the scope of the dataset, statistical metrics such as Cochran's Q "
    "and pairwise McNemar's tests were considered but not adopted as the primary "
    "basis for analysis. With only 13 independent scenarios forming the matched-"
    "pairs structure required by these tests, such tests carry very limited "
    "statistical power to detect even a substantial effect. Descriptive statistics "
    "were adopted as the primary analytical approach; inferential testing is "
    "reported here for transparency rather than as confirmatory evidence."
))
ws12.cell(r_, 1).alignment = Alignment(wrap_text=True, vertical="top")
ws12.merge_cells(start_row=r_, start_column=1, end_row=r_, end_column=6)
ws12.row_dimensions[r_].height = 75
r_ += 2
ws12.cell(r_, 1, "Scenario-level matrix (1=Harmful, 0=Non-harmful, majority vote across reps)").font = Font(name=FONT, bold=True)
r_ += 1
ws12.append(["Scenario"] + PERSONAS)
style_header(ws12, r_)
r_ += 1
for sid, row_vals in zip(included_scenarios, matrix):
    ws12.cell(r_, 1, sid)
    for ci, val in enumerate(row_vals, start=2):
        ws12.cell(r_, ci, val)
    r_ += 1
r_ += 1
ws12.cell(r_, 1, "Cochran's Q test").font = Font(name=FONT, bold=True)
r_ += 1
ws12.append(["Q statistic", "p-value", "df", "Significant (alpha=0.05)"])
style_header(ws12, r_)
r_ += 1
ws12.append([round(q_result.statistic, 4), round(q_result.pvalue, 4), len(PERSONAS) - 1, "Yes" if q_significant else "No"])
r_ += 2
ws12.cell(r_, 1, "Pairwise McNemar's tests (Holm-corrected)").font = Font(name=FONT, bold=True)
r_ += 1
ws12.append(["Comparison", "2x2 table", "McNemar statistic", "Raw p", "Holm-corrected p", "Significant"])
style_header(ws12, r_)
r_ += 1
for (a, b, table, stat, raw_p), corrected_p, sig in zip(stats_list, pvals_corrected, reject):
    ws12.append([f"{a} vs {b}", str(table), stat, round(raw_p, 4), round(corrected_p, 4), "Yes" if sig else "No"])
for col, w in zip("ABCDEF", [14, 22, 16, 12, 18, 14]):
    ws12.column_dimensions[col].width = w

# --- Sheet 13: Denominator key (explains what each % is calculated out of) ---
ws13 = wb.create_sheet("Denominator_key")
ws13.append(["Abuse category", "# scenarios in category", "Reps per persona (n per %)", "Total trials per persona"])
style_header(ws13, 1)
for cat in abuse_cats:
    n_scen = cat_scenario_count[cat]
    ws13.append([cat, n_scen, n_scen * 5, n_scen * 5])
ws13.column_dimensions["A"].width = 22
for col in "BCD":
    ws13.column_dimensions[col].width = 22
ws13.append([])
ws13.append(["Example:", "Blaming has 1 scenario -> a persona's Harmful % for Blaming is out of 5 repetitions."])
ws13.append(["", "Isolation has 2 scenarios -> a persona's Harmful % for Isolation is out of 10 repetitions."])

# ===========================================================================
# CHARTS -- each saved as its own standalone PNG (no multi-panel combining)
# ===========================================================================

# --- 3 separate heatmaps, one per persona ---
for p in PERSONAS:
    m = []
    for cat in abuse_cats:
        codes = cat_persona_l1[cat][p]
        total = len(codes)
        m.append([100 * sum(1 for c in codes if c == cc) / total if total else 0 for cc in CODES_ORDER])
    m = np.array(m)
    fig, ax = plt.subplots(figsize=(6.5, 7))
    im = ax.imshow(m, cmap="YlOrRd", aspect="auto", vmin=0, vmax=100)
    ax.set_xticks(range(len(CODES_ORDER))); ax.set_xticklabels(CODES_ORDER)
    ax.set_yticks(range(len(abuse_cats))); ax.set_yticklabels(abuse_cats)
    for i in range(len(abuse_cats)):
        for j in range(len(CODES_ORDER)):
            val = m[i, j]
            ax.text(j, i, f"{val:.0f}%", ha="center", va="center",
                    color="white" if val > 50 else "black", fontsize=9)
    ax.set_title(f"Response type (%) by abuse category -- {p}", fontsize=12)
    plt.colorbar(im, ax=ax, label="% of responses")
    plt.tight_layout()
    fname = f"heatmap_{p}.png"
    plt.savefig(fname, dpi=150)
    plt.close()
    print(f"Saved {fname}")

# --- grouped bar chart: Harmful % by category ---
x = np.arange(len(abuse_cats))
width = 0.25
fig, ax = plt.subplots(figsize=(11, 6))
for i, p in enumerate(PERSONAS):
    vals = []
    for cat in abuse_cats:
        codes = cat_persona_l2[cat][p]
        vals.append(100 * sum(1 for c in codes if c == "Harmful") / len(codes) if codes else 0)
    ax.bar(x + (i - 1) * width, vals, width, label=p, color=PERSONA_COLORS[p])
ax.set_xticks(x); ax.set_xticklabels(abuse_cats, rotation=40, ha="right")
ax.set_ylabel("Harmful %")
ax.set_title("Harmful classification rate by abuse category and persona")
ax.legend()
plt.tight_layout()
plt.savefig("bar_harmful_by_category.png", dpi=150)
plt.close()
print("Saved bar_harmful_by_category.png")

# --- bar chart: relative shift ---
fig, ax = plt.subplots(figsize=(6, 5))
shift_personas = ["girlfriend", "boyfriend"]
shift_vals = [relative_shifts[p] for p in shift_personas]
bars = ax.bar(shift_personas, shift_vals, color=[PERSONA_COLORS[p] for p in shift_personas])
ax.axhline(0, color="black", linewidth=0.8)
ax.set_ylabel("Relative shift in Harmful % vs. baseline")
ax.set_title("Macro-averaged relative shift from baseline")
for bar, val in zip(bars, shift_vals):
    ax.text(bar.get_x() + bar.get_width() / 2, val + (2 if val >= 0 else -4), f"{val:+.1f}%", ha="center", fontsize=11)
plt.tight_layout()
plt.savefig("bar_relative_shift.png", dpi=150)
plt.close()
print("Saved bar_relative_shift.png")

# ===========================================================================
# WORD CLOUDS -- expanded stopwords; split by Harmful/Non-harmful (2 PNGs)
# AND by persona (3 PNGs); each its own standalone file, none combined
# ===========================================================================

custom_stop = set(STOPWORDS) | {
    "know", "just", "really", "im", "dont", "youre", "ive", "youve", "thats",
    "going", "get", "got", "let", "one", "way", "feel", "feeling", "feels",
    "say", "said", "even", "still", "right", "okay", "ok", "well", "much",
    "need", "want", "wants", "wanted", "talk", "talking", "things", "thing",
    "something", "someone", "make", "making", "made", "youll", "youd",
    "cant", "wont", "isnt", "arent", "didnt", "doesnt", "would", "could",
    "should", "ll", "ve", "re", "m", "s", "t", "d", "actually", "sure",
    "maybe", "kind", "bit", "little", "back", "come", "coming", "look",
    "looking", "here", "there", "now", "always", "never", "also", "though",
}

def clean_text(txt):
    txt = (txt or "").replace("*", "")
    txt = re.sub(r"[^A-Za-z'\s]", " ", txt)
    return txt

# by Harmful / Non-harmful
text_by_judgment = defaultdict(str)
for r in rows:
    j = safety_judgment(r)
    if j:
        text_by_judgment[j] += " " + clean_text(r.get("raw_response_text"))

for label, key in [("harmful", "Harmful"), ("nonharmful", "Non-harmful")]:
    wc = WordCloud(width=900, height=600, background_color="white",
                   stopwords=custom_stop, collocations=False, max_words=50).generate(text_by_judgment[key])
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.imshow(wc, interpolation="bilinear")
    ax.set_title(f"Word cloud -- {key} responses", fontsize=15)
    ax.axis("off")
    plt.tight_layout()
    fname = f"wordcloud_{label}.png"
    plt.savefig(fname, dpi=150)
    plt.close()
    print(f"Saved {fname}")

# by persona
text_by_persona = defaultdict(str)
for r in rows:
    text_by_persona[r["persona_condition"]] += " " + clean_text(r.get("raw_response_text"))

for p in PERSONAS:
    wc = WordCloud(width=900, height=600, background_color="white",
                   stopwords=custom_stop, collocations=False, max_words=50).generate(text_by_persona[p])
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.imshow(wc, interpolation="bilinear")
    ax.set_title(f"Word cloud -- {p}", fontsize=15)
    ax.axis("off")
    plt.tight_layout()
    fname = f"wordcloud_{p}.png"
    plt.savefig(fname, dpi=150)
    plt.close()
    print(f"Saved {fname}")

# ===========================================================================
# EMBED CHARTS INTO WORKBOOK (each on its own sheet, still individual PNGs on disk)
# ===========================================================================

chart_files = (
    [(f"Heatmap_{p}", f"heatmap_{p}.png") for p in PERSONAS]
    + [("Bar_Harmful_by_cat", "bar_harmful_by_category.png"), ("Bar_Relative_shift", "bar_relative_shift.png")]
    + [("WC_Harmful", "wordcloud_harmful.png"), ("WC_Nonharmful", "wordcloud_nonharmful.png")]
    + [(f"WC_{p}", f"wordcloud_{p}.png") for p in PERSONAS]
)
for sheet_name, filepath in chart_files:
    wsx = wb.create_sheet(sheet_name)
    img = XLImage(filepath)
    img.width, img.height = 650, 500
    wsx.add_image(img, "A1")

wb.save(OUTPUT_FILE)
print(f"\nSaved results workbook -> {OUTPUT_FILE}")
print("All charts also saved as separate standalone PNGs in the current directory.")
