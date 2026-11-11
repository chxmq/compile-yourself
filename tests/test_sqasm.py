#!/usr/bin/env python3
"""Macro tests: assemble snippets, run in a tiny Python simulator (checked against sqvm
elsewhere via test_vm.py and the tier1 tests), compare cell contents."""
import os, sys, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "toolkit"))
from sqasm import Asm, N, IN, OUT, _wrap32

def sim(prog, inp=b"", limit=2_000_000):
    mem = {i: w for i, w in enumerate(prog.words)}
    pc, steps, out, ip = 0, 0, bytearray(), 0
    g = mem.get
    while 0 <= pc <= N - 3:
        steps += 1
        assert steps <= limit, "step limit"
        a, b, c = g(pc, 0), g(pc + 1, 0), g(pc + 2, 0)
        assert 0 <= a < N and 0 <= b < N and a != IN, (pc, a, b, c)
        if b == IN:
            vb = inp[ip] if ip < len(inp) else -1
            ip += 1 if ip < len(inp) else 0
        else:
            vb = g(b, 0) if b != OUT else 0
        r = _wrap32((g(a, 0) if a != OUT else 0) - vb)
        if a == OUT: out.append(r & 255)
        else: mem[a] = r
        pc = c if r < 0 else pc + 3
    return mem, bytes(out), steps

def run(build, inp=b""):
    a = Asm(); build(a); a.halt()
    p = a.assemble()
    mem, out, steps = sim(p, inp)
    return p, mem, out, steps

I32 = [0, 1, -1, 2, -2, 5, 100, -100, 65535, 2**31 - 1, -2**31, -2**31 + 1, 2**30, -2**30, 2000000000, -2000000000]
random.seed(1)
I32 += [random.randint(-2**31, 2**31 - 1) for _ in range(6)]
fails = 0
def check(name, got, exp):
    global fails
    if got != exp:
        fails += 1; print("FAIL", name, "got", got, "expected", exp)

def val(p, mem, name): return mem.get(p.syms[name], 0)

for x in I32:
    for y in I32:
        def b(a):
            a.cell("x", x); a.cell("y", y); a.bss("r")
            a.mov("r", "x"); a.add("r", "y")
        p, m, _, _ = run(b); check("add %d %d" % (x, y), val(p, m, "r"), _wrap32(x + y))
        def b(a):
            a.cell("x", x); a.cell("y", y); a.bss("r")
            a.mov("r", "x"); a.sub("r", "y")
        p, m, _, _ = run(b); check("sub", val(p, m, "r"), _wrap32(x - y))
        for nm, f in (("blt", lambda: x < y), ("bge", lambda: x >= y), ("bgt", lambda: x > y), ("ble", lambda: x <= y), ("beq", lambda: x == y), ("bne", lambda: x != y)):
            def b(a):
                a.cell("x", x); a.cell("y", y); a.bss("r")
                getattr(a, nm)("x", "y", "yes")
                a.halt(); a.label("yes"); a.mov("r", "ONE")
            p, m, _, _ = run(b); check("%s %d %d" % (nm, x, y), val(p, m, "r"), int(f()))
        def b(a):
            a.cell("x", x); a.cell("y", y); a.bss("r")
            a.slt("r", "x", "y")
        p, m, _, _ = run(b); check("slt %d %d" % (x, y), val(p, m, "r"), int(x < y))

for x in I32:
    for nm, f in (("bz", x == 0), ("bnz", x != 0), ("bgt0", x > 0), ("ble0", x <= 0), ("bge0", x >= 0), ("blt0", x < 0)):
        def b(a):
            a.cell("x", x); a.bss("r")
            getattr(a, nm)("x", "yes")
            a.halt(); a.label("yes"); a.mov("r", "ONE")
        p, m, _, _ = run(b); check("%s %d" % (nm, x), val(p, m, "r"), int(f))
        check("%s leaves x" % nm, val(p, m, "x"), _wrap32(x))
    def b(a):
        a.cell("x", x); a.cell("z", 7); a.bss("r")
        a.mov("r", "x"); a.neg("r"); a.negto("z", "x"); a.addi("x", 1234567); a.mov("T1", "x")
        a.movi("r", 99999); a.addi("r", -5); a.subi("r", 3)
    p, m, _, _ = run(b)
    check("neg/negto/addi", (val(p, m, "z"), val(p, m, "x"), val(p, m, "r")), (_wrap32(-x), _wrap32(x + 1234567), 99991))
    def b(a):
        a.cell("x", x); a.bss("r"); a.neg("x")
    p, m, _, _ = run(b); check("neg", val(p, m, "x"), _wrap32(-x))

# pointers: load/store/jmpi/call/ret
def b(a):
    a.array("arr", [10, 20, 30, 40]); a.bss("p"); a.bss("r"); a.bss("s"); a.cell("v", -777)
    a.movi("p", "arr+2"); a.load("r", "p")           # r = 30
    a.store("p", "v")                                # arr[2] = -777
    a.inc("p"); a.movi("s", 55); a.store("p", "s")   # arr[3] = 55
    a.movi("p", "arr+2"); a.load("s", "p")
p, m, _, _ = run(b)
check("load/store", (val(p, m, "r"), val(p, m, "s"), m[p.syms["arr"] + 2], m[p.syms["arr"] + 3], m[p.syms["arr"]]), (30, -777, -777, 55, 10))
def b(a):
    a.cell("tgt", "dest"); a.bss("r")
    a.jmpi("tgt"); a.mov("r", "ONE"); a.halt()
    a.label("dest"); a.mov("r", "NEG1")
p, m, _, _ = run(b); check("jmpi", val(p, m, "r"), -1)
def b(a):
    a.bss("ra"); a.bss("r"); a.bss("ra2"); a.entry = "main"
    a.label("f"); a.inc("r"); a.ret("ra")
    a.label("g"); a.call("f", "ra"); a.call("f", "ra"); a.ret("ra2")
    a.label("main"); a.call("g", "ra2"); a.call("f", "ra"); a.putc_const("O"); a.putc_const("K")
p, m, out, _ = run(b); check("call/ret", (val(p, m, "r"), out), (3, b"OK"))
# IO
def b(a):
    a.bss("c"); a.bss("n"); a.entry = "s"
    a.label("s"); a.getc("c"); a.blt0("c", "end"); a.putc("c"); a.inc("n"); a.jmp("s"); a.label("end")
for inp in (b"", b"abc", bytes(range(256))):
    p, m, out, _ = run(b, inp); check("getc/putc %r" % inp[:3], (out, val(p, m, "n")), (inp, len(inp)))
def b(a):
    a.cell("big", 321 + 256 * 1000); a.putc("big"); a.putc_const(0); a.putc_const(255)
p, m, out, _ = run(b); check("putc low8", out, bytes([65, 0, 255]))
def b(a):
    a.cell("k", 3); a.cell("w", "k"); a.cell("z", "k+2")   # symbolic init
p, m, _, _ = run(b); check("symbolic init", (val(p, m, "w"), val(p, m, "z")), (p.syms["k"], p.syms["k"] + 2))
# text front end
from sqasm import assemble_text
a = assemble_text("""
 .cell n 3
 .bss r
 loop: ADD r, n      ; r += n
       DEC n
       BGT0 n, loop
       HALT
""")
p = a.assemble(); m, _, _ = sim(p); check("text asm", m[p.syms["r"]], 6)
print("sqasm tests:", "FAILED %d" % fails if fails else "all passed")
sys.exit(1 if fails else 0)
