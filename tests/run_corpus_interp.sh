#!/bin/bash
# Checks every tests/corpus/*.v1 against its .out using ref/v1interp.py.
# usage: tests/run_corpus_interp.sh [--regen]   (--regen rewrites .out files; review diffs!)
# invalid/*.v1 must be rejected with exit code 2.
cd "$(dirname "$0")/.." || exit 1
INTERP="python3 ref/v1interp.py"
fail=0; n=0
for p in tests/corpus/*.v1; do
    b=${p%.v1}
    in=/dev/null; [ -f "$b.in" ] && in="$b.in"
    n=$((n+1))
    if [ "$1" = "--regen" ]; then
        $INTERP "$p" < "$in" > "$b.out" || { echo "FAIL (run) $p"; fail=$((fail+1)); }
        continue
    fi
    if ! $INTERP "$p" < "$in" 2>/tmp/v1err.$$ | cmp -s - "$b.out"; then
        echo "FAIL $p"; fail=$((fail+1))
    fi
done
for p in tests/corpus/invalid/*.v1 tests/corpus/invalid_strict/*.v1; do
    n=$((n+1))
    $INTERP --check "$p" </dev/null >/dev/null 2>&1
    rc=$?
    if [ $rc -ne 2 ]; then echo "FAIL (not rejected, rc=$rc) $p"; fail=$((fail+1)); fi
done
rm -f /tmp/v1err.$$
echo "$n checked, $fail failed"
[ $fail -eq 0 ]
