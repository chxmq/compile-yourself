#!/usr/bin/env python3
"""Tier-1 BIGMUL and HANOI tests (Python as oracle).  usage: run_v1progs.py [vm]"""
import os, random, subprocess, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
vm = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "vm", "sqvm")
random.seed(7)

def run(prog, inp):
    r = subprocess.run([vm, os.path.join(ROOT, "tier1", prog + ".txt")], input=inp, capture_output=True, env=dict(os.environ, VM_STATS="1"))
    s = int(r.stderr.split(b"halted after ")[1].split()[0]) if b"halted after" in r.stderr else None
    return r.stdout, r.returncode, s

def hanoi(n, a, c, b, out):
    if n == 0: return
    hanoi(n - 1, a, b, c, out); out.append(a + c); hanoi(n - 1, b, c, a, out)

def rnd(d): return str(random.randint(10 ** (d - 1), 10 ** d - 1)) if d > 1 else str(random.randint(0, 9))
nums = ["0", "1", "9", "10", "99", "12345678901234567890", "9" * 40, "1" + "0" * 39, "5" * 40, "4" * 39, "1" + "0" * 38 + "1", "2147483648", "4294967296"]
nums += [rnd(random.randint(1, 40)) for _ in range(30)]
pairs = [(a, b) for a in nums[:13] for b in nums[:13]] + [(random.choice(nums), random.choice(nums)) for _ in range(150)]
bad = 0; worst = 0
for a, b in pairs:
    out, rc, st = run("bigmul", ("%s\n%s\n" % (a, b)).encode())
    exp = ("%d\n" % (int(a) * int(b))).encode()
    worst = max(worst, st or 0)
    if out != exp or rc != 0:
        bad += 1; print("FAIL bigmul", a, b, out[:100], st)
whanoi = 0
for n in range(1, 9):
    out, rc, st = run("hanoi", ("%d\n" % n).encode())
    moves = []; hanoi(n, "A", "C", "B", moves)
    exp = ("\n".join(moves) + "\n").encode()
    whanoi = max(whanoi, st or 0)
    if out != exp or rc != 0:
        bad += 1; print("FAIL hanoi", n, st)
print("bigmul/hanoi:", "FAILED %d" % bad if bad else "all passed", "(%d bigmul cases; max steps bigmul %d, hanoi %d)" % (len(pairs), worst, whanoi))
sys.exit(1 if bad else 0)
