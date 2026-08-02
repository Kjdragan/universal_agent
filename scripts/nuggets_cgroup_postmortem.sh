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
  echo "CGPM leftover_procs=$n"
  while read -r p; do
    [ -n "$p" ] && echo "CGPM leftover pid=$p comm=$(cat "/proc/$p/comm" 2>/dev/null || echo gone)"
  done < "$CG/cgroup.procs"
fi

# A few high-signal memory.stat rows (the full file is ~50 lines of noise).
if [ -r "$CG/memory.stat" ]; then
  grep -E '^(anon|file|slab|sock|shmem|workingset_refault_anon|pgmajfault|pgscan_direct|pgscan_kswapd) ' \
    "$CG/memory.stat" 2>/dev/null | sed 's/^/CGPM memory.stat /'
fi

exit 0
