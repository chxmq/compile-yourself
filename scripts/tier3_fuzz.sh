#!/bin/bash
# Differential fuzz: tests/fuzz/gen_v1.py programs; C1 (on sqvm) output must equal v1c output.
# usage: scripts/tier3_fuzz.sh [N=200] [first_seed=1] [c1.txt]
cd "$(dirname "$0")/.." || exit 1
make -C vm >/dev/null || exit 1
N=${1:-200}; S=${2:-1}
tmp=$(mktemp -d)
C1=${3:-$tmp/c1.txt}
[ -n "$3" ] || python3 tier2/v1c.py < tier3/tier3.v1 > $C1 || exit 1
bad=0; maxsteps=0
for ((i=S; i<S+N; i++)); do
    size=$(( (i % 15) * 2 + 2 ))
    python3 tests/fuzz/gen_v1.py --seed $i --size $size -o $tmp/p.v1 --input $tmp/p.in || { echo "GEN FAIL $i"; bad=$((bad+1)); continue; }
    python3 tier2/v1c.py < $tmp/p.v1 > $tmp/exp.txt 2>/dev/null || { echo "v1c rejects seed $i"; continue; }
    SQ_LIMIT=10000000000 VM_STATS=1 vm/sqvm $C1 < $tmp/p.v1 > $tmp/got.txt 2>$tmp/err
    rc=$?
    steps=$(sed -n 's/^halted after \([0-9]*\) steps/\1/p' $tmp/err)
    [ -n "$steps" ] && [ "$steps" -gt "$maxsteps" ] && maxsteps=$steps
    if [ $rc -ne 0 ] || ! cmp -s $tmp/exp.txt $tmp/got.txt; then echo "MISMATCH seed=$i size=$size rc=$rc"; cp $tmp/p.v1 /private/tmp/claude-501/tfail_$i.v1; bad=$((bad+1)); fi
done
echo "$N fuzz programs from seed $S, $bad failed; max steps $maxsteps"
rm -rf $tmp
[ $bad -eq 0 ]
