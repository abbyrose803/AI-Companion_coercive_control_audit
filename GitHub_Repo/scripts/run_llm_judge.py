"""
LLM-judge coding pass using gpt-oss-safeguard:20b through Ollama.

Reads data/responses_final.csv (195 responses), classifies each response
against the coding policy passed with --policy, and writes a new CSV with the
judge's codes in separate columns prefixed llm_, alongside the original
columns.

The policy file is sent as the system prompt. The three policies used in the
thesis are policy/coding_policy_A.md (labels only), coding_policy_B.md
(labels and definitions) and coding_policy_C.md (definitions and rules).

The persona_condition label is not shown to the judge: it sees only the
scenario prompt and the AI's response, so its classification cannot be
influenced by knowing which persona produced the response.

Usage (from the repository root):
    python3 scripts/run_llm_judge.py --policy policy/coding_policy_A.md
    python3 scripts/run_llm_judge.py --policy policy/coding_policy_C.md --limit 10    # first 10 rows only
    python3 scripts/run_llm_judge.py --policy policy/coding_policy_C.md --run-ids id1,id2

Output is written to the current directory as judge_output_full_<policy>.csv
(or judge_output_pilot_<policy>.csv when --limit or --run-ids is used).
"""

import argparse
import csv
import json
import os
import time
from datetime import datetime
import ollama

MODEL = "gpt-oss-safeguard:20b"
REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
INPUT_FILE = os.path.join(REPO_ROOT, "data", "responses_final.csv")
DEFAULT_POLICY_FILE = os.path.join(REPO_ROOT, "policy", "coding_policy_A.md")

# reasoning listed first to match the reasoning-first field order requested
# in the policy's Output format section
NEW_FIELDS = [
    "llm_reasoning",
    "llm_layer1_response_type",
    "llm_layer2_safety_judgment",
    "llm_layer3_transgression_subcat",
    "llm_layer3_ai_role",
    "llm_mixed_categorisation",
    "llm_raw_output",
    "llm_parse_error",
]


def classify(scenario_prompt: str, response_text: str, policy: str) -> dict:
    """Send one response to gpt-oss-safeguard:20b and parse its JSON verdict."""
    user_content = (
        f"Scenario the user sent the AI: \"{scenario_prompt}\"\n\n"
        f"AI's response to classify:\n\"\"\"\n{response_text}\n\"\"\""
    )
    messages = [
        {"role": "system", "content": policy},
        {"role": "user", "content": user_content},
    ]

    result = ollama.chat(
        model=MODEL,
        messages=messages,
        options={"temperature": 0},  # deterministic classification, not creative generation
    )
    raw = result["message"]["content"]

    # Model may wrap JSON in ```json fences despite instructions -- strip if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
        return {
            "llm_reasoning": parsed.get("reasoning", ""),
            "llm_layer1_response_type": parsed.get("layer1_response_type", ""),
            "llm_layer2_safety_judgment": parsed.get("layer2_safety_judgment", ""),
            "llm_layer3_transgression_subcat": parsed.get("layer3_transgression_subcat", ""),
            "llm_layer3_ai_role": parsed.get("layer3_ai_role", ""),
            "llm_mixed_categorisation": parsed.get("mixed_categorisation", ""),
            "llm_raw_output": raw,
            "llm_parse_error": "",
        }
    except json.JSONDecodeError as e:
        return {
            "llm_reasoning": "",
            "llm_layer1_response_type": "",
            "llm_layer2_safety_judgment": "",
            "llm_layer3_transgression_subcat": "",
            "llm_layer3_ai_role": "",
            "llm_mixed_categorisation": "",
            "llm_raw_output": raw,
            "llm_parse_error": f"JSON parse failed: {e}",
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                         help="Pilot mode: classify only the first N rows.")
    parser.add_argument("--run-ids", type=str, default=None,
                         help="Pilot mode: comma-separated run_ids to classify; "
                              "overrides --limit.")
    parser.add_argument("--policy", type=str, default=DEFAULT_POLICY_FILE,
                         help="Policy markdown file to use (default: policy/coding_policy_A.md).")
    args = parser.parse_args()

    with open(args.policy, "r", encoding="utf-8") as f:
        policy = f.read()
    print(f"Using policy file: {args.policy}")

    pilot = args.limit is not None or args.run_ids is not None
    # output filename includes the policy version so different-policy runs
    # never silently overwrite each other
    policy_tag = os.path.splitext(os.path.basename(args.policy))[0]
    output_file = f"judge_output_pilot_{policy_tag}.csv" if pilot else f"judge_output_full_{policy_tag}.csv"

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames + NEW_FIELDS

    if args.run_ids:
        wanted = set(x.strip() for x in args.run_ids.split(","))
        rows = [r for r in rows if r["run_id"] in wanted]
        missing = wanted - set(r["run_id"] for r in rows)
        if missing:
            print(f"Warning: run_ids not found in {INPUT_FILE}: {missing}")
    elif args.limit:
        rows = rows[:args.limit]

    # Resume support: skip rows already classified if output file exists
    # (pilot runs always start fresh)
    done_ids = set()
    if not pilot and os.path.isfile(output_file):
        with open(output_file, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("llm_layer1_response_type"):
                    done_ids.add(r["run_id"])

    file_exists = (not pilot) and os.path.isfile(output_file)
    mode = "a" if file_exists else "w"

    with open(output_file, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        total = len(rows)
        done = 0
        for row in rows:
            done += 1
            if row["run_id"] in done_ids:
                print(f"[{done}/{total}] {row['run_id']} -- already coded, skipping")
                continue

            print(f"[{done}/{total}] {row['run_id']}")
            try:
                verdict = classify(row["user_prompt_text"], row["raw_response_text"], policy)
            except Exception as e:
                verdict = {k: "" for k in NEW_FIELDS}
                verdict["llm_parse_error"] = f"API call failed: {e}"
                print(f"  !! failed: {e}")

            if pilot:
                print(f"  layer1={verdict['llm_layer1_response_type']!r} "
                      f"layer2={verdict['llm_layer2_safety_judgment']!r} "
                      f"mixed={verdict['llm_mixed_categorisation']!r}")
                print(f"  reasoning: {verdict['llm_reasoning']}")

            out_row = dict(row)
            out_row.update(verdict)
            writer.writerow(out_row)
            f.flush()
            time.sleep(0.2)

    print(f"\nDone. Classified {total - len(done_ids)} new rows -> {output_file}")


if __name__ == "__main__":
    main()
