#!/bin/bash
# Invalid-program corpus through C1 = v1c(tier3.v1) running on the official VM_tier3 (needs Docker).
# Every program v1c rejects must also make the on-machine compiler exit nonzero, with the same exit code as sqvm.
# A valid program is compiled first as a control, so a stopped Docker daemon cannot pass as "rejected".
cd "$(dirname "$0")/.." || exit 1
make -C vm >/dev/null || exit 1
mkdir -p build/off
python3 tier2/v1c.py < tier3/tier3.v1 > build/off/c1.txt || exit 1
ctl=$(ls tests/corpus/*.v1 | head -1)
OFFICIAL_VM=tier3 scripts/official_vm.sh build/off/c1.txt < "$ctl" > build/off/ctl.out 2>/dev/null; rc=$?
python3 tier2/v1c.py < "$ctl" | cmp -s - build/off/ctl.out && [ $rc -eq 0 ] || { echo "control failed (is Docker running?)"; exit 1; }
n=0; acc=0; diffrc=0
for p in tests/corpus/invalid/*.v1 tests/corpus/invalid_strict/*.v1; do
  python3 tier2/v1c.py < "$p" >/dev/null 2>&1 && continue
  n=$((n+1))
  SQ_LIMIT=10000000000 vm/sqvm build/off/c1.txt < "$p" >/dev/null 2>&1; r1=$?
  OFFICIAL_VM=tier3 scripts/official_vm.sh build/off/c1.txt < "$p" >/dev/null 2>&1; r2=$?
  [ $r2 -eq 0 ] && { echo "ACCEPTED by official: $p"; acc=$((acc+1)); }
  [ $r1 -ne $r2 ] && { echo "exit code differs: $p (sqvm $r1, official $r2)"; diffrc=$((diffrc+1)); }
done
echo "$n invalid programs: $acc accepted, $diffrc exit-code differences"
[ $acc -eq 0 ] && [ $diffrc -eq 0 ]
