# Data

`responses_final.csv` holds the raw model outputs: 195 trials (3 persona conditions × 13 scenarios × 5 repetitions) from `gemma4:26b`, collected with `scripts/collect_data_final.py`.

Each row records the run ID, persona condition, system prompt, scenario, repetition number, the random seed, the generation settings, a timestamp, token counts and the full response text.

Notes:

- The coding columns at the end of the file (`layer1_response_type` etc.) are empty. Manual coding was done in `codebook/Manual_Coding.xlsx`, which is linked to this file by `run_id`.
- `model_digest` is "unknown" on every row: the exact model build was not recorded.
- The response text for `baseline_S10_r4_20260728234926` differs between this file and `Manual_Coding.xlsx`. Both versions are refusals.

Content warning: the prompts depict coercive control and intimate partner abuse, and some responses contain sexualised or violent text.
