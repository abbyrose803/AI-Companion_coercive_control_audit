# Scripts

| Script | What it does | Reads | Writes |
|---|---|---|---|
| `collect_data_final.py` | Runs the 195 trials on `gemma4:26b` through Ollama | Prompts defined in the script | `responses_final.csv` |
| `run_llm_judge.py` | Runs the LLM judge (`gpt-oss-safeguard:20b`) over all responses with one policy | `data/responses_final.csv`, a policy file from `policy/` | `judge_output_full_<policy>.csv` |
| `submit_full_job.sh` | Cluster (SGE) job script that starts Ollama and runs `run_llm_judge.py` | A policy file | Job log |
| `build_results.py` | Builds the persona results tables and figures from the manual coding | `codebook/Manual_Coding.xlsx` | `persona_results_REBUILD.xlsx`, `figs/` |
| `build_stage_results.py` | Builds the LLM judge tables and figures for Stages A–C | The three judge output CSVs in `policy/` | `stage_results.xlsx`, `figs_stages/` |
| `run_persona_analysis_v2.py` | Earlier analysis script, superseded by `build_results.py` | `codebook/Manual_Coding.xlsx` | `persona_results_FINAL.xlsx` and PNGs |

## Running

Install the dependencies with `pip install -r requirements.txt`, then run from the repository root:

```bash
python3 scripts/build_results.py
python3 scripts/build_stage_results.py \
    policy/judge_output_full_coding_policy_A.csv \
    policy/judge_output_full_coding_policy_B.csv \
    policy/judge_output_full_coding_policy_C.csv \
    stage_results.xlsx --labels "Names only" "Full definitions" "Definitions+rules"
```

Both build scripts delete and recreate their figure folder on each run.

`collect_data_final.py` needs Ollama running with `gemma4:26b` pulled. Each trial draws a new random seed, which is saved in the output, so a re-run will not reproduce the original responses unless the saved seeds are reused.
