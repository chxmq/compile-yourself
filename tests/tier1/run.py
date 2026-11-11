#!/usr/bin/env python3
"""Tier-1 tests: echo + reverse (bigmul/hanoi tests get added by their authors).
   python3 tests/tier1/run.py [vm-binary]       (pass scripts/official_vm.sh to use the official VM)"""
import os, random, subprocess, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
vm = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "vm", "sqvm")
random.seed(42)

def run(prog, inp):
    env = dict(os.environ, VM_STATS="1")
    r = subprocess.run([vm, os.path.join(ROOT, "tier1", prog + ".txt")], input=inp, capture_output=True, env=env)
    steps = int(r.stderr.split(b"halted after ")[1].split()[0]) if b"halted after" in r.stderr else None
    return r.stdout, r.returncode, steps

nonul = lambda n: bytes(random.randint(1, 255) for _ in range(n))
echo_cases = [b"", b"a", b"\n", b"hello world\n", b"\x00", b"\xff", b"\x00\x00\x00", bytes(range(256)), bytes(range(255, -1, -1)),
              b"\xff\x00\xff\x00", bytes(random.randint(0, 255) for _ in range(5000)), b"\x00" * 1000, b"\xff" * 1000,
              bytes(random.randint(0, 255) for _ in range(1_000_000))]
rev_cases = [b"", b"a", b"\n", b"ab", b"hello\n", b"\xff", bytes(range(1, 256)), nonul(999), nonul(1000), b"\xff" * 1000, b"\n" * 1000,
             b"a" * 1000, b"x\x00y", nonul(1000)]
bad = 0
worst = {}
for prog, cases, f in (("echo", echo_cases, lambda d: d), ("reverse", rev_cases, lambda d: d[::-1])):
    for d in cases:
        out, rc, steps = run(prog, d)
        ok = out == f(d) and rc == 0 and steps is not None and steps < 10**8
        worst[prog] = max(worst.get(prog, 0), steps or 0)
        if not ok:
            bad += 1
            print("FAIL", prog, "len", len(d), "rc", rc, "steps", steps)
print("tier1 echo/reverse: %s  (max steps: %s)" % ("FAILED %d" % bad if bad else "all passed", worst))
sys.exit(1 if bad else 0)
