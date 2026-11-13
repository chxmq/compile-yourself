#!/usr/bin/env python3
"""superopt.py: bounded exhaustive superoptimizer for the subleq-style machine
     mem[a] -= mem[b]; if mem[a] < 0: pc = c else pc += 1   (here pc counts instructions)

Inspired by Massalin, "Superoptimizer: A Look at the Smallest Program", ASPLOS 1987.

Cell model.  Writable cells X, Y, T, U (T, U are scratch with ARBITRARY initial values) and the
read-only reserved cells Z=ZERO, O=ONE, N=NEG1.  Constants may only appear as the `b` operand,
except the no-op write `N Z` (NEG1 -= ZERO), which is the machine's unconditional jump.
`c` is an instruction index later in the sequence, the end of the sequence F (fall-through exit)
or J (jump-out exit).  Every spec fixes which cells are writable, which cells must hold given
values at exit, and for branch specs which exit must be taken.

Sequence text:  "X X;T T;T Y;X T"   one instruction "a b" per item, optionally "a b>k" (k an
instruction index; k == length means F) or "a b>J".  No ">" means "next".

Commands:
    superopt.py search [--specs A,B] [--threads N] [--md docs/SUPEROPT.md]   run the search (minutes)
    superopt.py report docs/SUPEROPT.md                       regenerate the markdown from the stored results
    superopt.py verify [--quick]                  re-verify every documented sequence (python sim + sqvm)
    superopt.py show NAME
"""
import json, os, random, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(HERE, "superopt_results.json")
CSRC = os.path.join(HERE, "superopt_c.c")
CBIN = os.path.join(ROOT, "build", "superopt_c")
SQVM = os.path.join(ROOT, "vm", "sqvm")

INT_MIN, INT_MAX = -2**31, 2**31 - 1
CELLS = "XYTUZON"          # indices 0..6
M32 = 0xFFFFFFFF


def w32(v):
    v &= M32
    return v - (1 << 32) if v & 0x80000000 else v


# ---------------------------------------------------------------- sequences
def parse_seq(text):
    """-> list of (a, b, c) with a, b cell indices and c an instruction index, L (F) or L+1 (J)"""
    items = [s.strip() for s in text.split(";") if s.strip()]
    L = len(items)
    out = []
    for i, it in enumerate(items):
        ins, _, tgt = it.partition(">")
        a, b = ins.split()
        c = i + 1 if not tgt else (L + 1 if tgt.strip() == "J" else int(tgt))
        assert CELLS.index(a[0]) < 4 or (a == "N" and b == "Z"), "const write: " + it
        assert 0 <= c <= L + 1, "bad target: " + it
        out.append((CELLS.index(a), CELLS.index(b), c))
    return out


def sim(seq, x, y, t, u):
    """-> (final [X,Y,T,U,Z,O,N], exit 0=F/1=J, steps)"""
    L = len(seq)
    m = [x, y, t, u, 0, 1, -1]
    pc = steps = 0
    while pc < L:
        a, b, c = seq[pc]
        r = w32(m[a] - m[b])
        m[a] = r
        steps += 1
        assert steps < 1000, "runaway sequence"
        pc = c if r < 0 else pc + 1
    return m, pc - L, steps


# ---------------------------------------------------------------- specs (independent python reference)
def _cmp(name):
    return {"JLT0": lambda x, y: x < 0, "JGE0": lambda x, y: x >= 0, "JGT0": lambda x, y: x > 0,
            "JLE0": lambda x, y: x <= 0, "JEQ0": lambda x, y: x == 0, "JNE0": lambda x, y: x != 0,
            "JEQ_XY": lambda x, y: x == y, "JNE_XY": lambda x, y: x != y,
            "JLT_XY": lambda x, y: x < y, "JGE_XY": lambda x, y: x >= y}[name]


def ref(spec, x, y):
    """-> (required {cell index: value}, expected exit)"""
    n, k = spec["kind"], spec.get("k")
    if n == "CLEAR": return {0: 0}, 0
    if n == "MOV": return {0: y, 1: y}, 0
    if n == "NEG": return {0: w32(-x)}, 0
    if n == "NEGTO": return {0: w32(-y), 1: y}, 0
    if n == "ADD": return {0: w32(x + y), 1: y}, 0
    if n == "SUB": return {0: w32(x - y), 1: y}, 0
    if n == "DBL": return {0: w32(2 * x)}, 0
    if n == "ADD3": return {3: w32(x + y), 0: x, 1: y}, 0
    if n == "SUB3": return {3: w32(x - y), 0: x, 1: y}, 0
    if n == "CONST": return {0: k}, 0
    if n == "JMP": return {0: x}, 1
    if n in ("JEQ_XY", "JNE_XY", "JLT_XY", "JGE_XY"):
        return {0: x, 1: y}, int(_cmp(n)(x, y))
    return {0: x}, int(_cmp(n)(x, y))


def edge_values():
    vs = {0, 1, -1, 2, -2, 3, -3, 4, -4, 5, 7, -7, 10, 100, -100, 255, 256, 1000, -1000, 65535, 65536,
          INT_MIN, INT_MIN + 1, INT_MIN + 2, INT_MAX, INT_MAX - 1, INT_MAX - 2}
    for k in range(2, 31):
        for d in (-1, 0, 1):
            vs.add(w32((1 << k) + d)); vs.add(w32(-(1 << k) + d))
    rnd = random.Random(12345)
    for _ in range(12):
        vs.add(w32(rnd.getrandbits(32)))
    return sorted(vs)


def rand_val(rnd):
    r = rnd.random()
    if r < 0.35: return w32(rnd.getrandbits(32))
    if r < 0.55: return rnd.randint(-20, 20)
    if r < 0.70: return rnd.choice([INT_MIN, INT_MIN + 1, INT_MAX, INT_MAX - 1, 0, 1, -1])
    if r < 0.85: return w32(rnd.choice([1, -1]) * (1 << rnd.randint(0, 31)) + rnd.randint(-2, 2))
    return rnd.randint(-100000, 100000)


def verify_seq(spec, text, nrand=20000, seed=1, edge=True):
    """exhaustive on edge values (all pairs, several scratch settings) + random samples.
    -> (ok, info) ; info has worst steps per exit and per sign class of X"""
    seq = parse_seq(text)
    rnd = random.Random(seed)
    uses_y = spec["kind"] not in ("CLEAR", "NEG", "DBL", "CONST", "JMP") and not spec["kind"].endswith("0")
    wf = wj = 0
    cls = [0, 0, 0]
    cases = []
    if edge:
        ev = edge_values()
        for x in ev:
            for y in (ev if uses_y else [0]):
                cases.append((x, y, rnd.choice([0, INT_MIN, INT_MAX, w32(rnd.getrandbits(32))]),
                              rnd.choice([0, -7, INT_MIN, w32(rnd.getrandbits(32))])))
    for _ in range(nrand):
        x = rand_val(rnd)
        y = x if rnd.random() < 0.1 else w32(x + rnd.choice([-1, 1])) if rnd.random() < 0.1 else rand_val(rnd)
        cases.append((x, y, rand_val(rnd), rand_val(rnd)))
    for (x, y, t, u) in cases:
        m, ex, st = sim(seq, x, y, t, u)
        req, want_ex = ref(spec, x, y)
        if ex != want_ex or m[4:] != [0, 1, -1] or any(m[c] != v for c, v in req.items()):
            return False, "FAIL x=%d y=%d t=%d u=%d -> %s exit %d (want %s exit %d)" % (x, y, t, u, m, ex, req, want_ex)
        if ex: wj = max(wj, st)
        else: wf = max(wf, st)
        c = 0 if x < 0 else 1 if x == 0 else 2
        cls[c] = max(cls[c], st)
    return True, {"cases": len(cases), "wF": wf, "wJ": wj, "cls": cls, "worst": max(wf, wj)}


# ---------------------------------------------------------------- real VM check
class Prog:
    def __init__(self):
        self.ins, self.labels = [], {}

    def pc(self): return 6 + 3 * len(self.ins)
    def label(self, n): self.labels[n] = self.pc()
    def emit(self, a, b, c=None): self.ins.append((a, b, ("next",) if c is None else c))


def vm_program(spec, text, x, y, t, u):
    """machine program: init cells, run the sequence, then check the contract and print K (ok) or X"""
    seq = parse_seq(text)
    L = len(seq)
    req, want_ex = ref(spec, x, y)
    P = Prog()
    Z, O, N_ = 3, 4, 5
    cell = lambda n: ("cell", n)
    addr_of = {"X": cell("X"), "Y": cell("Y"), "T": cell("T"), "U": cell("U"), "Z": Z, "O": O, "N": N_}
    for (a, b, c) in seq:
        tgt = ("lab", "LF") if c == L else ("lab", "LJ") if c == L + 1 else 6 + 3 * c
        P.emit(addr_of[CELLS[a]], addr_of[CELLS[b]], tgt if c != len(P.ins) + 1 else None)
    OUT = 1048575
    pool = []

    def chain(name, expect):
        P.label(name)
        if expect:
            for ck, val in req.items():
                pool.append(val)
                P.emit(cell("W"), cell("W")); P.emit(cell("V"), cell("V"))
                P.emit(cell("V"), cell("XYTU"[ck])); P.emit(cell("W"), cell("V"))
                P.emit(cell("W"), ("pool", len(pool) - 1)); P.emit(cell("W"), Z, ("lab", "FAIL"))
                P.emit(cell("V"), cell("V")); P.emit(cell("V"), cell("W")); P.emit(cell("V"), Z, ("lab", "FAIL"))
            P.emit(OUT, cell("NK")); P.emit(N_, Z, -1)
        else:
            P.emit(OUT, cell("NX")); P.emit(N_, Z, -1)

    chain("LF", want_ex == 0)
    chain("LJ", want_ex == 1)
    P.label("FAIL"); P.emit(OUT, cell("NX")); P.emit(N_, Z, -1)
    code_end = P.pc()
    data = {"X": x, "Y": y, "T": t, "U": u, "W": 0, "V": 0, "NK": -ord("K"), "NX": -ord("X")}
    base = code_end
    caddr = {n: base + i for i, n in enumerate(data)}
    poolbase = base + len(data)
    words = [5, 3, 6, 0, 1, -1]
    for (a, b, c) in P.ins:
        def res(v):
            if isinstance(v, tuple):
                if v[0] == "cell": return caddr[v[1]]
                if v[0] == "pool": return poolbase + v[1]
                if v[0] == "lab": return P.labels[v[1]]
            return v
        pcnow = 6 + 3 * (len(words) - 6) // 3
        words += [res(a), res(b), (len(words) + 3) if c == ("next",) else res(c)]
    words += list(data.values()) + pool
    return "\n".join(str(w) for w in words) + "\n"


def vm_check(spec, text, vectors):
    """run each vector through the real sqvm; -> (ok, message)"""
    with tempfile.TemporaryDirectory() as d:
        pf = os.path.join(d, "p.txt")
        for (x, y, t, u) in vectors:
            with open(pf, "w") as f:
                f.write(vm_program(spec, text, x, y, t, u))
            r = subprocess.run([SQVM, pf], capture_output=True, timeout=20)
            if r.stdout != b"K":
                return False, "sqvm said %r for x=%d y=%d t=%d u=%d (%s)" % (r.stdout, x, y, t, u, r.stderr[:80])
    return True, "ok"


def vm_vectors(spec, n=24, seed=7):
    rnd = random.Random(seed)
    base = [INT_MIN, INT_MIN + 1, -2, -1, 0, 1, 2, INT_MAX - 1, INT_MAX, 1 << 30, -(1 << 30), 12345, -777]
    vs = []
    for x in base:
        for y in (base if spec["kind"] in ("MOV", "ADD", "SUB", "NEGTO", "ADD3", "SUB3") or spec["kind"].endswith("XY") else [0]):
            vs.append((x, y, rnd.choice([0, INT_MIN, 99, INT_MAX]), rnd.choice([0, -5, INT_MAX])))
    rnd.shuffle(vs)
    return vs[:n] + [(5, 5, 0, 0), (-3, -3, 1, 1), (INT_MAX, INT_MIN, 0, 0), (INT_MIN, INT_MAX, 0, 0)]


# ---------------------------------------------------------------- spec table
def unary(k):
    if k == 0: return "X X"
    return "X X;" + ";".join(["X N" if k > 0 else "X O"] * abs(k))


SPECS = []
def add(name, cid, wmask, desc, ours, ours_note, ks, kb, k=0, kind=None):
    SPECS.append(dict(name=name, kind=kind or name, id=cid, k=k, wmask=wmask, desc=desc, ours=ours,
                      ours_note=ours_note, kstraight=ks, kbranch=kb))

# wmask bits: X=1 Y=2 T=4 U=8.  ids match the enum in superopt_c.c
add("CLEAR", 0, 5, "X := 0", "X X", "CLR", 8, 5)
add("MOV", 1, 7, "X := Y (Y kept, T clobbered)", "X X;T T;T Y;X T", "mov", 7, 5)
add("NEG", 2, 13, "X := -X in place (T, U clobbered)", "T T;U U;T X;U T;X X;X U", "unary minus on a variable (v1c unary())", 6, 5)
add("NEGTO", 3, 3, "X := -Y (Y kept)", "X X;X Y", "NEGTO / unary minus into a temp", 8, 5)
add("ADD", 4, 7, "X += Y (T clobbered)", "T T;T Y;X T", "in-place add", 7, 5)
add("SUB", 5, 3, "X -= Y", "X Y", "in-place sub", 8, 5)
add("DBL", 6, 5, "X += X (T clobbered)", "T T;T X;X T", "ADD x,x", 8, 5)
add("ADD3", 7, 15, "U := X + Y (X, Y kept, T clobbered)", "T T;T X;T Y;U U;U T", "generic add (add_sub)", 6, 4)
add("SUB3", 8, 15, "U := X - Y (X, Y kept, T clobbered)", "U U;T T;T X;U T;U Y", "generic sub (add_sub)", 6, 4)
for kk in range(-3, 9):
    add("CONST%+d" % kk, 9, 5, "X := %d using only ONE/NEG1 (T clobbered)" % kk, unary(kk),
        "unary ONE/NEG1 loading (v1c really uses a pool cell: 2 steps)", 8, 5, k=kk, kind="CONST")
add("JMP", 10, 5, "unconditional jump out", "N Z>J", "jmp", 8, 5)
add("JLT0", 11, 5, "jump if X < 0 (X kept, T clobbered)", "X Z>J", "BLT0", 8, 5)
add("JGE0", 12, 5, "jump if X >= 0", "X Z>2;N Z>J", "'<' with 0 (false->lf)", 8, 5)
add("JGT0", 13, 5, "jump if X > 0", "X Z>3;T T;T X>J", "'<=' with 0", 8, 5)
add("JLE0", 14, 5, "jump if X <= 0", "X Z>J;T T;T X>4;N Z>J", "'>' with 0", 8, 5)
add("JEQ0", 15, 5, "jump if X == 0", "X Z>4;T T;T X>4;N Z>J", "jz()", 8, 5)
add("JNE0", 16, 5, "jump if X != 0", "X Z>J;T T;T X>J", "jnz()", 8, 5)
add("JEQ_XY", 17, 7, "jump if X == Y (X, Y kept; T clobbered; U also free in 4-cell run)",
    "U U;T T;T X;U T;U Y;U Z>9;T T;T U>9;N Z>J", "'!=' on two non-temp cells: diff + jz", 6, 5)
add("JNE_XY", 18, 7, "jump if X != Y", "U U;T T;T X;U T;U Y;U Z>J;T T;T U>J", "'==' on two non-temp cells: diff + jnz", 6, 5)
add("JLT_XY", 19, 7, "jump if X < Y, true signed compare (no overflow)",
    "X Z>9;Y Z>11;U U;T T;T X;U T;U Y;U Z>J;N Z>11;Y Z>2;N Z>J", "lt_jt (non-temp x)", 6, 5)
add("JGE_XY", 20, 7, "jump if X >= Y, true signed compare",
    "X Z>9;Y Z>J;U U;T T;T X;U T;U Y;U Z>10;N Z>J;Y Z>2", "lt_jf (non-temp x)", 6, 5)
SPEC = {s["name"]: s for s in SPECS}


# ---------------------------------------------------------------- search driver
def build_c():
    os.makedirs(os.path.dirname(CBIN), exist_ok=True)
    if not os.path.exists(CBIN) or os.path.getmtime(CBIN) < os.path.getmtime(CSRC):
        subprocess.check_call(["cc", "-O2", "-pthread", "-o", CBIN, CSRC])


def run_c(spec, wmask, K, branch, threads):
    cmd = [CBIN, str(spec["id"]), str(spec["k"]), str(wmask), str(K), str(branch), str(threads)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    res = {}
    for line in out.splitlines():
        f = line.split()
        L = int(f[1])
        if f[3] == "0" and len(f) == 4:
            res.setdefault(L, {"count": 0})
            continue
        tag = f[4]
        d = res.setdefault(L, {})
        d["count"] = int(f[3])
        d[tag] = {"steps": int(f[6]), "wF": int(f[8]), "wJ": int(f[10]), "cls": [int(f[12]), int(f[13]), int(f[14])],
                  "seq": line.split(" seq ", 1)[1]}
    return res


def search_one(spec, threads):
    t0 = time.time()
    r_str = run_c(spec, spec["wmask"], spec["kstraight"], 0, threads)
    r_br = run_c(spec, spec["wmask"], spec["kbranch"], 1, threads)
    r4 = None
    if spec["wmask"] == 7 and spec["kind"].endswith("XY"):
        r4 = run_c(spec, 15, 4, 1, threads)      # also give it the second scratch cell U
    ours = verify_seq(spec, spec["ours"], nrand=2000)
    entry = {"desc": spec["desc"], "kind": spec["kind"], "k": spec["k"], "wmask": spec["wmask"],
             "bounds": {"straight": spec["kstraight"], "branch": spec["kbranch"]},
             "ours": {"seq": spec["ours"], "len": len(parse_seq(spec["ours"])), "note": spec["ours_note"],
                      "ok": ours[0], **(ours[1] if ours[0] else {})},
             "secs": 0}
    best = None
    for tag, res in (("straight", r_str), ("branch", r_br), ("branch+U", r4)):
        if res is None: continue
        lens = sorted(L for L, d in res.items() if d.get("count"))
        entry[tag] = {str(L): res[L] for L in lens}
        for L in lens:
            for which in ("MM", "SUM"):
                cand = res[L][which]
                if best is None or (len(parse_seq(cand["seq"])), cand["steps"]) < (len(parse_seq(best["seq"])), best["steps"]):
                    best = dict(cand, which=which, mode=tag)
    entry["min_len"] = min([int(L) for t in ("straight", "branch", "branch+U") for L in entry.get(t, {})], default=None)
    # candidates for documentation: shortest-code (first found) and fewest worst-case steps / min summed class steps
    cands = {}
    for tag in ("straight", "branch", "branch+U"):
        for L, d in entry.get(tag, {}).items():
            for which in ("MM", "SUM"):
                c = d[which]
                cands.setdefault(c["seq"], dict(c, mode=tag, L=int(L)))
    confirmed = []
    for seq, c in cands.items():
        ok, info = verify_seq(spec, seq, nrand=100000, seed=99)
        if ok:
            c = dict(c, confirmed=True, **{"v_" + k: v for k, v in info.items() if k != "cases"})
        else:
            c = dict(c, confirmed=False, why=info)
        confirmed.append(c)
    confirmed.sort(key=lambda c: (c["L"], c["steps"]))
    entry["candidates"] = confirmed
    entry["secs"] = round(time.time() - t0, 1)
    return entry


def fmt_ours(e):
    o = e["ours"]
    return "%d" % o["len"]


def write_md(results, path):
    rows = []
    for s in SPECS:
        e = results.get(s["name"])
        if not e: continue
        o = e["ours"]
        cs = [c for c in e["candidates"] if c.get("confirmed")]
        minlen = e["min_len"]
        bestc = cs[0] if cs else None
        o_steps = "F%d/J%d" % (o["wF"], o["wJ"]) if s["kind"].startswith("J") and s["kind"] != "JMP" else str(o["worst"])
        if minlen is None:
            verdict = "none found (straight <= %d, branching <= %d instr)" % (e["bounds"]["straight"], e["bounds"]["branch"])
            seq = "-"
        else:
            verdict = ("equal" if minlen == o["len"] else "**shorter**" if minlen < o["len"] else "(ours shorter?)")
            seq = "`%s`" % bestc["seq"] if bestc else "-"
        rows.append((s, e, o, minlen, o_steps, verdict, seq, bestc))
    L = []
    L.append("| primitive | spec | ours (instr) | ours worst steps | min found (instr) | worst steps of found | verdict | sequence found |")
    L.append("|---|---|---:|---:|---:|---:|---|---|")
    for (s, e, o, minlen, o_steps, verdict, seq, bestc) in rows:
        fs = "-"
        if bestc:
            fs = "F%d/J%d" % (bestc["wF"], bestc["wJ"]) if s["kind"].startswith("J") and s["kind"] != "JMP" else str(bestc["steps"])
        L.append("| %s | %s | %d | %s | %s | %s | %s | %s |" % (s["name"], s["desc"], o["len"], o_steps,
                 minlen if minlen is not None else ">%d/%d" % (e["bounds"]["straight"], e["bounds"]["branch"]), fs, verdict, seq))
    table = "\n".join(L)
    pareto = []
    for (s, e, o, minlen, o_steps, verdict, seq, bestc) in rows:
        shown = 0
        for c in e["candidates"]:
            if c.get("confirmed") and shown < 2 and (c["L"] < o["len"] or c["steps"] < o["worst"]):
                shown += 1
                pareto.append("| %s | %d | `%s` | %d/%d/%d | %d | `%s` | %d/%d/%d | %d |" % (
                    s["name"], c["L"], c["seq"], *c["cls"], c["steps"], o["seq"], *o["cls"], o["worst"]))
    ptab = ("| primitive | found instr | found sequence | steps (X<0 / X=0 / X>0) | worst | ours | ours steps (X<0/X=0/X>0) | ours worst |\n"
            "|---|---:|---|---|---:|---|---|---:|\n" + "\n".join(pareto))
    with open(path, "w") as f:
        f.write(DOC_HEAD)
        f.write("\n## 3. Results\n\n`ours` = the sequence tier-2 `v1c.py` emits for that primitive on its common path "
                "(encoded in this cell model and verified by the same checker).  \"min found\" = shortest verified "
                "sequence within the stated bounds (see section 2); \">K\" = nothing found up to K instructions.\n\n")
        f.write(table + "\n\n## 4. Sequences that beat or differ from the compiler's\n\n")
        f.write(ptab + "\n")
        f.write(DOC_TAIL)


DOC_HEAD = """# Superoptimizer results (`toolkit/superopt.py`)

A bounded exhaustive-search superoptimizer for the one-instruction machine
(`mem[a] -= mem[b]; if mem[a] < 0 then pc = c else pc += 3`), in the spirit of Massalin,
*Superoptimizer: A Look at the Smallest Program* (ASPLOS 1987).  Original implementation: a C core
(`toolkit/superopt_c.c`, multithreaded DFS over all instruction sequences) driven by Python.

## 1. Model

- Cells: writable `X, Y, T, U` (`T`, `U` are scratch with an arbitrary initial value, never assumed 0) and the
  reserved read-only `ZERO, ONE, NEG1` (`Z, O, N`), which must still hold their values at every exit.
- An instruction may write only to the spec's writable cells.  Constants appear only as the `b` operand, plus the
  no-op write `NEG1 -= ZERO`, which always branches (the unconditional jump idiom).  Writing a constant cell to
  something else and restoring it later is deliberately excluded.
- Branch targets `c`: any later instruction, the end of the sequence (`F`, fall-through exit) or `J` (jump-out exit).
  The search enumerates forward jumps only (so every candidate terminates); the compiler's own sequences are also
  allowed to jump backwards (the signed-compare idiom shares its subtract code), which the checker simulates with a step cap.  Steps are instructions executed, per path.
- A spec fixes which cells must hold which values at exit (all other writable cells are clobberable) and, for branch
  specs, which exit is taken: e.g. `JEQ0` = leave via `J` iff `X == 0`, via `F` otherwise, `X` preserved at both exits.
- Pooled literal cells (`x = 5` in 2 steps with a cell holding -5) are outside the model: only `ONE`/`NEG1` exist.
  That is why the constant-load rows compare against a unary ONE/NEG1 baseline, not the compiler's real 2 steps.

## 2. Method and what "minimal" means

- Sequences of length 1..K are enumerated exhaustively (with `a == b` instructions never branching, `NEG1 -= ZERO`
  only as a jump).  Straight-line specs are searched with and without internal branches.
  Bounds K: straight-line 6-8 instructions depending on the number of cells, branching 5 (4 when a fourth cell
  `U` is available), listed per spec in `toolkit/superopt_results.json` (`bounds`).
- A candidate must pass the equivalence test on every vector in a table of edge values (0, +-1, +-2, +-3, INT_MIN(+1,+2),
  INT_MAX(-1), powers of two and their neighbours, 100, 12345, random) for all (X, Y) pairs with two scratch
  settings each (about 4000 vectors); survivors are re-checked in Python with an independently written reference over
  all-pairs edge values plus 100,000 random samples (scratch cells random too).
  Documented sequences are additionally run on the real `vm/sqvm` (`tests/test_superopt.sh`).
- **"Minimal" means minimal within the searched bound and cell model above**, with equivalence established by testing
  (edge values + random), not by a symbolic proof.  A passing candidate is a very strong but not a formal guarantee;
  a search that finds nothing proves nothing beyond the bound K.
- "worst steps" is the maximum number of executed instructions over all test vectors, per exit for branch specs
  (`F` = fall-through exit, `J` = jump exit).  The search keeps two winners per length: fewest worst-case steps,
  and fewest summed per-sign-class worst steps (X<0, X==0, X>0).
"""

DOC_TAIL = """
## 5. Reading the numbers

- **Equal rows** confirm the hand-written v1c sequences are already optimal in this model: CLR, MOV (4), in-place ADD (3),
  DBL (3), SUB (1), NEGTO (2), in-place NEG (6), three-address ADD/SUB (5 each), JMP, `BLT0`, and the sign branches
  `>=0` (2), `>0` (3), `!=0` (3) are all minimal within the bounds.  The search also reproduces the hand-found
  costs exactly for ONE/NEG1 constants (|k|+1 for small k).
- **Nothing found** for the two-variable equality and true signed-compare branches (9, 8, 11 and 10 instructions in v1c):
  no equivalent exists with up to 5 instructions (6 straight-line, 4 with a spare cell `U`).  The exhaustive search
  cannot say how far below the compiler's 8-11 the true minimum is; it only rules out <= 5 (<= 4 with `U`).
- **Strictly shorter found:** `JEQ0` (3 instructions, `T=0; T-=X; if T<0 skip; T-=ONE; if T<0 jump`; the trick is that
  `-X-1 < 0` exactly when `X >= 0`, after `-X >= 0` has excluded `X > 0` and `INT_MIN`) vs `jz()`'s 4, and the same
  trick for `JLE0` (4 instructions with worst 3 steps vs 4).  `CONST+8` in 8 instructions uses doubling (`X=4; T=-X; X-=T`).
  The constant load is moot for the compiler, which loads constants from pool cells in 2 steps.
- **Why the compiler was NOT changed.**  The `JEQ0`/`JLE0` sequences cut the worst case (3 vs 4 steps) but make the
  `X < 0` path slower (3 vs 1 step), and the compiler's idioms are tuned for the common sign.  Per-class step counts
  (`X<0 / X=0 / X>0`) are in the table above: ours 1/4/3, found 3/3/2 (sum 8 vs 8, no sequence beats the sum).
  As a one-off experiment (not committed) `jz()` in a scratch copy of `v1c.py` was replaced with the 3-instruction form and the
  whole corpus re-run on `vm/sqvm`: total steps over `tests/corpus` went from 92.6M to 94.6M, with most programs a
  little faster but `gcd` (`while (a != b)`, where `a - b` is mostly positive) 12% slower (17.0M -> 19.0M).  Outputs
  of that experiment were not diffed and no other test was run, since it was discarded.  No change was made to
  `tier2/v1c.py`, `tier3/tier3.v1` or the generated output.
"""


def cmd_search(args):
    specs = SPECS
    threads = os.cpu_count() or 4
    md = None
    i = 0
    while i < len(args):
        if args[i] == "--specs": names = args[i + 1].split(","); specs = [s for s in SPECS if s["name"] in names or s["kind"] in names]; i += 2
        elif args[i] == "--threads": threads = int(args[i + 1]); i += 2
        elif args[i] == "--md": md = args[i + 1]; i += 2
        else: raise SystemExit("bad arg " + args[i])
    build_c()
    results = json.load(open(RESULTS)) if os.path.exists(RESULTS) else {}
    for s in specs:
        e = search_one(s, threads)
        results[s["name"]] = e
        o = e["ours"]
        print("%-8s ours %d (%s)  min found %s  [%.1fs]" % (s["name"], o["len"], "ok" if o["ok"] else "OURS FAILS!", e["min_len"], e["secs"]), flush=True)
        json.dump(results, open(RESULTS, "w"), indent=1, sort_keys=True)
    if md:
        write_md(results, md)


def cmd_verify(args):
    quick = "--quick" in args
    results = json.load(open(RESULTS))
    bad = 0
    nseq = 0
    t0 = time.time()
    for s in SPECS:
        e = results.get(s["name"])
        if e is None:
            print("missing result for", s["name"]); bad += 1; continue
        seqs = [("ours", s["ours"])]
        for c in e["candidates"]:
            if c.get("confirmed"):
                seqs.append(("found L%d" % c["L"], c["seq"]))
        done = set()
        for tag, sq in seqs:
            if sq in done: continue
            done.add(sq)
            ok, info = verify_seq(s, sq, nrand=3000 if quick else 20000)
            nseq += 1
            if not ok:
                print("FAIL", s["name"], tag, sq, info); bad += 1; continue
            if not quick or tag == "ours":
                ok, msg = vm_check(s, sq, vm_vectors(s, 10 if quick else 24))
                if not ok:
                    print("VMFAIL", s["name"], tag, sq, msg); bad += 1
    print("superopt verify: %d sequences, %d failures, %.1fs" % (nseq, bad, time.time() - t0))
    return 1 if bad else 0


def main():
    if len(sys.argv) < 2: print(__doc__); return 2
    c, a = sys.argv[1], sys.argv[2:]
    if c == "search": cmd_search(a); return 0
    if c == "report":
        write_md(json.load(open(RESULTS)), a[0]); return 0
    if c == "verify": return cmd_verify(a)
    if c == "show":
        print(json.dumps(json.load(open(RESULTS))[a[0]], indent=1)); return 0
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main())
