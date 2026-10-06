#!/bin/bash -l

# ---------------------------------------------------------------------------
# Myriad job script: LLM-judge coding pass (gpt-oss-safeguard:20b)
# Uses Apptainer container for Ollama (native binary fails on Myriad's glibc)
# Uses E/F-type nodes (Tesla V100). 20b fits on a single GPU.
#
# Policy: defaults to policy/coding_policy_A.md. To run another stage, pass
# the policy file as an argument at submission time:
#   qsub scripts/submit_full_job.sh policy/coding_policy_B.md
#   qsub scripts/submit_full_job.sh policy/coding_policy_C.md
#
# Before submitting: set the working directory below (two places) to your
# copy of this repository.
# ---------------------------------------------------------------------------

#$ -l h_rt=3:00:00
#$ -l mem=16G
#$ -l gpu=1
#$ -ac allow=EF
#$ -N llm_judge_full
#$ -wd /home/<your-username>/Scratch/ai-companion-coercive-control-audit
#$ -j n

POLICY_FILE="${1:-policy/coding_policy_A.md}"

# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------

module load python/3.11.4

# ---------------------------------------------------------------------------
# Start Ollama (via Apptainer container) as a background service
# ---------------------------------------------------------------------------

apptainer exec --nv ~/ollama-local/ollama.sif ollama serve > "$TMPDIR/ollama_serve.log" 2>&1 &
OLLAMA_PID=$!

echo "Waiting for Ollama to become ready..."
for i in {1..30}; do
    if apptainer exec --nv ~/ollama-local/ollama.sif ollama list > /dev/null 2>&1; then
        echo "Ollama is up after ${i}s."
        break
    fi
    sleep 1
done

echo "GPU visibility check:"
apptainer exec --nv ~/ollama-local/ollama.sif nvidia-smi --query-gpu=index,name,memory.total,memory.free --format=csv

# ---------------------------------------------------------------------------
# Pull the model if not already cached
# ---------------------------------------------------------------------------

echo "Pulling gpt-oss-safeguard:20b (skips if already cached)..."
apptainer exec --nv ~/ollama-local/ollama.sif ollama pull gpt-oss-safeguard:20b

# ---------------------------------------------------------------------------
# Run the LLM-judge coding pass
# ---------------------------------------------------------------------------

cd "$HOME/Scratch/ai-companion-coercive-control-audit" || exit 1

echo "Starting LLM-judge coding pass (20b) at $(date) using policy: $POLICY_FILE"
python3 scripts/run_llm_judge.py --policy "$POLICY_FILE"
EXIT_CODE=$?
echo "run_llm_judge.py finished at $(date) with exit code $EXIT_CODE"

# ---------------------------------------------------------------------------
# Clean up: stop the background Ollama process
# ---------------------------------------------------------------------------

kill "$OLLAMA_PID" 2>/dev/null

exit $EXIT_CODE
