#!/bin/bash
# Cgroup post-mortem for universal-agent-proactive-demo-nuggets.service.
#
# WHY THIS EXISTS: the unit is Type=oneshot, so its cgroup
# /sys/fs/cgroup/system.slice/<unit> is DESTROYED the moment the unit goes
# inactive. That means memory.events (the "high"/"max"/"oom" counters) and
# memory.pressure -- the only direct evidence of whether MemoryHigh throttling
# actually engaged -- are unreadable after the fact. systemd retains only
# MemoryPeak/MemorySwapPeak, which are a CLAMP reading when the job is pinned at
# its limit and therefore cannot distinguish "needed 1G" from "wanted 6G, got 1G
# and swapped the rest".
#
# ExecStopPost= runs INSIDE the unit's cgroup while it still exists (verified
# empirically on this box with a transient Type=oneshot probe: PHASE=STOPPOST
# saw its own cgroup dir and read memory.events/memory.peak/memory.pressure).
# systemd.service(5) documents that it also runs on abnormal exits, so this
# fires on oom-kill and on timeout too -- exactly the cases we need it for.
#
# Emits one CGPM-prefixed line per metric to the journal, so a whole run's
# memory story is greppable with:
#   journalctl -u universal-agent-proactive-demo-nuggets.service | grep CGPM
set -uo pipefail

CG="/sys/fs/cgroup/system.slice/universal-agent-proactive-demo-nuggets.service"

echo "CGPM result=${SERVICE_RESULT:-?} exit=${EXIT_CODE:-?}/${EXIT_STATUS:-?} cgroup_exists=$([ -d "$CG" ] && echo yes || echo no)"

[ -d "$CG" ] || exit 0

# Single-value files: emit as "CGPM <name>=<value>".
for f in memory.current memory.peak memory.high memory.max \
         memory.swap.current memory.swap.peak memory.swap.max \
         pids.current pids.peak; do
  [ -r "$CG/$f" ] && echo "CGPM $f=$(tr -d '\n' < "$CG/$f" 2>/dev/null)"
done

# Multi-line files: prefix every line so nothing is lost to the journal.
for f in memory.events memory.events.local memory.swap.events \
         memory.pressure io.pressure cpu.pressure cpu.stat; do
  [ -r "$CG/$f" ] && sed "s/^/CGPM $f /" "$CG/$f" 2>/dev/null
done

# The whole point of the 2026-08 investigation: are there leftover `claude`
# grandchildren still charged to this cgroup at teardown?
if [ -r "$CG/cgroup.procs" ]; then
  n=$(wc -l < "$CG/cgroup.procs" 2>/dev/null || echo 0)
  # MUST exclude our own process tree. ExecStopPost runs INSIDE the cgroup it is
  # measuring, so this script and every subshell it spawns appear in cgroup.procs.
  # Counting them made leftover_procs structurally incapable of reading 0 — the
  # first real run reported `leftover_procs=3` when all three were this script
  # (verified: the only pid logged was comm=nuggets_cgroup_). The nightly health
  # report escalates on leftover_procs != 0, so uncorrected this would have fired
  # a false alarm EVERY night and trained the operator to ignore the one signal
  # that proves #1587's process-group reap is still holding.
  self_tree=" $$ $PPID "
  for _ in 1 2 3; do            # a few passes to catch grandchildren of subshells
    for p in $(cat "$CG/cgroup.procs" 2>/dev/null); do
      ppid=$(awk '{print $4}' "/proc/$p/stat" 2>/dev/null)
      case "$self_tree" in *" $ppid "*) case "$self_tree" in *" $p "*) ;; *) self_tree="$self_tree$p " ;; esac ;; esac
    done
  done
  real=0
  for p in $(cat "$CG/cgroup.procs" 2>/dev/null); do
    case "$self_tree" in *" $p "*) continue ;; esac
    # A pid that is already gone by the time we look at it did NOT outlive the run —
    # it is a transient subshell from this script's own command substitutions that
    # raced the cgroup.procs read. Counting those kept the number stuck at 2.
    comm=$(cat "/proc/$p/comm" 2>/dev/null) || continue
    [ -n "$comm" ] || continue
    real=$((real + 1))
    echo "CGPM leftover pid=$p comm=$comm"
  done
  # `total` is the raw cgroup.procs count (includes this script); `leftover_procs`
  # is what actually outlived the run and is the number the health report gates on.
  echo "CGPM leftover_procs=$real total_in_cgroup=$n"
fi

# A few high-signal memory.stat rows (the full file is ~50 lines of noise).
if [ -r "$CG/memory.stat" ]; then
  grep -E '^(anon|file|slab|sock|shmem|workingset_refault_anon|pgmajfault|pgscan_direct|pgscan_kswapd) ' \
    "$CG/memory.stat" 2>/dev/null | sed 's/^/CGPM memory.stat /'
fi

exit 0
