#!/usr/bin/env python3
"""Strict reference interpreter for language v1 (oracle for compiler tests).

usage: v1interp.py [--max-steps N] [--max-depth N] [--count-main] [--dump-py] prog.v1 < in > out
exit codes: 0 ok, 2 compile error (invalid program), 3 runtime error
(undefined behaviour detected: array index out of range, recursion too deep,
step limit exceeded).  Output produced before a runtime error is still written.

Implementation: parse + semantic check, then translate to Python source and exec.
Arithmetic is kept modulo 2^32 and normalised to signed at observation points.
Evaluation order: left to right everywhere; for `a[i] = v` the index is
evaluated before the value (the spec does not pin this down; avoid depending on it).
"""
import sys, re, argparse, os

KEYWORDS = {"var", "array", "func", "if", "else", "while", "return"}
BUILTINS = {"getc": 0, "putc": 1}
MAX_SRC = 60000
MAX_FUNCS = 200
MAX_NAME = 24
MAX_PARAMS = 8
INT_MAX = 2147483647


class CompileError(Exception):
    pass


class RuntimeErr(Exception):
    pass


TOKEN_RE = re.compile(r"""
    (?P<ws>[ \t\r\n]+)
  | (?P<comment>//[^\n]*)
  | (?P<num>[0-9]+)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><=|>=|==|!=|[<>+\-*=(){}\[\];,])
""", re.X)


def lex(src):
    toks = []
    pos = 0
    line = 1
    n = len(src)
    while pos < n:
        m = TOKEN_RE.match(src, pos)
        if not m:
            raise CompileError("line %d: unexpected character %r" % (line, src[pos]))
        kind = m.lastgroup
        text = m.group()
        if kind == "num":
            if m.end() < n and (src[m.end()].isalnum() or src[m.end()] == "_"):
                raise CompileError("line %d: malformed number/name near %r" % (line, text))
            if int(text) > INT_MAX:
                raise CompileError("line %d: literal %s exceeds 2147483647" % (line, text))
            toks.append(("num", int(text), line))
        elif kind == "name":
            if len(text) > MAX_NAME:
                raise CompileError("line %d: name %r longer than %d chars" % (line, text, MAX_NAME))
            toks.append(("kw" if text in KEYWORDS else "name", text, line))
        elif kind == "op":
            toks.append(("op", text, line))
        line += text.count("\n")
        pos = m.end()
    toks.append(("eof", "", line))
    return toks


class Parser:
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    def peek(self):
        return self.t[self.i]

    def err(self, msg):
        k, v, l = self.peek()
        raise CompileError("line %d: %s (at %r)" % (l, msg, v if k != "eof" else "end of file"))

    def isop(self, v):
        k, x, _ = self.peek()
        return k == "op" and x == v

    def iskw(self, v):
        k, x, _ = self.peek()
        return k == "kw" and x == v

    def eatop(self, v):
        if not self.isop(v):
            self.err("expected %r" % v)
        self.i += 1

    def eatkw(self, v):
        if not self.iskw(v):
            self.err("expected %r" % v)
        self.i += 1

    def name(self):
        k, v, l = self.peek()
        if k != "name":
            self.err("expected identifier")
        self.i += 1
        return v, l

    # ---- top level
    def program(self):
        decls = []
        while self.peek()[0] != "eof":
            k, v, l = self.peek()
            if k == "kw" and v == "var":
                self.i += 1
                n, ln = self.name()
                self.eatop(";")
                decls.append(("var", n, ln))
            elif k == "kw" and v == "array":
                self.i += 1
                n, ln = self.name()
                self.eatop("[")
                k2, sz, l2 = self.peek()
                if k2 != "num":
                    self.err("array size must be a decimal literal")
                if sz <= 0:
                    self.err("array size must be positive")
                self.i += 1
                self.eatop("]")
                self.eatop(";")
                decls.append(("array", n, sz, ln))
            elif k == "kw" and v == "func":
                self.i += 1
                n, ln = self.name()
                self.eatop("(")
                params = []
                if not self.isop(")"):
                    while True:
                        params.append(self.name())
                        if self.isop(","):
                            self.i += 1
                        else:
                            break
                self.eatop(")")
                body = self.block()
                decls.append(("func", n, params, body, ln))
            else:
                self.err("expected top-level declaration")
        return decls

    def block(self):
        self.eatop("{")
        stmts = []
        while not self.isop("}"):
            if self.peek()[0] == "eof":
                self.err("unexpected end of file in block")
            stmts.append(self.stmt())
        self.eatop("}")
        return stmts

    def stmt(self):
        k, v, l = self.peek()
        if k == "kw":
            if v == "var":
                self.i += 1
                n, ln = self.name()
                self.eatop(";")
                return ("local", n, ln)
            if v == "if":
                self.i += 1
                self.eatop("(")
                c = self.expr()
                self.eatop(")")
                a = self.block()
                b = None
                if self.iskw("else"):
                    self.i += 1
                    if self.iskw("if"):
                        self.err("'else if' is not allowed; use else { if ... }")
                    b = self.block()
                return ("if", c, a, b, l)
            if v == "while":
                self.i += 1
                self.eatop("(")
                c = self.expr()
                self.eatop(")")
                return ("while", c, self.block(), l)
            if v == "return":
                self.i += 1
                if self.isop(";"):
                    self.err("bare 'return;' is not allowed")
                e = self.expr()
                self.eatop(";")
                return ("return", e, l)
            self.err("unexpected keyword")
        if k == "name":
            self.i += 1
            if self.isop("="):
                self.i += 1
                e = self.expr()
                self.eatop(";")
                return ("assign", v, e, l)
            if self.isop("["):
                self.i += 1
                ix = self.expr()
                self.eatop("]")
                self.eatop("=")
                e = self.expr()
                self.eatop(";")
                return ("aassign", v, ix, e, l)
            if self.isop("("):
                args = self.args()
                self.eatop(";")
                return ("callstmt", v, args, l)
            self.err("expected '=', '[' or '(' after identifier")
        self.err("expected statement")

    def args(self):
        self.eatop("(")
        args = []
        if not self.isop(")"):
            while True:
                args.append(self.expr())
                if self.isop(","):
                    self.i += 1
                else:
                    break
        self.eatop(")")
        return args

    # ---- expressions
    def expr(self):
        left = self.additive()
        while self.peek()[0] == "op" and self.peek()[1] in ("<", "<=", ">", ">=", "==", "!="):
            op = self.peek()[1]
            l = self.peek()[2]
            self.i += 1
            left = ("bin", op, left, self.additive(), l)
        return left

    def additive(self):
        left = self.term()
        while self.peek()[0] == "op" and self.peek()[1] in ("+", "-"):
            op = self.peek()[1]
            l = self.peek()[2]
            self.i += 1
            left = ("bin", op, left, self.term(), l)
        return left

    def term(self):
        left = self.unary()
        while self.isop("*"):
            l = self.peek()[2]
            self.i += 1
            left = ("bin", "*", left, self.unary(), l)
        return left

    def unary(self):
        if self.isop("-"):
            l = self.peek()[2]
            self.i += 1
            return ("neg", self.unary(), l)
        return self.primary()

    def primary(self):
        k, v, l = self.peek()
        if k == "num":
            self.i += 1
            return ("num", v)
        if k == "op" and v == "(":
            self.i += 1
            e = self.expr()
            self.eatop(")")
            return e
        if k == "name":
            self.i += 1
            if self.isop("("):
                return ("call", v, self.args(), l)
            if self.isop("["):
                self.i += 1
                ix = self.expr()
                self.eatop("]")
                return ("idx", v, ix, l)
            return ("var", v, l)
        self.err("expected expression")


WRAP = "((%s+2147483648)&4294967295)-2147483648"


class Gen:
    def __init__(self, steps_enabled):
        self.funcs = {}      # name -> nparams (defined so far)
        self.gscalars = set()
        self.garrays = {}
        self.gnames = set()  # all global names
        self.steps = steps_enabled
        self.out = []
        self.nfuncs = 0

    def cerr(self, line, msg):
        raise CompileError("line %d: %s" % (line, msg))

    def declare_global(self, name, line):
        if name in self.gnames or name in BUILTINS:
            self.cerr(line, "redefinition of global name %r" % name)
        self.gnames.add(name)

    def run(self, decls):
        for d in decls:
            if d[0] == "var":
                self.declare_global(d[1], d[2])
                self.gscalars.add(d[1])
            elif d[0] == "array":
                self.declare_global(d[1], d[3])
                self.garrays[d[1]] = d[2]
            else:
                self.func(d)
        if "main" not in self.funcs:
            raise CompileError("no main function")
        if self.funcs["main"] != 0:
            raise CompileError("main must take no parameters")

    def func(self, d):
        _, name, params, body, line = d
        self.declare_global(name, line)
        self.nfuncs += 1
        if self.nfuncs > MAX_FUNCS:
            self.cerr(line, "more than %d functions" % MAX_FUNCS)
        if len(params) > MAX_PARAMS:
            self.cerr(line, "more than %d parameters" % MAX_PARAMS)
        self.funcs[name] = len(params)  # visible inside its own body
        self.locals = set()
        self.assigned_globals = set()
        self.used_globals = set()
        self.fname = name
        plist = []
        for p, pl in params:
            if p in self.locals:
                self.cerr(pl, "duplicate parameter %r" % p)
            if p in self.funcs:
                self.cerr(pl, "parameter %r conflicts with a function name" % p)
            self.locals.add(p)
            plist.append("l_" + p)
        lines = []
        self.body(body, lines, 1)
        lines.append("    _D[0] -= 1")
        lines.append("    return 0")
        hdr = ["def f_%s(%s):" % (name, ", ".join(plist))]
        decl = []
        if self.used_globals:
            decl.append("    global " + ", ".join(sorted("g_" + g for g in self.used_globals)))
        decl.append("    _D[0] += 1")
        decl.append("    if _D[0] > _LIM: _depth()")
        if self.steps:
            decl.append("    _S[0] += 1")
            decl.append("    if _S[0] > _SLIM: _steps()")
        for l in sorted(self.locals):
            if l not in [p for p, _ in params]:
                decl.append("    l_%s = 0" % l)
        self.out.extend(hdr + decl + lines)

    # statements --------------------------------------------------
    def body(self, stmts, out, ind):
        n0 = len(out)
        for s in stmts:
            self.stmt(s, out, ind)
        if len(out) == n0:
            out.append("    " * ind + "pass")

    def stmt(self, s, out, ind):
        pad = "    " * ind
        k = s[0]
        if k == "local":
            _, n, line = s
            if n in self.locals:
                self.cerr(line, "redeclaration of local %r" % n)
            if n in self.funcs:
                self.cerr(line, "local %r conflicts with a function name" % n)
            self.locals.add(n)
            out.append(pad + "l_%s = 0" % n)  # (value undefined in v1)
        elif k == "assign":
            _, n, e, line = s
            tgt = self.scalar_target(n, line)
            out.append(pad + "%s = %s" % (tgt, self.exprs(e)))
        elif k == "aassign":
            _, n, ix, e, line = s
            arr = self.array_ref(n, line)
            out.append(pad + "_i = %s" % self.index(n, ix))
            out.append(pad + "%s[_i] = %s" % (arr, self.exprs(e)))
        elif k == "callstmt":
            _, n, args, line = s
            out.append(pad + self.call(n, args, line))
        elif k == "if":
            _, c, a, b, line = s
            out.append(pad + "if %s:" % self.cond(c))
            self.body(a, out, ind + 1)
            if b is not None:
                out.append(pad + "else:")
                self.body(b, out, ind + 1)
        elif k == "while":
            _, c, b, line = s
            out.append(pad + "while %s:" % self.cond(c))
            if self.steps:
                out.append(pad + "    _S[0] += 1")
                out.append(pad + "    if _S[0] > _SLIM: _steps()")
            self.body(b, out, ind + 1)
        elif k == "return":
            out.append(pad + "_r = %s" % self.exprs(s[1]))
            out.append(pad + "_D[0] -= 1")
            out.append(pad + "return _r")

    def scalar_target(self, n, line):
        if n in self.locals:
            return "l_" + n
        if n in self.gscalars:
            self.used_globals.add(n)
            return "g_" + n
        if n in self.garrays:
            self.cerr(line, "array %r used without index" % n)
        if n in self.funcs:
            self.cerr(line, "%r is a function, not a variable" % n)
        self.cerr(line, "undeclared variable %r" % n)

    def array_ref(self, n, line):
        if n in self.locals:
            self.cerr(line, "%r is a scalar, cannot be indexed" % n)
        if n in self.garrays:
            return "a_" + n
        if n in self.gscalars:
            self.cerr(line, "%r is a scalar, cannot be indexed" % n)
        self.cerr(line, "undeclared array %r" % n)

    def index(self, n, ix):
        size = self.garrays[n]
        return "(_t if 0 <= (_t := %s) < %d else _oob(_t, %r, %d))" % (self.exprs(ix), size, n, size)

    def call(self, n, args, line):
        if n in BUILTINS:
            if len(args) != BUILTINS[n]:
                self.cerr(line, "%s takes %d argument(s)" % (n, BUILTINS[n]))
            if n == "getc":
                return "_getc()"
            return "_putc(%s)" % self.exprs(args[0])
        if n in self.locals:
            self.cerr(line, "%r is a variable, not a function" % n)
        if n not in self.funcs:
            if n in self.gnames:
                self.cerr(line, "%r is not a function" % n)
            self.cerr(line, "call to undefined (or not yet defined) function %r" % n)
        if len(args) != self.funcs[n]:
            self.cerr(line, "function %r expects %d argument(s), got %d" % (n, self.funcs[n], len(args)))
        return "f_%s(%s)" % (n, ", ".join(self.exprs(a) for a in args))

    # expressions.  raw() returns (code, prec, needs_wrap).  prec: 3 atom/unary, 2 '*',
    # 1 '+'/'-', 0 comparison.  needs_wrap: value is only correct modulo 2^32.
    @staticmethod
    def par(code, prec, need):
        return code if prec >= need else "(" + code + ")"

    def expr(self, e):
        code, prec, raw = self.raw(e)
        if raw:
            return "(%s+2147483648&4294967295)-2147483648" % self.par(code, prec, 1), 1
        return code, prec

    def exprp(self, e, need):
        code, prec = self.expr(e)
        return self.par(code, prec, need)

    def exprs(self, e):
        return self.exprp(e, 0)

    def cond(self, e):
        code, prec, raw = self.raw(e)
        if raw:
            return "%s&4294967295" % self.par(code, prec, 1)
        return code

    def raw(self, e):
        k = e[0]
        if k == "num":
            return str(e[1]), 3, False
        if k == "var":
            _, n, line = e
            return self.scalar_target(n, line), 3, False
        if k == "idx":
            _, n, ix, line = e
            return "%s[%s]" % (self.array_ref(n, line), self.index(n, ix)), 3, False
        if k == "call":
            return self.call(e[1], e[2], e[3]), 3, False
        if k == "neg":
            c, p, _ = self.raw(e[1])
            return "-" + self.par(c, p, 3), 3, True
        _, op, l, r, line = e
        if op in ("+", "-", "*"):
            pr = 2 if op == "*" else 1
            a, ap, _ = self.raw(l)
            b, bp, _ = self.raw(r)
            return "%s %s %s" % (self.par(a, ap, pr), op, self.par(b, bp, pr + 1)), pr, True
        return "%s %s %s" % (self.exprp(l, 1), op, self.exprp(r, 1)), 0, False


def compile_v1(src_bytes, steps_enabled=False):
    if len(src_bytes) > MAX_SRC:
        raise CompileError("source larger than %d bytes" % MAX_SRC)
    try:
        src = src_bytes.decode("ascii")
    except UnicodeDecodeError:
        # allow non-ascii only inside comments
        src = src_bytes.decode("latin-1")
    toks = lex(src)
    decls = Parser(toks).program()
    g = Gen(steps_enabled)
    g.run(decls)
    header = []
    for n in sorted(g.gscalars):
        header.append("g_%s = 0" % n)
    for n, sz in sorted(g.garrays.items()):
        header.append("a_%s = [0] * %d" % (n, sz))
    return "\n".join(header + g.out) + "\n"


def run(pysrc, data, max_depth, max_steps):
    out = bytearray()
    pos = [0]
    D = [0]
    S = [0]

    def getc():
        p = pos[0]
        if p >= len(data):
            return -1
        pos[0] = p + 1
        return data[p]

    def putc(v):
        out.append(v & 255)
        return 0

    def depth():
        raise RuntimeErr("recursion deeper than %d calls" % max_depth)

    def steps():
        raise RuntimeErr("step limit %d exceeded" % max_steps)

    def oob(i, name, size):
        raise RuntimeErr("array index %d out of range for %s[%d]" % (i, name, size))

    env = {"_D": D, "_S": S, "_LIM": max_depth, "_SLIM": max_steps, "_depth": depth,
           "_steps": steps, "_oob": oob, "_getc": getc, "_putc": putc}
    err = None
    try:
        try:
            code = compile(pysrc, "<v1>", "exec")
        except (SyntaxError, RecursionError, MemoryError):
            return b"", "INTERPRETER LIMITATION: expression nesting too deep for the python backend"
        exec(code, env)
        env["f_main"]()
    except RuntimeErr as e:
        err = str(e)
    except RecursionError:
        err = "python recursion limit"
    return bytes(out), err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prog")
    ap.add_argument("--max-steps", type=int, default=0, help="limit (loop iterations + calls); 0 = none")
    ap.add_argument("--max-depth", type=int, default=1000)
    ap.add_argument("--count-main", action="store_true", help="count main as a call level (stricter)")
    ap.add_argument("--dump-py", action="store_true")
    ap.add_argument("--check", action="store_true", help="only check validity")
    a = ap.parse_args()
    with open(a.prog, "rb") as f:
        src = f.read()
    sys.setrecursionlimit(20000)
    import threading
    threading.stack_size(512 * 1024 * 1024)
    cres = {}

    def ctarget():
        try:
            cres["py"] = compile_v1(src, a.max_steps > 0)
        except CompileError as e:
            cres["err"] = ("compile error: %s" % e, 2)
        except (RecursionError, MemoryError):
            cres["err"] = ("interpreter limitation: program too deeply nested", 4)

    ct = threading.Thread(target=ctarget)
    ct.start()
    ct.join()
    if "err" in cres:
        sys.stderr.write(cres["err"][0] + "\n")
        return cres["err"][1]
    py = cres["py"]
    if a.dump_py:
        sys.stdout.write(py)
        return 0
    if a.check:
        return 0
    data = sys.stdin.buffer.read()
    # depth counter: main itself is level 1 in _D; lenient mode allows limit+1
    lim = a.max_depth if a.count_main else a.max_depth + 1
    res = {}

    def target():
        res["r"] = run(py, data, lim, a.max_steps)

    t = threading.Thread(target=target)
    t.start()
    t.join()
    out, err = res["r"]
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.flush()
    if err:
        sys.stderr.write("runtime error: %s\n" % err)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
