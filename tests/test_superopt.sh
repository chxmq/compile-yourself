#!/bin/bash
# Re-verify every sequence documented in docs/SUPEROPT.md (the compiler's and the superoptimizer's):
# python simulator on edge values + random samples, and the real vm/sqvm.  Also checks the C search
# core still finds MOV = 4 instructions and JEQ0 = 3.  Quick (< 60 s).
cd "$(dirname "$0")/.." || exit 1
make -C vm >/dev/null || exit 1
python3 toolkit/superopt.py verify --quick || exit 1
mkdir -p build && cc -O2 -pthread -o build/superopt_c toolkit/superopt_c.c || exit 1
out=$(build/superopt_c 1 0 7 4 0 4) || exit 1
echo "$out" | grep -q "^L 4 count [1-9][0-9]* MM steps 4 " || { echo "superopt: MOV search regression"; exit 1; }
echo "$out" | grep -q "^L 3 count 0" || { echo "superopt: MOV found in <4"; exit 1; }
out=$(build/superopt_c 15 0 5 3 1 4) || exit 1
echo "$out" | grep -q "^L 3 count [1-9]" || { echo "superopt: JEQ0 3-instruction search regression"; exit 1; }
echo "superopt: OK"
