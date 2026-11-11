#!/usr/bin/env python3
"""Per-source-line step profile.   usage: tools/v1prof.py prog.v1 [input]
Compiles with tier2/v1c.py (V1C_MAP), runs vm/sqvm --profile, prints steps per source line (top 25)
and the share of steps outside any statement (runtime routines, call glue after the last statement start)."""
import os, subprocess, sys, tempfile, collections
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = sys.argv[1]
inp = open(sys.argv[2], "rb").read() if len(sys.argv) > 2 else b""
d = tempfile.mkdtemp()
env = dict(os.environ, V1C_MAP=d + "/map")
subprocess.run([sys.executable, root + "/tier2/v1c.py"], stdin=open(src), stdout=open(d + "/p.txt", "w"), env=env, check=True)
subprocess.run([root + "/vm/sqvm", "--profile", d + "/prof", d + "/p.txt"], input=inp, stdout=subprocess.DEVNULL, check=False)
smap = [tuple(map(int, l.split())) for l in open(d + "/map")]
hits = [tuple(map(int, l.split())) for l in open(d + "/prof")]
smap.sort()
import bisect
starts = [a * 1 for a, _ in smap]
per = collections.Counter()
total = 0
for pc, n in hits:
    total += n
    i = bisect.bisect_right(starts, pc - 6) - 1   # map pcs are code offsets? (pc() includes the 6 prefix)
    i = bisect.bisect_right(starts, pc) - 1
    per[smap[i][1] if i >= 0 else 0] += n
lines = open(src).read().split("\n")
print("total steps", total)
for ln, n in per.most_common(25):
    print("%10d %5.1f%%  line %-4d %s" % (n, 100.0 * n / total, ln, lines[ln - 1].strip()[:70] if ln else "(runtime/boot)"))
