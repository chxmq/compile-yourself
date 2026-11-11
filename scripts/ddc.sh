#!/bin/bash
# Diverse Double-Compiling (Wheeler, 2005) check for the tier-3 compiler.
#
#   S  = tier3/tier3.v1                 source of the compiler (written in v1)
#   cT = tier2/v1c.py                   trusted, independently written compiler (Python)
#   cA = candidate binary               the compiler-under-test: the machine program that is
#                                       claimed to be "S compiled by itself" (default:
#                                       tier3/tier3.selfcompiled.txt, the shipped artifact)
#
#   stage1 = cT(S)                      a machine program built by the trusted compiler
#   stage2 = stage1(S)   (run on sqvm)  S compiled by (a build of) S itself, but with a
#                                       trusted ancestry that does not involve cA at all
#   MATCH iff stage2 == cA byte for byte.
#
# usage: scripts/ddc.sh [CANDIDATE.txt] [SOURCE.v1]
#        scripts/ddc.sh --make-candidate OUT.txt [SOURCE.v1]   write the self-compiled binary of SOURCE
#                                                                (used to create the shipped artifact)
# exit code: 0 MATCH, 1 MISMATCH, 2 setup/run error
cd "$(dirname "$0")/.." || exit 2
LIM=10000000000
steps_of() { sed -n 's/^halted after \([0-9]*\) steps/\1/p' "$1"; }
make -C vm >/dev/null || exit 2
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

selfcompile() {   # $1 = source; writes $tmp/s1.txt and $tmp/s2.txt, steps in $tmp/steps
    python3 tier2/v1c.py < "$1" > "$tmp/s1.txt" || { echo "ddc: trusted compiler v1c rejects $1" >&2; return 2; }
    SQ_LIMIT=$LIM VM_STATS=1 vm/sqvm "$tmp/s1.txt" < "$1" > "$tmp/s2.txt" 2> "$tmp/err" \
        || { echo "ddc: stage1 failed on $1: $(head -c 200 "$tmp/err")" >&2; return 2; }
    steps_of "$tmp/err" > "$tmp/steps"
}

if [ "$1" = "--make-candidate" ]; then
    out=$2; src=${3:-tier3/tier3.v1}
    [ -n "$out" ] || { echo "usage: ddc.sh --make-candidate OUT [SOURCE]"; exit 2; }
    # The candidate is built by the "normal" route: C1 = v1c(S), C2 = C1(S), C3 = C2(S), ship C3.
    selfcompile "$src" || exit 2
    SQ_LIMIT=$LIM vm/sqvm "$tmp/s2.txt" < "$src" > "$out" || exit 2
    echo "wrote $out ($(wc -w < "$out" | tr -d " ") words)"; exit 0
fi

CAND=${1:-tier3/tier3.selfcompiled.txt}
SRC=${2:-tier3/tier3.v1}
[ -f "$CAND" ] || { echo "ddc: candidate '$CAND' not found (create with: scripts/ddc.sh --make-candidate $CAND)"; exit 2; }
[ -f "$SRC" ] || { echo "ddc: source '$SRC' not found"; exit 2; }

echo "source S          : $SRC ($(wc -c < "$SRC") bytes)"
echo "trusted compiler  : tier2/v1c.py (Python, independent of S and of the candidate)"
echo "candidate cA      : $CAND ($(wc -w < "$CAND" | tr -d " ") words)"
echo "== stage1 = v1c(S)"
python3 tier2/v1c.py < "$SRC" > "$tmp/stage1.txt" || { echo "ddc: v1c rejects S"; exit 2; }
echo "stage1: $(wc -w < "$tmp/stage1.txt" | tr -d " ") words"
echo "== stage2 = stage1(S), run on vm/sqvm"
SQ_LIMIT=$LIM VM_STATS=1 vm/sqvm "$tmp/stage1.txt" < "$SRC" > "$tmp/stage2.txt" 2> "$tmp/err"
rc=$?
[ $rc -eq 0 ] || { echo "ddc: stage1 run failed rc=$rc: $(head -c 200 "$tmp/err")"; exit 2; }
echo "stage2: $(wc -w < "$tmp/stage2.txt" | tr -d " ") words, $(steps_of "$tmp/err") VM steps"
echo "== compare stage2 with candidate"
if cmp -s "$tmp/stage2.txt" "$CAND"; then
    cat <<MSG
DDC: MATCH  (stage2 == candidate, $(wc -c < "$CAND") bytes identical)

What a match proves: the candidate binary is exactly what you get by compiling S with a compiler
whose ancestry is v1c (a separate implementation, in a different language) instead of the candidate.
If the candidate carried a self-propagating "trusting trust" trojan that is not in the source S,
the candidate would differ from stage2 (stage2 never ran any code from the candidate), so a match
means the candidate contains no such source-invisible behaviour that changes the compiler's output on S.
Assumptions: v1c is not itself compromised in a way that colludes with the candidate; sqvm is correct;
the compiler is deterministic. It proves nothing about programs other than S (the check is exactly about
the compiler binary being the honest compilation of its source), and a mismatch is not by itself proof
of malice: it just means the candidate and S have to be reconciled.
MSG
    exit 0
else
    first=$(cmp "$tmp/stage2.txt" "$CAND" 2>&1 | head -1)
    cat <<MSG
DDC: MISMATCH  ($first)
  stage2: $(wc -w < "$tmp/stage2.txt" | tr -d " ") words; candidate: $(wc -w < "$CAND" | tr -d " ") words
The candidate is NOT the honest self-compilation of $SRC by the trusted chain. Either the candidate was built
from a different source, or from a different compiler version, or it is not what it claims to be.
MSG
    exit 1
fi
