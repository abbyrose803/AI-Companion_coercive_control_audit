"""
build_results.py -- builds the persona-results workbook and figures from the
manually coded dataset (codebook/Manual_Coding.xlsx).

Outputs:
  - one Excel workbook with a sheet per table (Layer 1 / Layer 2 by persona,
    rates by abuse category, AI role, harm subcategory, relative shift,
    repetition stability, Cochran's Q / McNemar tests) plus a Notes sheet
    explaining denominators and conventions;
  - one standalone PNG per chart in figs/ (also embedded in the workbook).

"combined" = girlfriend + boyfriend rows pooled. Persona order is fixed
throughout: baseline, girlfriend, boyfriend, combined.

Usage:
    python3 build_results.py [input.xlsx] [output.xlsx]

Defaults: ../codebook/Manual_Coding.xlsx -> persona_results_REBUILD.xlsx
"""

import sys
import os
import re
from collections import Counter, defaultdict
from datetime import datetime

import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XLImage
from openpyxl.formatting.rule import ColorScaleRule

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from statsmodels.stats.contingency_tables import cochrans_q as sm_cochrans_q, mcnemar as sm_mcnemar
from statsmodels.stats.multitest import multipletests

try:
    from wordcloud import WordCloud, STOPWORDS
    HAVE_WORDCLOUD = True
except ImportError:
    HAVE_WORDCLOUD = False

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

DEFAULT_INPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "codebook", "Manual_Coding.xlsx")
INPUT_FILE = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT
OUTPUT_FILE = sys.argv[2] if len(sys.argv) > 2 else "persona_results_REBUILD.xlsx"
FIG_DIR = "figs"
if os.path.isdir(FIG_DIR):
    import shutil
    shutil.rmtree(FIG_DIR)  # wipe stale figures from previous runs/versions before regenerating
os.makedirs(FIG_DIR, exist_ok=True)

PERSONA_ORDER = ["baseline", "girlfriend", "boyfriend", "combined"]
REAL_PERSONAS = ["baseline", "girlfriend", "boyfriend"]  # as actually coded
ROMANTIC_PERSONAS = ["girlfriend", "boyfriend"]           # pooled -> "combined"

CATEGORY_ORDER = [
    "Blaming", "Economic abuse", "Emotional abuse", "Intimidation",
    "Isolation", "Minimising/denying", "Physical abuse", "Sexual abuse",
    "Threats",
]

VALID_L2 = {"Harmful", "Non-harmful"}
VALID_ROLE = {"Facilitator", "Enabler"}
NA_VARIANTS = {"n/a", "na", "not applicable", "non applicable", "none", ""}


def normalize_subcat(s):
    """Collapse messy N/A variants ('n/a', 'not applicable', 'non applicable', ...)
    into a single 'N/A' label; keep real subcategories as-is."""
    if s is None:
        return "N/A"
    s_clean = str(s).strip()
    if s_clean.lower() in NA_VARIANTS:
        return "N/A"
    return s_clean

# Seaborn "darkgrid"-style aesthetic:
# light grey-blue panel background, white gridlines, muted colour palette,
# warm sequential colormap for heatmaps. Set via rcParams so no seaborn
# dependency is required.
AXES_BG = "#EAEAF2"
GRID_COLOR = "#FFFFFF"
PALETTE = ["#3B0F70", "#8C2981", "#B83280", "#DE4968", "#F2A65A"]  # purple-pink-orange, magma-family
SINGLE_BAR_COLOR = "#2E5EAA"  # navy, kept separate for the single-series "Overall Harmful Rate" chart
HEATMAP_CMAP = "magma"
WORDCLOUD_COLORS = ["#3B0F70", "#8C2981", "#B83280", "#DE4968", "#F2A65A"]  # matches PALETTE

plt.rcParams.update({
    "axes.facecolor": AXES_BG,
    "axes.edgecolor": AXES_BG,
    "axes.grid": True,
    "grid.color": GRID_COLOR,
    "grid.linewidth": 1.3,
    "axes.axisbelow": True,
    "figure.facecolor": "white",
    "font.family": "sans-serif",
    "text.color": "#2B2B2B",
    "axes.labelcolor": "#2B2B2B",
    "xtick.color": "#2B2B2B",
    "ytick.color": "#2B2B2B",
})

FILLER_WORDS = {
    "hey", "please", "trying", "listen", "just", "really", "im", "youre",
    "dont", "know", "want", "think", "feel", "feeling", "like", "okay",
    "ok", "going", "get", "got", "would", "could", "way", "thing",
    "things", "sorry", "understand", "youre", "ive", "youve", "id",
    "theyre", "arent", "isnt", "didnt", "cant", "wont", "wasnt",
    "youll", "ill", "were", "theyve", "weve", "whats", "thats", "its",
    # Generic connective/filler words that dominate word clouds without
    # conveying thematic content.
    "us", "let", "lets", "look", "looking", "looks", "take", "taking",
    "come", "coming", "much", "make", "making", "makes", "something",
    "actually", "instead", "even", "right", "now", "need", "needs",
    "say", "saying", "says", "see", "seeing", "little", "always", "back",
    "one", "certainly", "perhaps", "sound", "sounds", "moment", "second",
    "exactly", "sure", "really", "still", "yet", "also", "well", "maybe",
}

# ---------------------------------------------------------------------------
# STYLES
# ---------------------------------------------------------------------------

HEADER_FONT = Font(bold=True, size=11, color="000000")
HEADER_FILL = PatternFill("solid", fgColor="E8E8E8")  # light grey
ROW_LABEL_FONT = Font(bold=True, size=11, color="000000")

# Green colour-scale for heat-mapped percentage tables:
# pale/near-white for low values, ramping to a darker green for high values.
GREEN_SCALE = ColorScaleRule(
    start_type="min", start_color="FFFFFF",
    mid_type="percentile", mid_value=50, mid_color="C6E8B0",
    end_type="max", end_color="4E8A3C",
)


def apply_green_scale(ws, first_col, last_col, first_row, last_row):
    """Applies the green heat-map colour scale to a rectangular numeric
    range (e.g. the persona/condition percentage columns of a table),
    leaving the label column(s) untouched."""
    rng = f"{get_column_letter(first_col)}{first_row}:{get_column_letter(last_col)}{last_row}"
    ws.conditional_formatting.add(rng, GREEN_SCALE)
TITLE_FONT = Font(bold=True, size=13, color="000000")
NOTE_FONT = Font(italic=True, size=10, color="444444")
THIN = Side(style="thin", color="AAAAAA")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center")
LEFT = Alignment(horizontal="left", vertical="center")


def style_header_row(ws, row=1, ncols=None):
    ncols = ncols or ws.max_column
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = BORDER
        cell.alignment = CENTER


def style_data_cell(cell, center=True):
    cell.border = BORDER
    cell.alignment = CENTER if center else LEFT


def autosize(ws, min_width=10, max_width=50):
    widths = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            col = cell.column_letter
            widths[col] = max(widths.get(col, min_width), min(max_width, len(str(cell.value)) + 2))
    for col, w in widths.items():
        ws.column_dimensions[col].width = w


def write_table(ws, headers, rows, start_row=1, title=None):
    """Write a clean bordered table with bold header row. Returns next free row."""
    r = start_row
    if title:
        ws.cell(row=r, column=1, value=title).font = TITLE_FONT
        r += 1
    header_row = r
    for c, h in enumerate(headers, start=1):
        ws.cell(row=header_row, column=c, value=h)
    style_header_row(ws, header_row, len(headers))
    r += 1
    for row_vals in rows:
        for c, v in enumerate(row_vals, start=1):
            cell = ws.cell(row=r, column=c, value=v)
            style_data_cell(cell, center=(c > 1))
            if c == 1:
                cell.font = ROW_LABEL_FONT
        r += 1
    return r + 1  # blank row after table


def write_table_heatmap(ws, headers, rows, start_row=1, title=None, label_cols=1):
    """Same as write_table, but applies the green colour-scale to every
    numeric column (i.e. every column after the first `label_cols` label
    columns) -- for the by-category / by-persona percentage tables that
    should read as heat-mapped tables."""
    r = start_row
    if title:
        ws.cell(row=r, column=1, value=title).font = TITLE_FONT
        r += 1
    header_row = r
    for c, h in enumerate(headers, start=1):
        ws.cell(row=header_row, column=c, value=h)
    style_header_row(ws, header_row, len(headers))
    r += 1
    data_start = r
    for row_vals in rows:
        for c, v in enumerate(row_vals, start=1):
            cell = ws.cell(row=r, column=c, value=v)
            style_data_cell(cell, center=(c > 1))
            if c == 1:
                cell.font = ROW_LABEL_FONT
        r += 1
    data_end = r - 1
    if data_end >= data_start:
        apply_green_scale(ws, label_cols + 1, len(headers), data_start, data_end)
    return r + 1


# ---------------------------------------------------------------------------
# LOAD DATA
# ---------------------------------------------------------------------------

def primary_code(l1):
    """First-listed code for compound entries, e.g. 'R/BK and SRM' -> 'R/BK'."""
    if not l1:
        return None
    l1 = str(l1).strip()
    return re.split(r"\s+and\s+", l1)[0].strip()


def load_rows(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["Coding"]
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    idx = {name: i for i, name in enumerate(header)}
    data = []
    for r in rows[1:]:
        if r[idx["run_id"]] is None:
            continue
        data.append({
            "run_id": r[idx["run_id"]],
            "persona": r[idx["persona_condition"]],
            "scenario": r[idx["scenario_id"]],
            "category": r[idx["Abuse_type"]],
            "raw_text": r[idx["raw_response_text"]],
            "run_number": r[idx["run_number"]],
            "l1": r[idx["layer1_response_type"]],
            "l2": r[idx["layer2_safety_judgment"]],
            "role": r[idx["layer3_ai_role"]],
            "subcat": normalize_subcat(r[idx["layer3_transgression_subcat"]]),
            "mixed": r[idx["mixed_categorisation"]],
            "notes": r[idx["coder_notes"]],
        })
    return data


def with_combined(data):
    """Return data plus synthetic 'combined' rows (copy of girlfriend+boyfriend)."""
    out = list(data)
    for row in data:
        if row["persona"] in ROMANTIC_PERSONAS:
            new_row = dict(row)
            new_row["persona"] = "combined"
            out.append(new_row)
    return out


def excluded_rows(data):
    return [r for r in data if r["l2"] not in VALID_L2]


# ---------------------------------------------------------------------------
# ANALYSIS FUNCTIONS
# ---------------------------------------------------------------------------

def denominator_key(data):
    rows = []
    for cat in CATEGORY_ORDER:
        row = [cat]
        for p in PERSONA_ORDER:
            n = sum(1 for r in data if r["persona"] == p and r["category"] == cat)
            row.append(n)
        rows.append(row)
    total = ["TOTAL"]
    for p in PERSONA_ORDER:
        total.append(sum(1 for r in data if r["persona"] == p))
    rows.append(total)
    return rows


def layer1_by_persona(data):
    rows = []
    for p in PERSONA_ORDER:
        sub = [r for r in data if r["persona"] == p]
        n = len(sub)
        codes = Counter(primary_code(r["l1"]) for r in sub)
        srm = codes.get("SRM", 0)
        nfp = codes.get("NFP", 0)
        rd = codes.get("RD", 0)
        rbk = codes.get("R/BK", 0)
        rows.append([
            p, n,
            round(100 * srm / n, 1) if n else 0,
            round(100 * nfp / n, 1) if n else 0,
            round(100 * rd / n, 1) if n else 0,
            round(100 * rbk / n, 1) if n else 0,
        ])
    return rows


def layer1_full_codes(data):
    rows = []
    for p in PERSONA_ORDER:
        sub = [r for r in data if r["persona"] == p]
        n = len(sub)
        counts = Counter(str(r["l1"]).strip() for r in sub if r["l1"])
        for code, cnt in sorted(counts.items(), key=lambda kv: -kv[1]):
            rows.append([p, code, cnt, round(100 * cnt / n, 1) if n else 0])
    return rows


def layer2_by_persona(data):
    rows = []
    for p in PERSONA_ORDER:
        sub = [r for r in data if r["persona"] == p]
        valid = [r for r in sub if r["l2"] in VALID_L2]
        n = len(valid)
        h = sum(1 for r in valid if r["l2"] == "Harmful")
        nh = n - h
        excluded = len(sub) - n
        rows.append([
            p, n, excluded,
            round(100 * h / n, 1) if n else 0,
            round(100 * nh / n, 1) if n else 0,
        ])
    return rows


def rate_by_category(data, kind):
    """kind: 'harmful', 'srm', 'rbk' -- % of category's trials with that outcome
    (using valid L2 rows for 'harmful'; primary L1 code otherwise)."""
    rows = []
    for cat in CATEGORY_ORDER:
        row = [cat]
        for p in PERSONA_ORDER:
            sub = [r for r in data if r["persona"] == p and r["category"] == cat]
            if kind == "harmful":
                valid = [r for r in sub if r["l2"] in VALID_L2]
                n = len(valid)
                pct = 100 * sum(1 for r in valid if r["l2"] == "Harmful") / n if n else 0
            else:
                n = len(sub)
                target = "SRM" if kind == "srm" else "R/BK"
                pct = 100 * sum(1 for r in sub if primary_code(r["l1"]) == target) / n if n else 0
            row.append(round(pct, 1))
        rows.append(row)
    return rows


def ai_role_by_persona(data):
    """Facilitator / Enabler %, among Harmful responses only."""
    rows = []
    for p in PERSONA_ORDER:
        sub = [r for r in data if r["persona"] == p and r["l2"] == "Harmful"]
        n = len(sub)
        c = Counter(r["role"] for r in sub if r["role"] in VALID_ROLE)
        fac = c.get("Facilitator", 0)
        ena = c.get("Enabler", 0)
        rows.append([
            p, n,
            round(100 * fac / n, 1) if n else 0,
            round(100 * ena / n, 1) if n else 0,
        ])
    return rows


def relative_shift(data):
    """Macro-averaged (unweighted across categories) Harmful % per persona,
    plus relative % increase vs baseline."""
    macro = {}
    for p in PERSONA_ORDER:
        cat_pcts = []
        for cat in CATEGORY_ORDER:
            sub = [r for r in data if r["persona"] == p and r["category"] == cat]
            valid = [r for r in sub if r["l2"] in VALID_L2]
            n = len(valid)
            pct = 100 * sum(1 for r in valid if r["l2"] == "Harmful") / n if n else 0
            cat_pcts.append(pct)
        macro[p] = sum(cat_pcts) / len(cat_pcts)
    base = macro["baseline"]
    rows = []
    for p in PERSONA_ORDER:
        rel = 100 * (macro[p] - base) / base if base else float("nan")
        rows.append([p, round(macro[p], 1), round(rel, 1) if p != "baseline" else "-"])
    return rows


def mixed_features(data):
    rows = []
    for p in PERSONA_ORDER:
        sub = [r for r in data if r["persona"] == p and r["mixed"]]
        counts = Counter(str(r["mixed"]).strip() for r in sub)
        for feature, cnt in sorted(counts.items(), key=lambda kv: -kv[1]):
            rows.append([p, feature, cnt])
    return rows


def repetition_stability(data):
    rows = []
    for p in REAL_PERSONAS:  # combined doesn't have its own scenario reps
        for cat in CATEGORY_ORDER:
            scenarios = sorted(set(r["scenario"] for r in data
                                    if r["persona"] == p and r["category"] == cat))
            for sc in scenarios:
                sub = [r for r in data if r["persona"] == p and r["scenario"] == sc]
                sub_sorted = sorted(sub, key=lambda r: r["run_number"])
                codes = [primary_code(r["l1"]) for r in sub_sorted]
                majority = Counter(codes).most_common(1)[0]
                stable = "Yes" if majority[1] >= 4 else ("Mixed" if majority[1] == 3 else "No")
                rows.append([p, sc, cat, ", ".join(codes), majority[0], majority[1], stable])
    return rows


def average_per_scenario(data):
    """Harmful % per scenario, averaged across its 5 reps."""
    rows = []
    for p in PERSONA_ORDER:
        scenarios = sorted(set(r["scenario"] for r in data if r["persona"] == p))
        for sc in scenarios:
            sub = [r for r in data if r["persona"] == p and r["scenario"] == sc]
            cat = sub[0]["category"]
            valid = [r for r in sub if r["l2"] in VALID_L2]
            n = len(valid)
            pct = 100 * sum(1 for r in valid if r["l2"] == "Harmful") / n if n else 0
            rows.append([p, sc, cat, n, round(pct, 1)])
    return rows


def subcategory_by_persona(data):
    """Zhang et al. (2025) transgression subcategory, among Harmful responses.
    NOTE: not every Harmful response received a subcategory code -- only a
    subset were coded this way (the rest are 'N/A' even though L2=Harmful).
    n_Harmful is shown alongside n_subcoded so this isn't misread as 100% coverage."""
    rows = []
    for p in PERSONA_ORDER:
        sub = [r for r in data if r["persona"] == p and r["l2"] == "Harmful"]
        n_harmful = len(sub)
        subcoded = [r for r in sub if r["subcat"] != "N/A"]
        n_subcoded = len(subcoded)
        counts = Counter(r["subcat"] for r in subcoded)
        if not counts:
            rows.append([p, n_harmful, n_subcoded, "-", 0, 0])
            continue
        for subcat, cnt in sorted(counts.items(), key=lambda kv: -kv[1]):
            pct_of_subcoded = round(100 * cnt / n_subcoded, 1) if n_subcoded else 0
            pct_of_harmful = round(100 * cnt / n_harmful, 1) if n_harmful else 0
            rows.append([p, n_harmful, n_subcoded, subcat, cnt, pct_of_subcoded, pct_of_harmful])
    return rows


def relative_shift_by_code(data):
    """(%persona - %baseline) / %baseline for EACH response-type code
    (SRM, NFP, RD, R/BK), using the persona-level Layer1_by_persona %s
    (trial-pooled, not macro-averaged across category -- these are single
    overall rates per persona so macro-averaging doesn't apply here)."""
    l1 = {row[0]: row for row in layer1_by_persona(data)}  # persona -> row
    codes = [("SRM", 2), ("NFP", 3), ("RD", 4), ("R/BK", 5)]
    rows = []
    base_row = l1["baseline"]
    for p in PERSONA_ORDER:
        prow = l1[p]
        for code_name, col in codes:
            base_pct = base_row[col]
            p_pct = prow[col]
            if p == "baseline":
                rel = "-"
            elif base_pct == 0:
                rel = "undefined (baseline=0)"
            else:
                rel = round(100 * (p_pct - base_pct) / base_pct, 1)
            rows.append([p, code_name, p_pct, base_pct, rel])
    return rows


def nfp_rd_by_category(data):
    """Same shape as rate_by_category but for NFP and RD, so all four
    response types (SRM, NFP, RD, R/BK) have their own by-category sheet."""
    nfp_rows, rd_rows = [], []
    for cat in CATEGORY_ORDER:
        row_nfp, row_rd = [cat], [cat]
        for p in PERSONA_ORDER:
            sub = [r for r in data if r["persona"] == p and r["category"] == cat]
            n = len(sub)
            nfp_pct = 100 * sum(1 for r in sub if primary_code(r["l1"]) == "NFP") / n if n else 0
            rd_pct = 100 * sum(1 for r in sub if primary_code(r["l1"]) == "RD") / n if n else 0
            row_nfp.append(round(nfp_pct, 1))
            row_rd.append(round(rd_pct, 1))
        nfp_rows.append(row_nfp)
        rd_rows.append(row_rd)
    return nfp_rows, rd_rows


def harmful_composition(data):
    """Of all Harmful responses for 'combined', how many came from girlfriend
    vs boyfriend -- makes the pooling transparent."""
    sub = [r for r in data if r["persona"] == "combined" and r["l2"] == "Harmful"]
    # combined rows are duplicated from girlfriend/boyfriend originals; recover
    # source persona from run_id prefix
    src = Counter(r["run_id"].split("_")[0] for r in sub)
    total = sum(src.values())
    rows = []
    for p in ROMANTIC_PERSONAS:
        cnt = src.get(p, 0)
        rows.append([p, cnt, round(100 * cnt / total, 1) if total else 0])
    return rows


# ---------------------------------------------------------------------------
# STATISTICAL TESTS (Cochran's Q + pairwise McNemar, Holm-corrected)
# ---------------------------------------------------------------------------

def cochrans_q(data):
    """Each scenario/persona cell collapsed to Harmful(1)/Non-harmful(0) via
    majority vote across its 5 reps, TIES BROKEN TOWARD HARMFUL (partial-harm
    rule). Omnibus test via statsmodels' cochrans_q; pairwise via statsmodels'
    EXACT binomial McNemar (not the chi-square approximation), Holm-corrected."""
    scenarios = sorted(set(r["scenario"] for r in data if r["persona"] == "baseline"))
    matrix, included = [], []
    for sid in scenarios:
        row_vals, complete = [], True
        for p in REAL_PERSONAS:
            sub = [r for r in data if r["persona"] == p and r["scenario"] == sid
                   and r["l2"] in VALID_L2]
            if not sub:
                complete = False
                break
            counts = Counter(r["l2"] for r in sub)
            row_vals.append(1 if counts["Harmful"] >= counts["Non-harmful"] else 0)
        if complete:
            matrix.append(row_vals)
            included.append(sid)
    arr = np.array(matrix)

    q_result = sm_cochrans_q(arr)
    Q, p_val = q_result.statistic, q_result.pvalue
    df = len(REAL_PERSONAS) - 1

    pairs = [(0, 1), (0, 2), (1, 2)]  # baseline-gf, baseline-bf, gf-bf
    pvals_raw, pair_meta = [], []
    for ia, ib in pairs:
        col_a, col_b = arr[:, ia], arr[:, ib]
        both1 = int(((col_a == 1) & (col_b == 1)).sum())
        a1b0 = int(((col_a == 1) & (col_b == 0)).sum())
        a0b1 = int(((col_a == 0) & (col_b == 1)).sum())
        both0 = int(((col_a == 0) & (col_b == 0)).sum())
        table = [[both1, a1b0], [a0b1, both0]]
        result = sm_mcnemar(table, exact=True)
        pvals_raw.append(result.pvalue)
        pair_meta.append((REAL_PERSONAS[ia], REAL_PERSONAS[ib], a1b0, a0b1, result.statistic, result.pvalue))

    reject, pvals_corrected, _, _ = multipletests(pvals_raw, alpha=0.05, method="holm")
    pair_results = []
    for (a, b, b_val, c_val, stat, raw_p), corr_p, sig in zip(pair_meta, pvals_corrected, reject):
        pair_results.append([a, b, b_val, c_val, round(float(stat), 4) if stat is not None else stat,
                              round(raw_p, 4), round(corr_p, 4), "Yes" if sig else "No"])

    return Q, df, p_val, pair_results, len(included), len(scenarios)


# ---------------------------------------------------------------------------
# CHART BUILDERS -- seaborn-darkgrid style, one standalone PNG per chart.
# Legends are always placed below the axes so they never overlap the bars.
# ---------------------------------------------------------------------------

def save_fig(fig, name):
    path = os.path.join(FIG_DIR, f"{name}.png")
    fig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def bar_chart_single(categories, values, title, ylabel, name, ylim=None):
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.bar(categories, values, color=SINGLE_BAR_COLOR, width=0.6)
    for i, v in enumerate(values):
        ax.text(i, v + (max(values) * 0.02 if max(values) else 0.5), f"{v:.1f}",
                 ha="center", va="bottom", fontsize=9)
    ax.set_title(title, fontsize=12, fontweight="bold", pad=10)
    ax.set_ylabel(ylabel, fontsize=10)
    if ylim:
        ax.set_ylim(*ylim)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(axis="x", rotation=20)
    plt.tight_layout()
    return save_fig(fig, name)


def bar_chart_grouped(categories, series_dict, title, ylabel, name):
    fig, ax = plt.subplots(figsize=(9.5, 5.2))
    n = len(series_dict)
    x = np.arange(len(categories))
    width = 0.8 / n
    for i, (label, vals) in enumerate(series_dict.items()):
        ax.bar(x + i * width - 0.4 + width / 2, vals, width, label=label, color=PALETTE[i % len(PALETTE)])
    ax.set_xticks(x)
    ax.set_xticklabels(categories, rotation=35, ha="right", fontsize=9)
    ax.set_title(title, fontsize=12, fontweight="bold", pad=10)
    ax.set_ylabel(ylabel, fontsize=10)
    # Legend always below the axes -- never overlaps the bars regardless of data height.
    ax.legend(frameon=False, fontsize=9, loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=n)
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    return save_fig(fig, name)


def stacked_bar(categories, series_dict, title, ylabel, name):
    fig, ax = plt.subplots(figsize=(7, 5))
    bottom = np.zeros(len(categories))
    for i, (label, vals) in enumerate(series_dict.items()):
        vals = np.array(vals, dtype=float)
        ax.bar(categories, vals, bottom=bottom, label=label, color=PALETTE[i % len(PALETTE)], width=0.6)
        bottom += vals
    ax.set_title(title, fontsize=12, fontweight="bold", pad=10)
    ax.set_ylabel(ylabel, fontsize=10)
    ax.legend(frameon=False, fontsize=9, loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=len(series_dict))
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    return save_fig(fig, name)


def heatmap_chart(categories, response_types, matrix, title, name):
    fig, ax = plt.subplots(figsize=(6.5, 5.8))
    ax.grid(False)
    ax.set_facecolor("white")
    im = ax.imshow(matrix, cmap=HEATMAP_CMAP, vmin=0, vmax=100, aspect="auto")
    ax.set_xticks(range(len(response_types))); ax.set_xticklabels(response_types, fontsize=9)
    ax.set_yticks(range(len(categories))); ax.set_yticklabels(categories, fontsize=9)
    for i in range(len(categories)):
        for j in range(len(response_types)):
            val = matrix[i][j]
            ax.text(j, i, f"{val:.0f}", ha="center", va="center", fontsize=8,
                     color="white" if val < 50 else "black")
    ax.set_title(title, fontsize=12, fontweight="bold", pad=10)
    # Single-axes colorbar -- no overlap risk once charts are one-per-figure.
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("% of category's trials", fontsize=9)
    plt.tight_layout()
    return save_fig(fig, name)


def _muted_color_func(word=None, font_size=None, position=None, orientation=None,
                        font_path=None, random_state=None):
    """Cycles through a small fixed set of muted colours instead of a
    rainbow matplotlib colormap -- calmer, more consistent look."""
    return WORDCLOUD_COLORS[(random_state.randint(0, 10**6) if random_state else 0) % len(WORDCLOUD_COLORS)]


def wordcloud_image(text, title, name):
    if not HAVE_WORDCLOUD or not text.strip():
        return None
    stop = set(STOPWORDS) | FILLER_WORDS
    wc = WordCloud(width=900, height=520, background_color="white", stopwords=stop,
                    max_words=50, prefer_horizontal=0.95,
                    color_func=_muted_color_func).generate(text)
    fig, ax = plt.subplots(figsize=(7.5, 4.3))
    ax.imshow(wc, interpolation="bilinear")
    ax.axis("off")
    ax.set_title(title, fontsize=12, fontweight="bold", pad=10)
    plt.tight_layout()
    return save_fig(fig, name)


# Persona -> border colour, matching the bar-chart colour convention used
# elsewhere (navy for baseline/single-series, then the purple-pink-orange
# multi-series palette in the same order as PERSONA_ORDER).
PERSONA_BORDER_COLOR = {
    "baseline": SINGLE_BAR_COLOR,
    "girlfriend": PALETTE[1],
    "boyfriend": PALETTE[3],
    "combined": PALETTE[4],
}


def wordcloud_grid(text_by_persona, title, name):
    """2x2 grid, one panel per persona, each with a coloured border matching
    that persona's colour elsewhere in the workbook."""
    if not HAVE_WORDCLOUD:
        return None
    stop = set(STOPWORDS) | FILLER_WORDS
    fig, axes = plt.subplots(2, 2, figsize=(11, 7))
    axes = axes.reshape(-1)
    for i, persona in enumerate(PERSONA_ORDER):
        ax = axes[i]
        text = text_by_persona.get(persona, "")
        if text.strip():
            wc = WordCloud(width=700, height=420, background_color="white", stopwords=stop,
                            max_words=40, prefer_horizontal=0.95,
                            color_func=_muted_color_func).generate(text)
            ax.imshow(wc, interpolation="bilinear")
        ax.set_title(persona.capitalize(), fontsize=11, fontweight="bold", pad=8,
                      color=PERSONA_BORDER_COLOR[persona])
        ax.set_xticks([]); ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_color(PERSONA_BORDER_COLOR[persona])
            spine.set_linewidth(3)
    fig.suptitle(title, fontsize=14, fontweight="bold", y=1.02)
    plt.tight_layout()
    return save_fig(fig, name)


def plain_table_image(row_labels, col_labels, matrix, title, name, value_suffix="%", figsize=None):
    """A genuinely plain table -- white background, black text, thin grey
    borders, bold header row and row labels. No colour shading at all.
    Intended for use as a table in the Results chapter."""
    n_rows, n_cols = len(row_labels), len(col_labels)
    max_label_len = max(len(str(rl)) for rl in row_labels)
    label_units = max(1.1, max_label_len * 0.105)
    if figsize is None:
        figsize = (1.3 * n_cols + label_units + 0.8, 0.5 * n_rows + 1.3)
    fig, ax = plt.subplots(figsize=figsize)
    ax.set_xlim(0, n_cols + label_units)
    ax.set_ylim(0, n_rows + 1)
    ax.invert_yaxis()
    ax.axis("off")
    ax.set_facecolor("white")

    for j, col in enumerate(col_labels):
        ax.text(label_units + j + 0.5, 0.5, col, ha="center", va="center", fontsize=10, fontweight="bold")
    ax.plot([label_units, n_cols + label_units], [1, 1], color="#333333", linewidth=1.3)

    for i, row_label in enumerate(row_labels):
        ax.text(label_units - 0.15, i + 1.5, row_label, ha="right", va="center", fontsize=10, fontweight="bold")
        for j, val in enumerate(matrix[i]):
            ax.add_patch(plt.Rectangle((label_units + j, i + 1), 1, 1, facecolor="white",
                                         edgecolor="#CCCCCC", linewidth=0.8))
            label = f"{val:.1f}{value_suffix}"
            ax.text(label_units + j + 0.5, i + 1.5, label, ha="center", va="center", fontsize=9.5, color="black")

    ax.set_title(title, fontsize=13, fontweight="bold", pad=14, loc="left")
    plt.tight_layout()
    return save_fig(fig, name)


def clean_text(raw):
    if not raw:
        return ""
    # Delete apostrophes FIRST so contractions collapse into one token
    # ("don't" -> "dont", "I'm" -> "Im") instead of splitting into
    # meaningless fragments ("don"+"t", ""+"m") when the apostrophe is
    # replaced with a space by the next step.
    text = raw.replace("'", "").replace("\u2019", "")
    return re.sub(r"[^a-zA-Z\s]", " ", text)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    print(f"Loading {INPUT_FILE} ...")
    raw = load_rows(INPUT_FILE)
    data = with_combined(raw)
    excl = excluded_rows(raw)

    print(f"  {len(raw)} raw rows, {len(excl)} excluded (unresolved L2), "
          f"{len(data)} rows after adding combined")

    wb = Workbook()
    wb.remove(wb.active)

    # ---- Notes sheet -------------------------------------------------
    ws = wb.create_sheet("Notes")
    ws.column_dimensions["A"].width = 110
    notes = [
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} from {os.path.basename(INPUT_FILE)} by build_results.py.",
        "",
        "PERSONA ORDER (fixed throughout): baseline, girlfriend, boyfriend, combined.",
        "'combined' = girlfriend + boyfriend rows pooled (n=130), i.e. all romantic-persona trials together.",
        "",
        "DENOMINATORS: Layer 2 (Harmful/Non-harmful) percentages exclude rows where the coding is unresolved.",
        f"Currently {len(excl)} row(s) excluded: " + "; ".join(f"{r['run_id']} (L2='{r['l2']}')" for r in excl) + ".",
        "See Denominator_key for the exact n each % in Harmful_by_category / SRM_by_category / RBK_by_category is out of.",
        "",
        "LAYER 1 (response type): Layer1_by_persona uses the PRIMARY (first-listed) code for compound entries",
        "e.g. 'R/BK and SRM' -> counted as R/BK. See Layer1_full_codes for the untouched compound strings.",
        "",
        "AI_role_by_persona: Facilitator/Enabler %, calculated ONLY among Harmful responses for that persona",
        "(role is not meaningful for Non-harmful responses, coded as N/A in the source sheet).",
        "",
        "RELATIVE_SHIFT: macro-averaged Harmful % = simple mean of the 9 category-level Harmful rates",
        "(each category weighted equally regardless of trial count), NOT the trial-pooled overall %.",
        "This is why Relative_shift's per-persona Harmful % differs slightly from Layer2_by_persona's.",
        "",
        "STATISTICAL_TESTS: Cochran's Q (statsmodels) on scenario-level majority-Harmful votes",
        "(baseline/girlfriend/boyfriend only -- 'combined' is not an independent condition so is excluded),",
        "ties broken toward Harmful (partial-harm rule), followed by pairwise EXACT binomial McNemar tests",
        "(statsmodels, exact=True) with Holm-Bonferroni correction across the 3 pairwise comparisons.",
        "Exact tests are used throughout, not chi-square approximations.",
        "",
        "All figures use a seaborn-darkgrid aesthetic (light grey-blue panel background, white gridlines,",
        "muted multi-colour palette, warm colormap for heatmaps). Each chart is saved as its own PNG;",
        "word clouds are saved as one 2x2 grid (one panel per persona) per subset.",
        "",
        "Percentage tables (Harmful/SRM/RBK/NFP/RD_by_category, Layer2_by_persona, AI_role_by_persona) use a",
        "green conditional-formatting colour scale on their numeric columns -- pale for low values, darker",
        "green for high values -- so the tables themselves read as heat maps.",
        "",
        "SUBCATEGORY_BY_PERSONA: Zhang et al. (2025) transgression subcategory was NOT assigned to every",
        "Harmful response -- only a subset were subcategory-coded. n_Harmful and n_subcategory-coded are shown",
        "separately so this isn't misread as full coverage. Messy 'n/a' / 'not applicable' / 'non applicable'",
        "variants in the raw sheet were normalised to a single 'N/A' before counting.",
        "",
        "RELATIVE_SHIFT_BY_CODE: relative shift calculated PER response-type code (SRM/NFP/RD/R-BK) as",
        "(persona% - baseline%)/baseline%, as defined in the Methodology -- distinct from Relative_shift,",
        "which only covers the Harmful/Non-harmful judgment, macro-averaged across categories.",
    ]
    for i, line in enumerate(notes, start=1):
        c = ws.cell(row=i, column=1, value=line)
        if i == 1:
            c.font = TITLE_FONT
        else:
            c.font = NOTE_FONT

    # ---- Denominator_key ----------------------------------------------
    ws = wb.create_sheet("Denominator_key")
    write_table(ws, ["Abuse category"] + PERSONA_ORDER, denominator_key(data),
                title="n trials per category x persona (denominators for the % sheets)")
    autosize(ws)

    # ---- Layer1_by_persona ---------------------------------------------
    ws = wb.create_sheet("Layer1_by_persona")
    write_table(ws, ["Persona", "n coded", "SRM %", "NFP %", "RD %", "R/BK %"],
                layer1_by_persona(data), title="Primary response type (first-listed code) by persona")
    autosize(ws)

    # ---- Layer1_full_codes ----------------------------------------------
    ws = wb.create_sheet("Layer1_full_codes")
    write_table(ws, ["Persona", "Full code as entered", "Count", "% of persona rows"],
                layer1_full_codes(data), title="Untouched compound codes (see Notes)")
    autosize(ws)

    # ---- Layer2_by_persona ---------------------------------------------
    ws = wb.create_sheet("Layer2_by_persona")
    write_table_heatmap(ws, ["Persona", "n valid", "n excluded", "Harmful %", "Non-harmful %"],
                layer2_by_persona(data), title="Overall harm judgment by persona")
    autosize(ws)

    # ---- Harmful / SRM / RBK by category ---------------------------------
    harm_rows = rate_by_category(data, "harmful")
    srm_rows = rate_by_category(data, "srm")
    rbk_rows = rate_by_category(data, "rbk")

    ws = wb.create_sheet("Harmful_by_category")
    write_table_heatmap(ws, ["Abuse category"] + PERSONA_ORDER, harm_rows,
                title="Harmful rate WITHIN each category (see Denominator_key for n)")
    autosize(ws)

    ws = wb.create_sheet("SRM_by_category")
    write_table_heatmap(ws, ["Abuse category"] + PERSONA_ORDER, srm_rows,
                title="SRM (supportive reinforcement/mirroring) rate WITHIN each category, incl. combined")
    autosize(ws)

    ws = wb.create_sheet("RBK_by_category")
    write_table_heatmap(ws, ["Abuse category"] + PERSONA_ORDER, rbk_rows,
                title="R/BK (rejection/boundary-keeping) rate WITHIN each category, incl. combined")
    autosize(ws)

    nfp_rows, rd_rows = nfp_rd_by_category(data)
    ws = wb.create_sheet("NFP_by_category")
    write_table_heatmap(ws, ["Abuse category"] + PERSONA_ORDER, nfp_rows,
                title="NFP (neutral factual/passive) rate WITHIN each category, incl. combined")
    autosize(ws)

    ws = wb.create_sheet("RD_by_category")
    write_table_heatmap(ws, ["Abuse category"] + PERSONA_ORDER, rd_rows,
                title="RD (redirection) rate WITHIN each category, incl. combined")
    autosize(ws)

    # ---- AI_role_by_persona ---------------------------------------------
    ws = wb.create_sheet("AI_role_by_persona")
    write_table_heatmap(ws, ["Persona", "n Harmful", "Facilitator %", "Enabler %"],
                ai_role_by_persona(data),
                title="AI role among Harmful responses only")
    autosize(ws)

    # ---- Harmful_composition ----------------------------------------------
    ws = wb.create_sheet("Harmful_composition")
    write_table(ws, ["Source persona", "n Harmful", "% of combined Harmful total"],
                harmful_composition(data),
                title="Of combined's Harmful responses, how many came from girlfriend vs boyfriend")
    autosize(ws)

    # ---- Relative_shift ----------------------------------------------------
    ws = wb.create_sheet("Relative_shift")
    write_table(ws, ["Persona", "Macro-avg Harmful %", "Relative shift vs baseline (%)"],
                relative_shift(data), title="Macro-averaged (category-equal-weighted) Harmful rate")
    autosize(ws)

    # ---- Relative_shift_by_code -------------------------------------------
    ws = wb.create_sheet("Relative_shift_by_code")
    write_table(ws, ["Persona", "Code", "Persona %", "Baseline %", "Relative shift (%)"],
                relative_shift_by_code(data),
                title="Relative shift = (persona% - baseline%) / baseline%, per response-type code")
    autosize(ws)

    # ---- Subcategory_by_persona (Zhang et al. 2025) ------------------------
    ws = wb.create_sheet("Subcategory_by_persona")
    write_table(ws, ["Persona", "n Harmful", "n subcategory-coded", "Subcategory", "Count",
                      "% of subcoded", "% of all Harmful"],
                subcategory_by_persona(data),
                title="Zhang et al. (2025) transgression subcategory -- NOT every Harmful response was subcategory-coded (see n subcategory-coded vs n Harmful)")
    autosize(ws)

    # ---- Mixed_features ------------------------------------------------
    ws = wb.create_sheet("Mixed_features")
    write_table(ws, ["Persona", "Feature", "Count"], mixed_features(data),
                title="Inductive mixed-categorisation features by persona")
    autosize(ws)

    # ---- Average_per_scenario --------------------------------------------
    ws = wb.create_sheet("Average_per_scenario")
    write_table(ws, ["Persona", "Scenario", "Category", "n valid", "Harmful %"],
                average_per_scenario(data), title="Harmful % per scenario (averaged across 5 reps)")
    autosize(ws)

    # ---- Repetition_stability --------------------------------------------
    ws = wb.create_sheet("Repetition_stability")
    write_table(ws, ["Persona", "Scenario", "Category", "Codes (r1-r5)", "Majority code",
                      "Majority count", "Stable?"],
                repetition_stability(data),
                title="Within-scenario response-type stability across the 5 repetitions")
    autosize(ws)

    # ---- Statistical_tests -------------------------------------------------
    ws = wb.create_sheet("Statistical_tests")
    try:
        Q, df, p_val, pairs, n_incl, n_total = cochrans_q(data)
        r = 1
        ws.cell(row=r, column=1, value="Cochran's Q test (baseline vs girlfriend vs boyfriend, scenario-level majority-Harmful vote, ties broken toward Harmful)").font = TITLE_FONT
        r += 1
        ws.cell(row=r, column=1,
                value=f"Scenarios included: {n_incl}/{n_total} (all complete across the 3 conditions -- combined excluded, not an independent condition)").font = NOTE_FONT
        r += 2
        r = write_table(ws, ["Q statistic", "df", "p-value"],
                         [[round(Q, 4), df, round(p_val, 4)]], start_row=r)
        r = write_table(ws, ["Persona A", "Persona B", "A-only (b)", "B-only (c)",
                              "McNemar stat (exact)", "raw p", "Holm-adjusted p", "Significant (Holm, a=0.05)"],
                         pairs, start_row=r,
                         title="Pairwise McNemar tests (exact binomial, statsmodels), Holm-Bonferroni corrected")
        note = ws.cell(row=r, column=1,
                        value="Note: with only 13 scenarios, this test is under-powered -- treat as a documented limitation, not a null result. Reported for transparency; descriptive statistics remain the primary analytical approach.")
        note.font = NOTE_FONT
    except ImportError:
        ws.cell(row=1, column=1, value="statsmodels not available -- install statsmodels to compute Cochran's Q / McNemar tests.").font = NOTE_FONT
    autosize(ws)

    # ---- Figures (individual PNGs, seaborn-style, embedded one per row) -----
    ws = wb.create_sheet("Figures")
    row_cursor = 1

    harm2 = layer2_by_persona(data)
    l1 = layer1_by_persona(data)
    role_rows = ai_role_by_persona(data)
    subcat_rows = subcategory_by_persona(data)
    cats = [r[0] for r in harm_rows]

    def embed(path):
        nonlocal row_cursor
        img = XLImage(path); img.anchor = f"A{row_cursor}"; ws.add_image(img)
        row_cursor += 24

    # ---- Table images (plain) ---
    # Only SRM/R-BK by-category tables are rendered here -- Harmful-by-category,
    # overall Harmful/Non-harmful, and AI role are already shown as bar
    # charts below (fig2, fig1, fig5), so a duplicate table would just
    # repeat the same numbers in a second format.
    embed(plain_table_image(cats, PERSONA_ORDER, [row[1:] for row in srm_rows],
        "SRM Rate by Category", "table1_srm_by_category"))
    embed(plain_table_image(cats, PERSONA_ORDER, [row[1:] for row in rbk_rows],
        "R/BK Rate by Category", "table2_rbk_by_category"))

    embed(bar_chart_single(PERSONA_ORDER, [r[3] for r in harm2],
        "Overall Harmful Rate by Condition", "Harmful %", "fig1_overall_harmful", ylim=(0, 100)))

    embed(bar_chart_grouped(cats, {p: [r[i + 1] for r in harm_rows] for i, p in enumerate(REAL_PERSONAS)},
        "Harmful Rate by Category", "Harmful %", "fig2_harmful_by_category"))

    embed(bar_chart_grouped(cats, {p: [r[i + 2] for r in harm_rows] for i, p in enumerate(["girlfriend", "boyfriend"])},
        "Harmful Rate by Category: Girlfriend vs Boyfriend", "Harmful %", "fig3_harmful_gf_vs_bf"))

    embed(stacked_bar([row[0] for row in l1],
        {"SRM": [row[2] for row in l1], "NFP": [row[3] for row in l1],
         "RD": [row[4] for row in l1], "R/BK": [row[5] for row in l1]},
        "Response-Type Composition by Persona", "% of trials", "fig4_response_type_composition"))

    embed(stacked_bar([row[0] for row in role_rows],
        {"Facilitator": [r[2] for r in role_rows], "Enabler": [r[3] for r in role_rows]},
        "AI Role Among Harmful Responses, by Persona", "% of Harmful trials", "fig5_ai_role"))

    subcat_by_persona_pct = defaultdict(dict)
    for row in subcat_rows:
        persona, n_harm, n_sub, subcat, cnt = row[0], row[1], row[2], row[3], row[4]
        pct_sub = row[5]
        if subcat != "-":
            subcat_by_persona_pct[persona][subcat] = pct_sub
    all_subcats = sorted({sc for d in subcat_by_persona_pct.values() for sc in d})
    embed(stacked_bar(PERSONA_ORDER,
        {sc: [subcat_by_persona_pct[p].get(sc, 0) for p in PERSONA_ORDER] for sc in all_subcats},
        "Harm Subcategory Among Subcategory-Coded Harmful Responses", "% of subcategory-coded responses",
        "fig6_subcategory"))

    response_types = ["SRM", "NFP", "RD", "R/BK"]
    for p in PERSONA_ORDER:
        matrix = []
        for cat in CATEGORY_ORDER:
            sub = [r for r in data if r["persona"] == p and r["category"] == cat]
            n = len(sub)
            codes = Counter(primary_code(r["l1"]) for r in sub)
            matrix.append([100 * codes.get(rt, 0) / n if n else 0 for rt in response_types])
        embed(heatmap_chart(CATEGORY_ORDER, response_types, matrix,
            f"Response Type by Abuse Category ({p.capitalize()})", f"fig7_heatmap_{p}"))

    # ---- Word clouds (2x2 grid per subset, one panel per persona) -----------
    if HAVE_WORDCLOUD:
        fig_counter = 8
        for subset_name, l2filter in [("all responses", None), ("Harmful responses only", "Harmful"),
                                        ("Non-harmful responses only", "Non-harmful")]:
            text_by_persona = {}
            for p in PERSONA_ORDER:
                sub = [r for r in data if r["persona"] == p and (l2filter is None or r["l2"] == l2filter)]
                text_by_persona[p] = " ".join(clean_text(r["raw_text"]).lower() for r in sub)
            path = wordcloud_grid(text_by_persona, f"Word Frequency by Persona: {subset_name.capitalize()}",
                                    f"fig{fig_counter}_wordcloud_grid_{subset_name.split()[0].lower()}")
            if path:
                embed(path)
            fig_counter += 1
    else:
        print("  [!] wordcloud package not installed -- skipping word cloud figures. "
              "pip install wordcloud")

    # ---- Sheet order / index ------------------------------------------------
    order = ["Notes", "Denominator_key", "Layer1_by_persona", "Layer1_full_codes",
             "Layer2_by_persona", "Harmful_by_category", "SRM_by_category", "RBK_by_category",
             "NFP_by_category", "RD_by_category", "AI_role_by_persona", "Subcategory_by_persona",
             "Harmful_composition", "Relative_shift", "Relative_shift_by_code", "Mixed_features",
             "Average_per_scenario", "Repetition_stability", "Statistical_tests", "Figures"]
    wb._sheets = [wb[name] for name in order if name in wb.sheetnames]

    wb.save(OUTPUT_FILE)
    print(f"\nSaved {OUTPUT_FILE}")
    print(f"Standalone PNG figures in {FIG_DIR}/ for direct Word/Overleaf use.")


if __name__ == "__main__":
    main()
