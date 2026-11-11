#!/bin/bash
# Shows that scripts/ddc.sh is sensitive: a candidate compiler that is valid but differs from the
# honest self-compilation of tier3.v1 in a harmless way is reported as MISMATCH.
# The variant: identical compiler except that it separates output numbers with a space instead of a
# newline (the VM loader accepts any whitespace). It is a benign formatting change, and it is
# verified below to still be a working compiler.
cd "$(dirname "$0")/.." || exit 1
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
fail=0

# variant source: the single putc(10) in outraw becomes putc(32)
n=$(grep -c '^    putc(10);$' tier3/tier3.v1)
[ "$n" = 1 ] || { echo "FAIL: expected exactly one 'putc(10);' line in tier3.v1, found $n"; exit 1; }
sed 's/^    putc(10);$/    putc(32);/' tier3/tier3.v1 > "$tmp/variant.v1"
cmp -s tier3/tier3.v1 "$tmp/variant.v1" && { echo "FAIL: variant identical"; exit 1; }

scripts/ddc.sh --make-candidate "$tmp/variant.txt" "$tmp/variant.v1" || { echo "FAIL: could not build variant"; exit 1; }

# 1. the variant is a valid, working compiler: it compiles a corpus program that then runs correctly
vm/sqvm "$tmp/variant.txt" < tests/corpus/gcd.v1 > "$tmp/gcd.txt" || { echo "FAIL: variant compiler failed"; exit 1; }
grep -q ' ' "$tmp/gcd.txt" || { echo "FAIL: variant does not use spaces"; fail=1; }
in=/dev/null; [ -f tests/corpus/gcd.in ] && in=tests/corpus/gcd.in
vm/sqvm "$tmp/gcd.txt" < $in | cmp -s - tests/corpus/gcd.out && echo "ok: variant compiler works (gcd.v1 output correct)" || { echo "FAIL: variant output wrong"; fail=1; }

# 2. ddc.sh against the original source must say MISMATCH (exit 1)
scripts/ddc.sh "$tmp/variant.txt" > "$tmp/ddc.out" 2>&1; rc=$?
if [ $rc -eq 1 ] && grep -q 'DDC: MISMATCH' "$tmp/ddc.out"; then echo "ok: ddc.sh reports MISMATCH for the variant"; else echo "FAIL: ddc.sh rc=$rc"; cat "$tmp/ddc.out"; fail=1; fi

# 3. control: the variant checked against its OWN source is a MATCH, and the shipped artifact matches the real source
scripts/ddc.sh "$tmp/variant.txt" "$tmp/variant.v1" > "$tmp/ddc2.out" 2>&1; rc=$?
if [ $rc -eq 0 ]; then echo "ok: control (variant vs its own source) is MATCH"; else echo "FAIL: control rc=$rc"; fail=1; fi
scripts/ddc.sh > /dev/null 2>&1 && echo "ok: shipped artifact is MATCH" || { echo "FAIL: shipped artifact mismatch"; fail=1; }

[ $fail -eq 0 ] && echo "PASS" || echo "FAILED"
exit $fail
