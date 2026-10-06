# LLM judge policies and outputs

The LLM judge (`gpt-oss-safeguard:20b`) classified all 195 responses three times, each time with a more detailed policy. The policy file was passed to the model as its system prompt.

| Thesis stage | Policy file | Contents | Output file |
|---|---|---|---|
| Stage A | `coding_policy_A.md` | Category labels only | `judge_output_full_coding_policy_A.csv` |
| Stage B | `coding_policy_B.md` | Labels and full definitions | `judge_output_full_coding_policy_B.csv` |
| Stage C | `coding_policy_C.md` | Definitions and interpretive rules | `judge_output_full_coding_policy_C.csv` |

The policy files are exactly as run. Their headings say "Stage 1", "Stage 2" and "Stage 3"; these correspond to Stages A, B and C in the thesis.

## Output files

Each output file contains every column of `data/responses_final.csv` plus the judge's classifications in columns prefixed `llm_`:

- `llm_reasoning`
- `llm_layer1_response_type`
- `llm_layer2_safety_judgment`
- `llm_layer3_ai_role`
- `llm_layer3_transgression_subcat`
- `llm_mixed_categorisation`
- `llm_raw_output`
- `llm_parse_error`

The `model_name` column is the model that generated the response (`gemma4:26b`), not the judge. The un-prefixed coding columns are empty; manual codes are in `codebook/Manual_Coding.xlsx`.

All three runs classified 195 of 195 responses with no parse errors. The persona condition was not shown to the judge.
