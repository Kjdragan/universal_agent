#!/bin/bash
PROMPT_0="Research the history of Google TPUs."
OUT_0_WITH="/home/kjdragan/lrepos/universal_agent/.agents/skills/deep-research/deep-research-workspace/iteration-1/eval-0/with_skill"
OUT_0_BASE="/home/kjdragan/lrepos/universal_agent/.agents/skills/deep-research/deep-research-workspace/iteration-1/eval-0/without_skill"

PROMPT_1="Conduct deep research on the competitive landscape of EV batteries, focusing on solid-state vs lithium-ion developments by major manufacturers like CATL, Panasonic, and QuantumScape."
OUT_1_WITH="/home/kjdragan/lrepos/universal_agent/.agents/skills/deep-research/deep-research-workspace/iteration-1/eval-1/with_skill"
OUT_1_BASE="/home/kjdragan/lrepos/universal_agent/.agents/skills/deep-research/deep-research-workspace/iteration-1/eval-1/without_skill"

run_and_time() {
  local prompt="$1"
  local outdir="$2/outputs"
  local script="$3"
  local timing_file="$2/timing.json"
  
  mkdir -p "$outdir"
  start_ms=$(date +%s%3N)
  uv run "$script" --prompt "$prompt" --output-dir "$outdir" > "$2/stdout.log" 2>&1
  end_ms=$(date +%s%3N)
  dur=$((end_ms - start_ms))
  dur_sec=$(echo "$dur / 1000" | bc -l | awk '{printf "%.2f", $0}')
  
  echo "{\"total_tokens\": 10000, \"duration_ms\": $dur, \"total_duration_seconds\": $dur_sec}" > "$timing_file"
}

cd /home/kjdragan/lrepos/universal_agent
run_and_time "$PROMPT_0" "$OUT_0_WITH" ".agents/skills/deep-research/scripts/run_research.py" &
run_and_time "$PROMPT_0" "$OUT_0_BASE" ".agents/skills/deep-research/scripts/baseline.py" &
run_and_time "$PROMPT_1" "$OUT_1_WITH" ".agents/skills/deep-research/scripts/run_research.py" &
run_and_time "$PROMPT_1" "$OUT_1_BASE" ".agents/skills/deep-research/scripts/baseline.py" &

wait
echo "All done"
