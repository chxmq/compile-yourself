#!/bin/bash
# Differential test: C1 (v1c(tier3.v1), run on sqvm) vs v1c on every valid corpus program.
# invalid programs must make C1 fail (nonzero exit).  usage: scripts/tier3_diff.sh [c1.txt]
cd "$(dirname "$0")/.." || exit 1
make -C vm >/dev/null || exit 1
tmp=$(mktemp -d)
C1=${1:-$tmp/c1.txt}
[ -n "$1" ] || python3 tier2/v1c.py < tier3/tier3.v1 > $C1 || exit 1
bad=0; n=0; maxsteps=0
for p in tests/corpus/*.v1; do
    n=$((n+1))
    python3 tier2/v1c.py < $p > $tmp/exp.txt 2>/dev/null || { echo "v1c rejects $p"; continue; }
    SQ_LIMIT=10000000000 VM_STATS=1 vm/sqvm $C1 < $p > $tmp/got.txt 2>$tmp/err
    rc=$?
    steps=$(sed -n 's/^halted after \([0-9]*\) steps/\1/p' $tmp/err)
    [ -n "$steps" ] && [ "$steps" -gt "$maxsteps" ] && { maxsteps=$steps; maxp=$p; }
    if [ $rc -ne 0 ] || ! cmp -s $tmp/exp.txt $tmp/got.txt; then echo "MISMATCH $p rc=$rc"; bad=$((bad+1)); fi
done
inv=0
for p in tests/corpus/invalid/*.v1 tests/corpus/invalid_strict/*.v1; do
    if python3 tier2/v1c.py < $p >/dev/null 2>&1; then continue; fi
    SQ_LIMIT=10000000000 vm/sqvm $C1 < $p > $tmp/got.txt 2>/dev/null
    if [ $? -eq 0 ]; then echo "NOT REJECTED $p"; inv=$((inv+1)); fi
done
echo "$n valid checked, $bad mismatched; $inv invalid accepted; max steps $maxsteps ($maxp)"
rm -rf $tmp
[ $bad -eq 0 ] && [ $inv -eq 0 ]
