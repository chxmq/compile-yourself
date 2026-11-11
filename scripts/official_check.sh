#!/bin/bash
# Full check on the ORGANIZERS' binaries (needs Docker, amd64). Output compared with expected files.
set -u
cd "$(dirname "$0")/.."; mkdir -p build/off; fail=0; n=0
for v in tests/corpus/*.v1; do
  b=$(basename $v .v1); in=tests/corpus/$b.in; [ -f $in ] || in=/dev/null
  python3 tier2/v1c.py < $v > build/off/$b.txt || { echo "compile fail $b"; fail=1; continue; }
  VM_STATS=1 scripts/official_vm.sh build/off/$b.txt < $in > build/off/$b.out 2>build/off/$b.err
  cmp -s build/off/$b.out tests/corpus/$b.out && n=$((n+1)) || { echo "MISMATCH $b"; fail=1; }
done
echo "tier2 corpus on official VM_tier1and2: $n ok"
for t in echo reverse bigmul hanoi; do
  case $t in echo|reverse) s="hello world";; bigmul) s=$'123456789012345678901234567890\n987654321098765432109876543210';; hanoi) s=$'3';; esac
  printf '%s\n' "$s" | scripts/official_vm.sh tier1/$t.txt | head -c 120 | tr '\n' '|'; echo "  <= $t"
done
# bootstrap with the tier-3 official VM
python3 tier2/v1c.py < tier3/tier3.v1 > build/off/C1.txt
OFFICIAL_VM=tier3 VM_STATS=1 scripts/official_vm.sh build/off/C1.txt < tier3/tier3.v1 > build/off/C2.txt 2>build/off/c2.err; tail -1 build/off/c2.err
OFFICIAL_VM=tier3 VM_STATS=1 scripts/official_vm.sh build/off/C2.txt < tier3/tier3.v1 > build/off/C3.txt 2>build/off/c3.err; tail -1 build/off/c3.err
cmp build/off/C2.txt build/off/C3.txt && cmp build/off/C1.txt build/off/C2.txt && echo "OFFICIAL BOOTSTRAP: C1==C2==C3" || fail=1
exit $fail
