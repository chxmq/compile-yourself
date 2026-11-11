#!/bin/bash
# Compile every tests/corpus/*.v1 with tier2/v1c.py, run on vm/sqvm, compare with .out.
# invalid/ and invalid_strict/ programs must be rejected (nonzero exit).  Also reports max steps.
# usage: tests/run_corpus_compiled.sh [-v]
cd "$(dirname "$0")/.." || exit 1
make -C vm >/dev/null || exit 1
tmp=$(mktemp -d)
fail=0; n=0; maxsteps=0
for p in tests/corpus/*.v1; do
    b=${p%.v1}; n=$((n+1))
    in=/dev/null; [ -f "$b.in" ] && in="$b.in"
    if ! python3 tier2/v1c.py < "$p" > $tmp/c.txt 2>$tmp/c.err; then
        echo "FAIL (compile) $p: $(head -c 150 $tmp/c.err)"; fail=$((fail+1)); continue
    fi
    VM_STATS=1 vm/sqvm $tmp/c.txt < "$in" > $tmp/o.out 2>$tmp/o.err
    rc=$?
    steps=$(sed -n 's/^halted after \([0-9]*\) steps/\1/p' $tmp/o.err)
    [ -n "$steps" ] && [ "$steps" -gt "$maxsteps" ] && { maxsteps=$steps; maxp=$p; }
    if [ $rc -ne 0 ] || ! cmp -s $tmp/o.out "$b.out"; then
        echo "FAIL (run rc=$rc) $p: $(head -c 150 $tmp/o.err)"; fail=$((fail+1))
    elif [ "$1" = "-v" ]; then echo "ok $p steps=$steps"; fi
done
for p in tests/corpus/invalid/*.v1 tests/corpus/invalid_strict/*.v1; do
    n=$((n+1))
    if python3 tier2/v1c.py < "$p" >/dev/null 2>&1; then echo "FAIL (not rejected) $p"; fail=$((fail+1)); fi
done
rm -rf $tmp
echo "$n checked, $fail failed; max steps $maxsteps ($maxp)"
[ $fail -eq 0 ]
