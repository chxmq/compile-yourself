#!/bin/bash
# Build a small demo trace for tools/viewer.html.
# usage: scripts/make_demo_trace.sh [OUT_DIR=build/demo]
# Writes OUT_DIR/demo.txt (machine program) and OUT_DIR/demo.trace (vm/sqvm --trace), steps capped.
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-build/demo}
CAP=${DEMO_CAP:-3000}          # step cap: SQ_LIMIT makes the VM stop with a timeout after CAP steps
mkdir -p "$OUT"; make -C vm >/dev/null || exit 1
python3 tier2/v1c.py < tools/demo/demo.v1 > "$OUT/demo.txt" || exit 1
full=$(VM_STATS=1 vm/sqvm "$OUT/demo.txt" 2>&1 >/dev/null | sed -n 's/^halted after \([0-9]*\) steps/\1/p')
SQ_LIMIT=$CAP vm/sqvm --trace "$OUT/demo.trace" "$OUT/demo.txt" > "$OUT/demo.out" 2>/dev/null
echo "${full:-0}" > "$OUT/demo.steps"
echo "program output: $(tr -d "\n" < "$OUT/demo.out")   (expected :6 because a[1]+a[3]=10 -> chr(48+10) and sum(3)=6)"
echo "full run: ${full:-?} steps (cap $CAP); trace: $(wc -c < "$OUT/demo.trace" | tr -d ' ') bytes"
echo "open tools/viewer.html in a browser, load $OUT/demo.trace (and optionally $OUT/demo.txt)"
