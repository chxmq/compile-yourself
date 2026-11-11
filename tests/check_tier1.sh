#!/bin/bash
# Tier-1 deliverables: the four machine programs must exist and pass their tests.
cd "$(dirname "$0")/.." || exit 1
make -C vm >/dev/null || exit 1
fail=0
for f in echo reverse bigmul hanoi; do
    if [ -s "tier1/$f.txt" ]; then echo "present: tier1/$f.txt ($(wc -w < tier1/$f.txt | tr -d ' ') words)"
    else echo "MISSING: tier1/$f.txt"; fail=1; fi
done
python3 tests/tier1/run.py        || fail=1     # echo, reverse
python3 tests/tier1/run_v1progs.py || fail=1    # bigmul, hanoi
[ $fail -eq 0 ] && echo "tier1: OK" || echo "tier1: FAILED"
exit $fail
