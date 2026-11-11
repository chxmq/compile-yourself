#!/bin/bash
# usage: tests/fuzz/check_gen.sh [N=300] [first_seed=1]
# Generates N programs (sizes cycle 2..30) and runs each through ref/v1interp.py; every run must exit 0.
cd "$(dirname "$0")/../.." || exit 1
N=${1:-300}; S=${2:-1}
tmp=$(mktemp -d)
bad=0; totbytes=0; totout=0
for ((i=S; i<S+N; i++)); do
    size=$(( (i % 15) * 2 + 2 ))
    python3 tests/fuzz/gen_v1.py --seed $i --size $size -o $tmp/p.v1 || { echo "GEN FAIL seed=$i"; bad=$((bad+1)); continue; }
    totbytes=$((totbytes + $(wc -c < $tmp/p.v1)))
    if ! timeout 60 python3 ref/v1interp.py --count-main --max-steps 50000000 $tmp/p.v1 < $tmp/p.in > $tmp/p.out 2>$tmp/p.err; then
        echo "FAIL seed=$i size=$size: $(head -c 200 $tmp/p.err)"; bad=$((bad+1))
    fi
    # determinism: uninitialised locals filled with garbage must not change the output
    timeout 60 python3 ref/v1interp.py --garbage $i --max-steps 50000000 $tmp/p.v1 < $tmp/p.in 2>/dev/null | cmp -s - $tmp/p.out || { echo "NONDETERMINISTIC (garbage locals) seed=$i size=$size"; bad=$((bad+1)); }
    totout=$((totout + $(wc -c < $tmp/p.out)))
done
echo "$N programs, $bad failed, avg source $((totbytes / N)) bytes, avg output $((totout / N)) bytes"
rm -rf $tmp
[ $bad -eq 0 ]
