#!/usr/bin/env python3
"""Hand-checked edge-case tests for vm/sqvm.c.

Each case: (name, program text, stdin bytes, env, expected stdout bytes,
expected exit code, expected stderr substring).
Every expectation was derived by hand from the rules in .ref/PROBLEM.txt
(and the official binary's disassembly for messages/exit codes).
Run:  python3 tests/test_vm.py [path/to/vm]    (default vm/sqvm; scripts/official_vm.sh
works too, if Docker is up, since it has the same CLI).
"""
import os, subprocess, sys, tempfile

N = 1048576
IN, OUT = N - 2, N - 1
vm = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "vm", "sqvm")

def P(*w):
    return " ".join(str(x) for x in w)

cases = [
    ("spec sample prints A", P(OUT, 6, 3, 7, 8, -1, -65, -1, 0), b"", {}, b"A", 0, "halted after 2 steps"),
    ("IN at EOF as b gives -1 (so 0-(-1)=1, then OUT prints -1 -> 255)",
     P(9, IN, 3, OUT, 9, 6, 12, 13, -1, 0, 0, 0, -1, 0), b"", {}, b"\xff", 0, ""),
    ("IN as b returns byte (A -> mem9=-66 -> prints 66)",
     P(9, IN, 3, OUT, 9, 6, 12, 13, -1, 0, 0, 0, -1, 0), b"B", {}, b"B", 0, ""),
    ("OUT prints low 8 bits: 321 -> 65", P(OUT, 6, 3, 7, 8, -1, -321, -1, 0), b"", {}, b"A", 0, ""),
    ("OUT of 256 -> NUL byte", P(OUT, 6, 3, 7, 8, -1, -256, -1, 0), b"", {}, b"\x00", 0, ""),
    ("OUT of negative: r=-1 prints 255, branch taken to -1", P(OUT, 6, -1, 0, 0, 0, 1), b"", {}, b"\xff", 0, "halted after 1 steps"),
    ("OUT,OUT prints NUL, r=0 so falls through", P(OUT, OUT, -1, 5, 5, -1, 3, 3, 3), b"", {}, b"\x00", 0, ""),
    ("OUT <- IN: byte 65 prints (-65)&255=191, r<0 jumps -1", P(OUT, IN, -1), b"A", {}, b"\xbf", 0, "halted after 1 steps"),
    ("wraparound INT_MIN-1 = INT_MAX (non-negative, falls through and prints X)",
     P(9, 10, -1, OUT, 11, 6, 12, 13, -1, -2147483648, 1, -88, -1, 0), b"", {}, b"X", 0, ""),
    ("wraparound INT_MAX-(-1) = INT_MIN (negative, jumps over print)",
     P(9, 10, 6, OUT, 11, 6, 12, 13, -1, 2147483647, -1, -88, -1, 0), b"", {}, b"", 0, ""),
    ("self-modifying: patch b field of next instr (13 -> 14): prints B",
     P(4, 12, 3, OUT, 13, 6, 15, 16, -1, 0, 0, 0, -1, -65, -66, -1, 0), b"", {}, b"B", 0, ""),
    ("instr writing own c field still uses the old c (fetched first)",
     P(2, 9, 6, 0, 0, 0, OUT, 10, -1, 100, 175), b"", {}, b"Q", 0, ""),
    ("error: a == N", P(N, 0, 0), b"", {}, b"", 1, "error at 0: operand outside memory (1048576 0 0)"),
    ("error: b == -1", P(0, -1, 0), b"", {}, b"", 1, "error at 0: operand outside memory (0 -1 0)"),
    ("error: a == -1", P(-1, 0, 0), b"", {}, b"", 1, "error at 0: operand outside memory (-1 0 0)"),
    ("error: IN as a", P(IN, 0, 0), b"x", {}, b"", 1, "error at 0: IN used as first operand (1048574 0 0)"),
    ("error reports pc of second instr", P(5, 5, 3, IN, 0, 0), b"", {}, b"", 1, "error at 3:"),
    ("error: IN as a and out-of-range b -> range error first", P(IN, N, 0), b"", {}, b"", 1, "operand outside memory"),
    ("halt by jump to N-2 (outside 0..N-3): 1 step", P(3, 4, IN, -1, 0), b"", {}, b"", 0, "halted after 1 steps"),
    ("jump to N-3 is valid: runs 0 0 0 at N-3 (reads mem[N-3..N-1]) then halts: 2 steps",
     P(3, 4, N - 3, -1, 0), b"", {}, b"", 0, "halted after 2 steps"),
    ("running off the end: all-zero instrs, pc=0,3,..,N-4 -> 349525 steps", P(0, 0, 0), b"", {}, b"", 0, "halted after 349525 steps"),
    ("empty program runs off the end too", "", b"", {}, b"", 0, "halted after 349525 steps"),
    ("timeout: infinite loop, SQ_LIMIT=1000", P(3, 4, 0, -1, 0), b"", {"SQ_LIMIT": "1000"}, b"", 2, "timeout after 1000 steps"),
    ("limit exactly equal to steps used is fine", P(OUT, 6, 3, 7, 8, -1, -65, -1, 0), b"", {"SQ_LIMIT": "2"}, b"A", 0, "halted after 2 steps"),
    ("limit one below steps used times out", P(OUT, 6, 3, 7, 8, -1, -65, -1, 0), b"", {"SQ_LIMIT": "1"}, b"A", 2, "timeout after 1 steps"),
    ("loader: bad token", "1 2 x", b"", {}, b"", 1, "bad token in program file after 2 words"),
    ("loader: word > int32", "1 2 2147483648", b"", {}, b"", 1, "word 2 in program file doesn't fit in 32 bits"),
    ("loader: INT_MIN accepted, newlines/tabs fine", "1048575\t6\n3\r\n7 8 -1 -65 -1 0\n", b"", {}, b"A", 0, ""),
    ("no VM_STATS line when unset", P(OUT, 6, 3, 7, 8, -1, -65, -1, 0), b"", {"VM_STATS": None}, b"A", 0, None),
]

def run(prog, stdin, env):
    e = dict(os.environ)
    for k, v in env.items():
        if v is None: e.pop(k, None)
        else: e[k] = v
    if "VM_STATS" not in env: e["VM_STATS"] = "1"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(prog)
    try:
        r = subprocess.run([vm, f.name], input=stdin, capture_output=True, env=e)
    finally:
        os.unlink(f.name)
    return r

bad = 0
for name, prog, stdin, env, eout, ercode, eerr in cases:
    r = run(prog, stdin, env)
    ok = r.stdout == eout and r.returncode == ercode
    if eerr: ok = ok and eerr in r.stderr.decode()
    if eerr is None: ok = ok and b"halted after" not in r.stderr
    if not ok:
        bad += 1
        print("FAIL:", name, "\n  got", r.stdout, r.returncode, r.stderr.decode().strip())
print("%d/%d vm edge cases passed" % (len(cases) - bad, len(cases)))
sys.exit(1 if bad else 0)
