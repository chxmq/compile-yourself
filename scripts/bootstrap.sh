#!/bin/bash
# Tier-3 bootstrap check.
#   C1 = v1c(tier3.v1)
#   verify C1 as a compiler on the corpus (compile on sqvm, run, compare .out) and on fuzz programs
#   C2 = C1(tier3.v1), C3 = C2(tier3.v1); require C2 == C3; report C1 == C2 == v1c(tier3.v1) (DDC readiness)
# usage: scripts/bootstrap.sh [FUZZ_N=60] [first_seed=1]
cd "$(dirname "$0")/.." || exit 1
FN=${1:-60}; FS=${2:-1}
make -C vm >/dev/null || exit 1
tmp=$(mktemp -d)
LIM=10000000000
fail=0
steps_of() { sed -n 's/^halted after \([0-9]*\) steps/\1/p' "$1"; }

echo "== step 1: C1 = v1c(tier3.v1)"
python3 tier2/v1c.py < tier3/tier3.v1 > $tmp/C1.txt || { echo "FAIL: v1c rejects tier3.v1"; exit 1; }
echo "tier3.v1: $(wc -c < tier3/tier3.v1) bytes; C1: $(wc -l < $tmp/C1.txt) words"

echo "== step 2: C1 on corpus (compile on sqvm, run on sqvm, compare with .out)"
n=0; bad=0; maxc=0; maxr=0
for p in tests/corpus/*.v1; do
    b=${p%.v1}; n=$((n+1)); in=/dev/null; [ -f "$b.in" ] && in="$b.in"
    SQ_LIMIT=$LIM VM_STATS=1 vm/sqvm $tmp/C1.txt < $p > $tmp/p.txt 2>$tmp/e1; rc=$?
    cs=$(steps_of $tmp/e1)
    if [ $rc -ne 0 ]; then echo "FAIL compile $p rc=$rc"; bad=$((bad+1)); continue; fi
    [ "$cs" -gt "$maxc" ] && maxc=$cs
    VM_STATS=1 vm/sqvm $tmp/p.txt < $in > $tmp/o.out 2>$tmp/e2; rc=$?
    rs=$(steps_of $tmp/e2); [ -n "$rs" ] && [ "$rs" -gt "$maxr" ] && maxr=$rs
    if [ $rc -ne 0 ] || ! cmp -s $tmp/o.out $b.out; then echo "FAIL run $p rc=$rc"; bad=$((bad+1)); fi
done
inv=0
for p in tests/corpus/invalid/*.v1 tests/corpus/invalid_strict/*.v1; do
    if python3 tier2/v1c.py < $p >/dev/null 2>&1; then continue; fi
    vm/sqvm $tmp/C1.txt < $p > /dev/null 2>&1 && { echo "FAIL not rejected $p"; inv=$((inv+1)); }
done
echo "corpus: $n programs, $bad failed, $inv invalid accepted; max compile steps $maxc; max run steps $maxr"
[ $bad -eq 0 ] && [ $inv -eq 0 ] || fail=1

echo "== step 3: C1 on $FN fuzz programs (seeds $FS..) vs interpreter"
fb=0; maxf=0
for ((i=FS; i<FS+FN; i++)); do
    size=$(( (i % 15) * 2 + 2 ))
    python3 tests/fuzz/gen_v1.py --seed $i --size $size -o $tmp/f.v1 --input $tmp/f.in || { fb=$((fb+1)); continue; }
    python3 ref/v1interp.py --count-main $tmp/f.v1 < $tmp/f.in > $tmp/f.exp 2>/dev/null || { fb=$((fb+1)); continue; }
    SQ_LIMIT=$LIM VM_STATS=1 vm/sqvm $tmp/C1.txt < $tmp/f.v1 > $tmp/f.txt 2>$tmp/e1 || { echo "FAIL fuzz compile seed $i"; fb=$((fb+1)); continue; }
    cs=$(steps_of $tmp/e1); [ "$cs" -gt "$maxf" ] && maxf=$cs
    vm/sqvm $tmp/f.txt < $tmp/f.in > $tmp/f.got 2>/dev/null
    cmp -s $tmp/f.exp $tmp/f.got || { echo "FAIL fuzz run seed $i"; fb=$((fb+1)); }
done
echo "fuzz: $FN programs, $fb failed; max compile steps $maxf"
[ $fb -eq 0 ] || fail=1

echo "== step 4: C2 = C1(tier3.v1), C3 = C2(tier3.v1)"
SQ_LIMIT=$LIM VM_STATS=1 vm/sqvm $tmp/C1.txt < tier3/tier3.v1 > $tmp/C2.txt 2>$tmp/e2 || { echo "FAIL: C1 on tier3.v1"; fail=1; }
echo "C1(tier3.v1) -> C2: $(steps_of $tmp/e2) steps, $(wc -l < $tmp/C2.txt) words"
SQ_LIMIT=$LIM VM_STATS=1 vm/sqvm $tmp/C2.txt < tier3/tier3.v1 > $tmp/C3.txt 2>$tmp/e3 || { echo "FAIL: C2 on tier3.v1"; fail=1; }
echo "C2(tier3.v1) -> C3: $(steps_of $tmp/e3) steps, $(wc -l < $tmp/C3.txt) words"
if cmp -s $tmp/C2.txt $tmp/C3.txt; then echo "C2 == C3: yes"; else echo "C2 == C3: NO"; fail=1; fi
if cmp -s $tmp/C1.txt $tmp/C2.txt; then echo "C1 file == C2: yes"; else echo "C1 file == C2: NO"; fail=1; fi
python3 tier2/v1c.py < tier3/tier3.v1 > $tmp/V.txt
if cmp -s $tmp/V.txt $tmp/C2.txt; then echo "v1c(tier3.v1) == C2: yes (DDC-ready)"; else echo "v1c(tier3.v1) == C2: NO"; fail=1; fi
if [ $fail -eq 0 ]; then echo "BOOTSTRAP PASSED"; else echo "BOOTSTRAP FAILED"; fi
rm -rf $tmp
exit $fail
