#!/bin/sh
# Run the official x86-64 Linux VM binary under Docker (amd64).
#   scripts/official_vm.sh program.txt < input.txt        (same CLI as ./vm)
#   OFFICIAL_VM=tier3 scripts/official_vm.sh ...          (use VM_tier3 instead of VM_tier1and2)
# Env VM_STATS is forwarded. Requires a running Docker daemon.
# The program file must live under the repo (it is bind-mounted read-only).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
case "${OFFICIAL_VM:-tier1and2}" in
  tier3) BIN=VM_tier3 ;;
  *) BIN=VM_tier1and2 ;;
esac
PROG="$1"; [ -n "$PROG" ] || { echo "usage: $0 program.txt < input.txt" >&2; exit 1; }
PDIR="$(cd "$(dirname "$PROG")" && pwd)"
exec docker run --rm -i --platform linux/amd64 \
  -e VM_STATS \
  -v "$ROOT/.ref/$BIN:/vm:ro" \
  -v "$PDIR:/prog:ro" \
  debian:stable-slim /vm "/prog/$(basename "$PROG")"
