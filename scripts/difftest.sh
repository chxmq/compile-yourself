#!/bin/bash
# Differential fuzz: tests/fuzz/gen_v1.py programs -> v1c -> sqvm  vs  ref/v1interp.py.
# usage: scripts/difftest.sh [N=200] [first_seed=1] [compiler=tier2/v1c.py]
cd "$(dirname "$0")/.." || exit 1
N=${1:-200}; S=${2:-1}; COMP=${3:-tier2/v1c.py}
make -C vm >/dev/null || exit 1
tmp=$(mktemp -d)
bad=0; maxsteps=0; maxct=0
for ((i=S; i<S+N; i++)); do
    size=$(( (i % 15) * 2 + 2 ))
    python3 tests/fuzz/gen_v1.py --seed $i --size $size -o $tmp/p.v1 --input $tmp/p.in || { echo "GEN FAIL seed=$i"; bad=$((bad+1)); continue; }
    python3 ref/v1interp.py --count-main $tmp/p.v1 < $tmp/p.in > $tmp/exp.out 2>/dev/null || { echo "ORACLE FAIL seed=$i"; bad=$((bad+1)); continue; }
    t0=$(date +%s)
    python3 $COMP < $tmp/p.v1 > $tmp/p.txt 2>$tmp/c.err || { echo "COMPILE FAIL seed=$i size=$size: $(head -c 200 $tmp/c.err)"; bad=$((bad+1)); continue; }
    ct=$(( $(date +%s) - t0 )); [ $ct -gt $maxct ] && maxct=$ct
    VM_STATS=1 vm/sqvm $tmp/p.txt < $tmp/p.in > $tmp/got.out 2>$tmp/run.err
    rc=$?
    steps=$(sed -n 's/^halted after \([0-9]*\) steps/\1/p' $tmp/run.err)
    [ -n "$steps" ] && [ "$steps" -gt "$maxsteps" ] && maxsteps=$steps
    if [ $rc -ne 0 ] || ! cmp -s $tmp/exp.out $tmp/got.out; then
        echo "MISMATCH seed=$i size=$size rc=$rc: $(head -c 200 $tmp/run.err)"; bad=$((bad+1)); cp $tmp/p.v1 /tmp/llh_fail_$i.v1 2>/dev/null
    fi
done
echo "$N programs from seed $S, $bad failed; max steps $maxsteps; max compile time ${maxct}s"
rm -rf $tmp
[ $bad -eq 0 ]
