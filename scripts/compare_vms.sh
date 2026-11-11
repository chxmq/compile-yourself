#!/bin/sh
# Differential check: our vm vs the official one (needs Docker). usage: compare_vms.sh prog.txt [input]
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IN="${2:-/dev/null}"
VM_STATS=1 "$ROOT/vm/sqvm" "$1" < "$IN" > /tmp/ours.out 2> /tmp/ours.err; echo "ours rc=$?"
VM_STATS=1 "$ROOT/scripts/official_vm.sh" "$1" < "$IN" > /tmp/off.out 2> /tmp/off.err; echo "official rc=$?"
cmp /tmp/ours.out /tmp/off.out && diff /tmp/ours.err /tmp/off.err && echo SAME
