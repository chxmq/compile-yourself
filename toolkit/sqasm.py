#!/usr/bin/env python3
"""sqasm: macro assembler for the one-instruction subtract-and-branch-if-negative machine.

Importable library (the tier-2 compiler emits code through `Asm`) plus a CLI.

Machine:  instruction `a b c`:  mem[a] -= mem[b]; if result < 0: pc = c else pc += 3.

Layout produced by `Asm.assemble()` (see docs/ABI.md, "Final layout (assembler)"):

    0..2   boot jump   NEG1 ZERO <entry>      (always jumps: -1 - 0 < 0)
    3      ZERO  = 0   never modified
    4      ONE   = 1   never modified
    5      NEG1  = -1  never modified
    6..    code (3 words per instruction, in emission order)
    ...    initialised data (cells/arrays with a nonzero or symbolic init), in order
    ...    zero-initialised data (cells declared with init 0, `bss`) -- never written to
           the file, because trailing zeros are trimmed (memory starts zeroed)
    N-3..  (optional) cells pinned with addr=... (compiler's top-down data region)

Operand expressions ("exprs") are ints or strings: `name`, `name+3`, `-name`, `a+b-2`.
Names are code labels or data cells; everything is resolved in pass 2 so forward
references work.  A 'c' of None means "the next instruction" (the harmless branch).

Macro notes: every macro that needs scratch uses the zero-initialised cells T and U
(and T1 for compares) and re-clears them itself, so nothing is assumed about their value
on entry.  Destination and source of MOV/NEGTO must differ unless stated.
"""
import re
import sys
import json

N = 1 << 20
IN = N - 2
OUT = N - 1
ZERO_ADDR, ONE_ADDR, NEG1_ADDR = 3, 4, 5
CODE_START = 6

_TERM = re.compile(r"\s*([+-]?)\s*([A-Za-z_.$][\w.$]*|\d+)")


def _wrap32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


class AsmError(Exception):
    pass


class Program:
    """Result of assembling."""

    def __init__(self, words, syms, code_start, code_end, data_end, listing, patches):
        self.words = words
        self.syms = syms          # name -> address
        self.code_start = code_start
        self.code_end = code_end
        self.data_end = data_end  # first address after initialised+zero data (excluding pinned)
        self.listing = listing    # list of (addr, text)
        self.patches = patches    # list of (patched word address, kind, instr label/addr)

    def text(self, per_line=3):
        w = self.words
        return "\n".join(" ".join(str(x) for x in w[i:i + per_line]) for i in range(0, len(w), per_line)) + "\n"

    def map_json(self):
        return json.dumps({
            "code_start": self.code_start, "code_end": self.code_end, "data_end": self.data_end,
            "size": len(self.words), "symbols": self.syms,
            "patched_words": [{"addr": a, "kind": k, "instr": i} for a, k, i in self.patches],
        }, indent=1, sort_keys=True)

    def listing_text(self):
        return "\n".join("%7d  %s" % (a, t) for a, t in self.listing) + "\n"


class Asm:
    def __init__(self):
        self.code = []        # list of (a, b, c, note) ; address = CODE_START + 3*index
        self.labels = {}      # code label -> address
        self.cells = []       # (name, init)       init is int or expr str; goes in data region
        self.zero_cells = []  # (name, size)       zero-initialised
        self.arrays = []      # (name, [values])   initialised arrays
        self.pinned = []      # (addr, name, [values])
        self.names = set(["ZERO", "ONE", "NEG1", "IN", "OUT"])
        self._k = {}          # const cache: key -> cell name
        self._gen = 0
        self.entry = None
        self.patch_words = []  # (expr of patched word, kind, instr label)
        self._note = None
        for t in ("T", "U", "T1"):
            self.bss(t)

    # ------------------------------------------------------------ basics
    ZERO, ONE, NEG1 = "ZERO", "ONE", "NEG1"
    T, U, T1 = "T", "U", "T1"

    def pc(self):
        return CODE_START + 3 * len(self.code)

    def gensym(self, prefix="L"):
        self._gen += 1
        return "%s$%d" % (prefix, self._gen)

    def _declare(self, name):
        if name in self.names or name in self.labels:
            raise AsmError("duplicate name %r" % name)
        self.names.add(name)

    def label(self, name=None):
        if name is None:
            name = self.gensym()
        if name in self.names or name in self.labels:
            raise AsmError("duplicate name %r" % name)
        self.labels[name] = self.pc()
        return name

    def ins(self, a, b, c=None, note=None):
        """Emit raw instruction.  c=None -> next instruction."""
        if c is None:
            c = self.pc() + 3
        self.code.append((a, b, c, note or self._note))
        return self.pc() - 3

    def cell(self, name, init=0, addr=None):
        """One data word.  Returns name.  init may be an int or an address expression."""
        self._declare(name)
        if addr is not None:
            self.pinned.append((addr, name, [init]))
        elif init == 0:
            self.zero_cells.append((name, 1))
        else:
            self.cells.append((name, init))
        return name

    def bss(self, name, size=1, addr=None):
        """Zero-initialised space (an array if size > 1)."""
        self._declare(name)
        if addr is not None:
            self.pinned.append((addr, name, [0] * size))
        else:
            self.zero_cells.append((name, size))
        return name

    def array(self, name, values, addr=None):
        """Initialised array (values: ints or exprs)."""
        self._declare(name)
        values = list(values)
        if addr is not None:
            self.pinned.append((addr, name, values))
        elif all(v == 0 for v in values):
            self.zero_cells.append((name, len(values)))
        else:
            self.arrays.append((name, values))
        return name

    def string(self, name, s, nul=True):
        data = list(s.encode("latin-1") if isinstance(s, str) else s) + ([0] if nul else [])
        return self.array(name, data)

    def const(self, v):
        """A cell holding constant v (int or expr).  Deduplicated.  Returns cell name."""
        if v == 0:
            return "ZERO"
        if v == 1:
            return "ONE"
        if v == -1:
            return "NEG1"
        key = v if isinstance(v, int) else str(v).replace(" ", "")
        if key not in self._k:
            if isinstance(key, int):
                nm = "K_%s" % (key if key >= 0 else "m%d" % -key)
            else:
                nm = "A_" + re.sub(r"\W", "_", key)
                while nm in self.names:
                    nm += "_"
            self._k[key] = self.cell(nm, v)
        return self._k[key]

    def macro(self, name):
        self._note = name

    # ------------------------------------------------------------ basic macros
    def clr(self, x):
        self.macro("CLR %s" % x)
        self.ins(x, x)

    def sub(self, x, y):
        """x -= y"""
        self.macro("SUB %s, %s" % (x, y))
        self.ins(x, y)

    def inc(self, x):
        self.macro("INC %s" % x)
        self.ins(x, "NEG1")

    def dec(self, x):
        self.macro("DEC %s" % x)
        self.ins(x, "ONE")

    def negto(self, t, y):
        """t = -y  (t and y must differ; use neg(x) in place)"""
        assert t != y, "negto needs distinct cells"
        self.macro("NEGTO %s, %s" % (t, y))
        self.ins(t, t)
        self.ins(t, y)

    def add(self, x, y):
        """x += y  (uses T).  x == y (doubling) is fine."""
        self.macro("ADD %s, %s" % (x, y))
        self.ins("T", "T")
        self.ins("T", y)
        self.ins(x, "T")

    def mov(self, x, y):
        """x = y  (uses T).  x == y emits nothing."""
        if x == y:
            return
        self.macro("MOV %s, %s" % (x, y))
        self.ins(x, x)
        self.ins("T", "T")
        self.ins("T", y)
        self.ins(x, "T")

    def neg(self, x):
        """x = -x in place (uses T, U)."""
        self.macro("NEG %s" % x)
        self.ins("T", "T")
        self.ins("U", "U")
        self.ins("T", x)      # T = -x
        self.ins("U", "T")    # U = x
        self.ins(x, x)
        self.ins(x, "U")      # x = -x

    def movi(self, x, v):
        """x = constant v (int or expr): CLR x then x -= (-v)... done through a constant cell."""
        if v == 0:
            self.clr(x)
        else:
            self.mov(x, self.const(v))

    def addi(self, x, v):
        """x += constant v"""
        if v == 0:
            return
        if v == 1:
            return self.inc(x)
        if v == -1:
            return self.dec(x)
        self.macro("ADDI %s, %s" % (x, v))
        self.ins(x, self.const(_neg_expr(v)))   # x -= (-v)

    def subi(self, x, v):
        self.addi(x, -v if isinstance(v, int) else _neg_expr(v))

    def jmp(self, label):
        self.macro("JMP %s" % label)
        self.ins("NEG1", "ZERO", label)

    def blt0(self, x, label):
        """if x < 0 goto label  (x unchanged)"""
        self.macro("BLT0 %s, %s" % (x, label))
        self.ins(x, "ZERO", label)

    def halt(self):
        self.macro("HALT")
        self.ins("NEG1", "ZERO", -1)

    # ------------------------------------------------------------ branches on sign / equality
    def bge0(self, x, label):
        skip = self.gensym("skip")
        self.blt0(x, skip)
        self.jmp(label)
        self.label(skip)

    def _pos(self, x, label):
        """if x > 0 goto label, for x known to be >= 0 (T = -x cannot overflow). Uses T."""
        self.ins("T", "T")
        self.ins("T", x, label)

    def bgt0(self, x, label):
        """if x > 0 goto label (uses T)"""
        skip = self.gensym("skip")
        self.macro("BGT0 %s, %s" % (x, label))
        self.blt0(x, skip)
        self._pos(x, label)
        self.label(skip)

    def ble0(self, x, label):
        """if x <= 0 goto label (uses T)"""
        skip = self.gensym("skip")
        self.blt0(x, label)
        self._pos(x, skip)
        self.jmp(label)
        self.label(skip)

    def bz(self, x, label):
        """if x == 0 goto label (uses T)"""
        nz = self.gensym("nz")
        self.blt0(x, nz)
        self._pos(x, nz)
        self.jmp(label)
        self.label(nz)

    def bnz(self, x, label):
        """if x != 0 goto label (uses T)"""
        self.blt0(x, label)
        self._pos(x, label)

    def beq(self, x, y, label):
        """if x == y goto label.  x - y wraps to 0 only when equal (uses T1, T)."""
        self.mov("T1", x)
        self.sub("T1", y)
        self.bz("T1", label)

    def bne(self, x, y, label):
        self.mov("T1", x)
        self.sub("T1", y)
        self.bnz("T1", label)

    def _lt(self, x, y, ltrue, lfalse):
        """Signed x < y with no overflow issue; always jumps to ltrue or lfalse."""
        xneg, same = self.gensym("xneg"), self.gensym("same")
        self.macro("LT %s, %s" % (x, y))
        self.blt0(x, xneg)            # x < 0 ?
        self.blt0(y, lfalse)          # x >= 0, y < 0  -> false
        self.label(same)              # same sign: x - y cannot overflow
        self.mov("T1", x)
        self.sub("T1", y)
        self.blt0("T1", ltrue)
        self.jmp(lfalse)
        self.label(xneg)
        self.blt0(y, same)            # both negative
        self.jmp(ltrue)               # x < 0 <= y

    def blt(self, x, y, label):
        """if x < y (true signed) goto label, else fall through"""
        f = self.gensym("f")
        self._lt(x, y, label, f)
        self.label(f)

    def bge(self, x, y, label):
        t = self.gensym("t")
        self._lt(x, y, t, label)
        self.label(t)

    def bgt(self, x, y, label):
        self.blt(y, x, label)

    def ble(self, x, y, label):
        self.bge(y, x, label)

    def slt(self, dst, x, y):
        """dst = 1 if x < y (signed) else 0"""
        t, e = self.gensym("t"), self.gensym("e")
        self.clr(dst)
        self.blt(x, y, t)
        self.jmp(e)
        self.label(t)
        self.mov(dst, "ONE")
        self.label(e)

    # ------------------------------------------------------------ pointer access by self-modification
    def _patch(self, word, src_neg):
        """word = -src_neg   where src_neg holds -p; i.e. store pointer p into patch word."""
        self.ins(word, word)
        self.ins(word, src_neg)

    def load(self, x, p):
        """x = mem[mem[p]]   (p is a cell holding an address).  Uses T.  x may equal p."""
        self.macro("LOAD %s, [%s]" % (x, p))
        li = self.gensym("ld")
        self.ins("T", "T")
        self.ins("T", p)                  # T = -p
        self._patch(li + "+1", "T")       # patch word b of the instruction below := p
        self.ins("T", "T")
        self.label(li)
        self.ins("T", 0)                  # T -= mem[b]   (b patched)
        self.patch_words.append((li + "+1", "b", li))
        self.ins(x, x)
        self.ins(x, "T")                  # x = mem[p]

    def store(self, p, v):
        """mem[mem[p]] = v   (v cell).  Uses T, U.  p and v are not modified."""
        self.macro("STORE [%s], %s" % (p, v))
        i1, i2 = self.gensym("st"), self.gensym("st")
        self.ins("T", "T")
        self.ins("T", p)                  # T = -p
        self._patch(i1 + "+0", "T")
        self._patch(i1 + "+1", "T")
        self._patch(i2 + "+0", "T")
        self.ins("U", "U")
        self.ins("U", v)                  # U = -v
        self.label(i1)
        self.ins(0, 0)                    # [p] = 0      (a and b patched)
        self.label(i2)
        self.ins(0, "U")                  # [p] -= -v    (a patched)
        self.patch_words += [(i1 + "+0", "a", i1), (i1 + "+1", "b", i1), (i2 + "+0", "a", i2)]

    def jmpi(self, target):
        """goto mem[target] (cell holding a code address).  Uses T."""
        self.macro("JMPI %s" % target)
        ji = self.gensym("ji")
        self.ins("T", "T")
        self.ins("T", target)
        self._patch(ji + "+2", "T")
        self.label(ji)
        self.ins("NEG1", "ZERO", 0)       # c patched
        self.patch_words.append((ji + "+2", "c", ji))

    def call(self, target, ra):
        """Static-frame call: ra := address after this call; goto target.  Callee ends with ret(ra)."""
        r = self.gensym("ret")
        self.macro("CALL %s (ra=%s)" % (target, ra))
        self.ins(ra, ra)
        self.ins(ra, self.const(_neg_expr(r)))   # ra = -(-r) = r  (2 steps)
        self.jmp(target)
        self.label(r)

    def ret(self, ra):
        self.jmpi(ra)

    # ------------------------------------------------------------ I/O
    def getc(self, x):
        """x = next input byte, or -1 at EOF.  x != T."""
        self.macro("GETC %s" % x)
        self.ins("T", "T")
        self.ins("T", "IN")       # T = -byte  (+1 at EOF)
        self.ins(x, x)
        self.ins(x, "T")          # x = byte (-1 at EOF)

    def putc(self, x):
        """print low 8 bits of x (uses T)."""
        self.macro("PUTC %s" % x)
        self.ins("T", "T")
        self.ins("T", x)          # T = -x
        self.ins("OUT", "T")      # prints 0 - T = x

    def putc_const(self, ch):
        if isinstance(ch, str):
            ch = ord(ch)
        self.macro("PUTC '%s'" % chr(ch) if 32 <= ch < 127 else "PUTC %d" % ch)
        self.ins("OUT", self.const(-ch))

    def puts_const(self, s):
        for ch in (s.encode("latin-1") if isinstance(s, str) else s):
            self.putc_const(ch)

    # ------------------------------------------------------------ assembling
    def assemble(self, entry=None):
        entry = entry or self.entry
        code_end = CODE_START + 3 * len(self.code)
        syms = {"ZERO": ZERO_ADDR, "ONE": ONE_ADDR, "NEG1": NEG1_ADDR, "IN": IN, "OUT": OUT}
        syms.update(self.labels)
        addr = code_end
        layout = []   # (addr, [exprs])
        for name, init in self.cells:
            syms[name] = addr
            layout.append((addr, [init]))
            addr += 1
        for name, vals in self.arrays:
            syms[name] = addr
            layout.append((addr, vals))
            addr += len(vals)
        for name, size in self.zero_cells:
            syms[name] = addr
            addr += size
        data_end = addr
        for a, name, vals in self.pinned:
            syms[name] = a
            layout.append((a, vals))
            if a + len(vals) > N - 2 or a < data_end:
                raise AsmError("pinned %s at %d overlaps code/data (data_end=%d) or IN/OUT" % (name, a, data_end))
        # pinned zero cells need no storage, others do

        def ev(e):
            if isinstance(e, int):
                return e
            total, pos, s = 0, 0, str(e).strip()
            if not s:
                raise AsmError("empty expression")
            while pos < len(s):
                m = _TERM.match(s, pos)
                if not m:
                    raise AsmError("bad expression %r" % e)
                sign, tok = m.groups()
                if tok.isdigit():
                    v = int(tok)
                elif tok in syms:
                    v = syms[tok]
                else:
                    raise AsmError("undefined name %r in %r" % (tok, e))
                total += -v if sign == "-" else v
                pos = m.end()
            return total

        if entry is None:
            boot = CODE_START
        else:
            boot = ev(entry)
        image = {0: NEG1_ADDR, 1: ZERO_ADDR, 2: boot, ZERO_ADDR: 0, ONE_ADDR: 1, NEG1_ADDR: -1}
        listing = [(0, "NEG1 ZERO %s    ; boot jump" % (entry or CODE_START)),
                   (3, "ZERO=0  ONE=1  NEG1=-1")]
        rev = {}
        for k, v in syms.items():
            if "$" not in k:
                rev.setdefault(v, []).append(k)
        for i, (a, b, c, note) in enumerate(self.code):
            base = CODE_START + 3 * i
            image[base], image[base + 1], image[base + 2] = ev(a), ev(b), ev(c)
            lab = " ".join(k + ":" for k in rev.get(base, []) if k in self.labels)
            txt = "%s %s %s" % (a, b, c)
            listing.append((base, "%-34s ; %s%s" % (txt, (lab + " ") if lab else "", note or "")))
        for a, vals in layout:
            for k, v in enumerate(vals):
                image[a + k] = ev(v)
        top = max(k for k, v in image.items() if v != 0 or k < 6)
        words = [_wrap32(image.get(i, 0)) for i in range(top + 1)]
        patches = [(ev(w), kind, ev(i)) for w, kind, i in self.patch_words]
        return Program(words, {k: v for k, v in syms.items()}, CODE_START, code_end, data_end, listing, patches)


def _neg_expr(e):
    if isinstance(e, int):
        return -e
    e = e.replace(" ", "")
    # negate each term of the sum
    out = []
    for m in re.finditer(r"([+-]?)([^+-]+)", e):
        s, t = m.groups()
        out.append(("+" if s == "-" else "-") + t)
    r = "".join(out)
    return r[1:] if r.startswith("+") else r


# ---------------------------------------------------------------------- text front end
_MACROS = ["clr", "sub", "inc", "dec", "negto", "add", "mov", "neg", "movi", "addi", "subi", "jmp", "blt0", "halt",
           "bge0", "bgt0", "ble0", "bz", "bnz", "beq", "bne", "blt", "bge", "bgt", "ble", "slt", "load", "store",
           "jmpi", "call", "ret", "getc", "putc", "putc_const", "puts_const"]


def _tok(s):
    s = s.strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"'.'", s):
        return ord(s[1])
    return s


def assemble_text(src):
    """Tiny text syntax:  `label:`  `MACRO a, b`  `.cell n [init]`  `.bss n [size]`  `.array n v v v`
    `.string n text`  `.entry label`  `.ins a b [c]` ; comments start with ';' or '#'."""
    a = Asm()
    for ln, line in enumerate(src.splitlines(), 1):
        line = re.sub(r"\s[;#].*$|^[;#].*$", "", line).strip()
        if not line:
            continue
        try:
            while True:
                m = re.match(r"^([A-Za-z_.$][\w.$]*):\s*(.*)$", line)
                if not m:
                    break
                a.label(m.group(1))
                line = m.group(2)
            if not line:
                continue
            op, _, rest = line.partition(" ")
            args = [x for x in re.split(r"[,\s]+", rest.strip()) if x] if op != ".string" else None
            if op.startswith("."):
                d = op[1:]
                if d == "cell":
                    a.cell(args[0], _tok(args[1]) if len(args) > 1 else 0)
                elif d == "bss":
                    a.bss(args[0], int(args[1]) if len(args) > 1 else 1)
                elif d == "array":
                    a.array(args[0], [_tok(x) for x in args[1:]])
                elif d == "string":
                    nm, _, txt = rest.strip().partition(" ")
                    a.string(nm, txt)
                elif d == "entry":
                    a.entry = args[0]
                elif d == "ins":
                    a.ins(*[_tok(x) for x in args])
                else:
                    raise AsmError("unknown directive " + op)
            else:
                f = op.lower()
                if f not in _MACROS:
                    raise AsmError("unknown macro " + op)
                getattr(a, f)(*[_tok(x) for x in args])
        except (AsmError, AssertionError, TypeError, IndexError) as e:
            raise AsmError("line %d: %s: %s" % (ln, line, e))
    return a


def load_source(path):
    if path.endswith(".py"):
        import runpy
        g = runpy.run_path(path)
        if "build" in g:
            r = g["build"]()
            return r if isinstance(r, Asm) else g.get("asm", r)
        if "asm" in g:
            return g["asm"]
        raise AsmError("%s defines neither build() nor asm" % path)
    with open(path) as f:
        return assemble_text(f.read())


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="assemble a .sqa text file or a python source (build()/asm)")
    ap.add_argument("src")
    ap.add_argument("-o", "--out")
    ap.add_argument("--map", help="write symbol/patch map JSON (for sqdis / visualiser)")
    ap.add_argument("--list", action="store_true", help="print annotated listing to stderr")
    args = ap.parse_args(argv)
    asm = load_source(args.src)
    prog = asm.assemble()
    if args.out:
        with open(args.out, "w") as f:
            f.write(prog.text())
    else:
        sys.stdout.write(prog.text())
    if args.map:
        with open(args.map, "w") as f:
            f.write(prog.map_json())
    if args.list:
        sys.stderr.write(prog.listing_text())
    sys.stderr.write("%d words (%d instructions)\n" % (len(prog.words), (prog.code_end - prog.code_start) // 3))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except AsmError as e:
        sys.exit("sqasm: %s" % e)
