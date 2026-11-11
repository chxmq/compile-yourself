#!/usr/bin/env python3
"""Seedable random generator of valid, terminating v1 programs (+ matching stdin).

usage: gen_v1.py --seed N [--size S] [-o prog.v1] [--input prog.in]
       (without -o the program goes to stdout and the input is not written)

Guarantees by construction (so the program never trips undefined behaviour):
 * every loop is a counter loop (up / down / down-while-truthy) whose counter is never
   assigned in the body, trip counts are bounded, and an estimated cost budget limits
   total work (calls inside loops are charged per iteration)
 * array indices are call-free and built only from locals: constants, loop counters whose
   proven range fits the array, sums of comparison results, counter*comparison, ...
   (v1 has no % or &, so ranges are proven via counters and 0/1 comparison values).
   Index expressions never read globals/arrays/calls, so index-vs-value evaluation order
   of `a[i] = v` cannot matter.
 * calls only go to earlier functions (or self-recursion with a decreasing counter);
   max call chain depth is tracked and kept <= 900 (<1000 limit)
 * locals are all declared at the top of a function and initialised before use
 * 0..8 parameters, wraparound arithmetic with boundary literals, getc/putc, early returns,
   missing returns, calls as statements, left-to-right dependent side effects through globals
"""
import argparse, random, sys

LIB = r'''array pw[10];
func initpw() {
    var i;
    pw[0] = 1;
    i = 1;
    while (i < 10) { pw[i] = pw[i - 1] * 10; i = i + 1; }
    return 0;
}
func pnum(n) {
    var neg; var i; var d; var started;
    neg = 0;
    if (n < 0) { neg = 1; putc(45); }
    started = 0;
    i = 9;
    while (i >= 0) {
        d = 0;
        if (neg) {
            while (n <= 0 - pw[i]) { n = n + pw[i]; d = d + 1; }
        } else {
            while (n >= pw[i]) { n = n - pw[i]; d = d + 1; }
        }
        if (started + d + (i == 0)) { putc(48 + d); started = 1; }
        i = i - 1;
    }
    return 0;
}
func pl(n) { pnum(n); putc(10); return 0; }
'''
PNUM_COST = 250
LITS = [0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 16, 31, 100, 127, 128, 255, 256, 1000, 4095, 32767, 32768,
        46340, 46341, 65535, 65536, 65537, 1000000, 1103515245, 1073741823, 1073741824,
        2000000000, 2147483646, 2147483647]
CMPS = ["<", "<=", ">", ">=", "==", "!="]
DEPTH_CAP = 900


class Fn:
    def __init__(self, name, params, kind):
        self.name = name
        self.params = params       # list of names
        self.kind = kind           # 'plain' | 'lin' | 'bin'
        self.cost = 1              # per-call cost (rec: per-level cost)
        self.maxcall = 0           # max depth of callees reached from body
        self.nmax = 0              # recursion: largest safe n


class Gen:
    def __init__(self, seed, size):
        self.r = random.Random(seed)
        self.size = max(1, size)
        self.fns = []
        self.gscal = ["g%d" % i for i in range(self.r.randint(2, 4 + self.size // 3))]
        self.arrays = {}
        for i in range(self.r.randint(1, 3 + self.size // 4)):
            self.arrays["ar%d" % i] = self.r.choice([4, 5, 8, 10, 16, 20, 33, 50])
        self.inlen = self.r.randint(0, 80)
        self.indata = bytes(self.r.choice([self.r.randrange(256), self.r.randrange(32, 127)]) for _ in range(self.inlen))

    # ------------------------------------------------------------ expressions
    # nodes: ('lit',n) ('var',n) ('arr',name,idx) ('call',fn,args) ('neg',x) ('bin',op,l,r)
    #        ('getc',) ('par',x)
    def lit(self):
        r = self.r
        v = r.choice(LITS) if r.random() < 0.6 else r.randrange(0, 2000)
        return ("lit", v)

    def pure(self, depth, vars_):
        """call-free, global-free expression over locals/literals (used inside indices)."""
        r = self.r
        if depth <= 0 or r.random() < 0.3:
            if vars_ and r.random() < 0.6:
                return ("var", r.choice(vars_))
            return self.lit()
        k = r.random()
        if k < 0.15:
            return ("neg", self.pure(depth - 1, vars_))
        op = r.choice(["+", "-", "*"] * 3 + CMPS)
        return ("bin", op, self.pure(depth - 1, vars_), self.pure(depth - 1, vars_))

    def cmpnode(self, vars_):
        return ("bin", self.r.choice(CMPS), self.pure(2, vars_), self.pure(2, vars_))

    def index(self, size, ctx):
        """returns an expression node guaranteed to lie in [0, size)."""
        r = self.r
        opts = ["const", "cmp"]
        good = [(v, hi) for v, hi in ctx["counters"] if hi < size]
        if good:
            opts += ["counter"] * 4 + ["cmpcounter", "cntplus"]
        k = r.choice(opts)
        pv = ctx["pvars"]
        if k == "const":
            return ("lit", r.randrange(size))
        if k == "cmp":
            n = r.randint(1, min(4, size - 1))
            e = self.cmpnode(pv)
            for _ in range(n - 1):
                e = ("bin", "+", e, self.cmpnode(pv))
            if r.random() < 0.3:
                e = ("bin", "*", e, ("lit", r.randint(0, (size - 1) // n)))
            return e
        v, hi = r.choice(good)
        if k == "counter":
            return ("var", v)
        if k == "cmpcounter":
            return ("bin", "*", ("var", v), self.cmpnode(pv))
        # cntplus: counter + cmp (hi + 1 < size)
        if hi + 1 < size:
            return ("bin", "+", ("var", v), self.cmpnode(pv))
        return ("var", v)

    def expr(self, depth, ctx, allow_calls=True):
        r = self.r
        pv = ctx["vars"]
        if depth <= 0 or r.random() < 0.2:
            c = r.random()
            if c < 0.45 and pv:
                return ("var", r.choice(pv))
            if c < 0.6 and self.gscal:
                return ("var", r.choice(self.gscal))
            if c < 0.7:
                return self.arrread(ctx)
            if c < 0.74 and ctx["mult"] * 3 + ctx["cost"] < ctx["budget"]:
                ctx["cost"] += ctx["mult"] * 2
                return ("getc",)
            return self.lit()
        k = r.random()
        if k < 0.10:
            return ("neg", self.expr(depth - 1, ctx, allow_calls))
        if k < 0.17:
            return self.arrread(ctx, depth - 1)
        if k < 0.26 and allow_calls:
            c = self.callnode(depth, ctx)
            if c is not None:
                return c
        if k < 0.31:
            return ("par", self.expr(depth - 1, ctx, allow_calls))
        op = r.choice(["+", "-", "*"] * 4 + CMPS)
        ctx["cost"] += ctx["mult"]
        return ("bin", op, self.expr(depth - 1, ctx, allow_calls), self.expr(depth - 1, ctx, allow_calls))

    def arrread(self, ctx, depth=0):
        name = self.r.choice(sorted(self.arrays))
        ctx["cost"] += ctx["mult"] * 2
        return ("arr", name, self.index(self.arrays[name], ctx))

    def callnode(self, depth, ctx):
        """call to an earlier function, respecting budget and depth caps; None if impossible."""
        r = self.r
        cands = [f for f in self.fns if f is not ctx.get("self")]
        if not cands:
            return None
        f = r.choice(cands)
        mult = ctx["mult"]
        args = []
        if f.kind == "plain":
            cost = f.cost
            dep = 1 + f.maxcall
        else:
            nmax = min(f.nmax, ctx["recmax"])
            if nmax < 0:
                return None
            # pick n so cost fits
            n = r.choice([0, 1, 2, nmax, r.randint(0, nmax)])
            n = min(n, nmax)
            while n > 0 and mult * self.reccost(f, n) + ctx["cost"] > ctx["budget"]:
                n //= 2
            cost = self.reccost(f, n)
            dep = n + 1 + f.maxcall
            args.append(("lit", n))
        if mult * cost + ctx["cost"] > ctx["budget"] or dep > DEPTH_CAP - ctx["basedepth"]:
            return None
        ctx["cost"] += mult * cost
        ctx["maxcall"] = max(ctx["maxcall"], dep)
        for _ in range(len(f.params) - len(args)):
            args.append(self.expr(max(0, depth - 2), ctx))
        return ("call", f.name, args)

    @staticmethod
    def reccost(f, n):
        return f.cost * (n + 1) if f.kind == "lin" else f.cost * (2 ** (n + 1))

    # ------------------------------------------------------------ printing
    PREC = {"*": 3, "+": 2, "-": 2, "<": 1, "<=": 1, ">": 1, ">=": 1, "==": 1, "!=": 1}

    def show(self, n, minprec=0):
        k = n[0]
        r = self.r
        if k == "lit":
            return str(n[1])
        if k == "var":
            return n[1]
        if k == "getc":
            return "getc()"
        if k == "arr":
            return "%s[%s]" % (n[1], self.show(n[2]))
        if k == "call":
            return "%s(%s)" % (n[1], ", ".join(self.show(a) for a in n[2]))
        if k == "par":
            return "(" + self.show(n[1]) + ")"
        if k == "neg":
            inner = self.show(n[1], 4)
            if inner.startswith("-"):
                inner = " " + inner
            return "-" + inner
        _, op, l, rr = n
        p = self.PREC[op]
        s = "%s %s %s" % (self.show(l, p), op, self.show(rr, p + 1))
        if p < minprec or (r.random() < 0.08 and minprec > 0):
            s = "(" + s + ")"
        return s

    # ------------------------------------------------------------ statements
    def stmts(self, ctx, depth, n):
        out = []
        for _ in range(n):
            if ctx["cost"] >= ctx["budget"]:
                break
            out.extend(self.stmt(ctx, depth))
        return out

    def ind(self, lines):
        return ["    " + l for l in lines]

    def stmt(self, ctx, depth):
        r = self.r
        m = ctx["mult"]
        ctx["cost"] += m * 2
        k = r.random()
        wr = ctx["writable"]
        E = lambda d=3: self.show(self.expr(d, ctx))
        if k < 0.22 and wr:
            return ["%s = %s;" % (r.choice(wr), E())]
        if k < 0.34 and self.gscal:
            return ["%s = %s;" % (r.choice(self.gscal), E())]
        if k < 0.46:
            name = r.choice(sorted(self.arrays))
            ix = self.show(self.index(self.arrays[name], ctx))
            return ["%s[%s] = %s;" % (name, ix, E())]
        if k < 0.54:
            ctx["cost"] += m
            return ["putc(%s);" % E(2)]
        if k < 0.60 and m * PNUM_COST + ctx["cost"] < ctx["budget"] and ctx["pl_ok"]:
            ctx["cost"] += m * PNUM_COST
            ctx["maxcall"] = max(ctx["maxcall"], 2)
            return ["pl(%s);" % E(2)]
        if k < 0.68:
            c = self.callnode(3, ctx)
            if c is not None:
                return [self.show(c) + ";"]
            return ["%s = %s;" % (r.choice(wr), E())] if wr else ["putc(%s);" % E(1)]
        if k < 0.80 and depth < 4:
            cond = self.show(self.expr(2, ctx))
            a = self.ind(self.stmts(ctx, depth + 1, r.randint(1, 3)) or ["putc(1);"])
            if r.random() < 0.5:
                b = self.ind(self.stmts(ctx, depth + 1, r.randint(1, 3)) or ["putc(2);"])
                return ["if (%s) {" % cond] + a + ["} else {"] + b + ["}"]
            return ["if (%s) {" % cond] + a + ["}"]
        if k < 0.88 and ctx.get("canreturn") and depth > 0:
            cond = self.show(self.expr(2, ctx))
            return ["if (%s) { return %s; }" % (cond, E(2))]
        if depth < 3 and ctx["counters_free"]:
            return self.loop(ctx, depth)
        return ["%s = %s;" % (r.choice(wr), E())] if wr else ["putc(3);"]

    def loop(self, ctx, depth):
        r = self.r
        cv = ctx["counters_free"].pop()
        trips = r.choice([1, 2, 3, 5, 8, 10, 16, 20, 40])
        if ctx["mult"] * trips * 3 + ctx["cost"] > ctx["budget"]:
            trips = max(1, trips // 4)
        style = r.choice(["up", "down", "downtruth"])
        ctx["mult"] *= trips
        saved_vars = list(ctx["vars"])
        ctx["vars"].append(cv)
        hi = trips - 1 if style == "up" else trips
        ctx["counters"].append((cv, hi))
        ctx["pvars"].append(cv)
        body = self.stmts(ctx, depth + 1, r.randint(1, 4))
        ctx["pvars"].pop()
        ctx["counters"].pop()
        ctx["vars"][:] = saved_vars
        ctx["mult"] //= trips
        ctx["counters_free"].append(cv)
        ctx["cost"] += ctx["mult"] * 2
        if style == "up":
            head = ["%s = 0;" % cv, "while (%s < %d) {" % (cv, trips)]
            tail = ["    %s = %s + 1;" % (cv, cv), "}"]
        elif style == "down":
            head = ["%s = %d;" % (cv, trips), "while (%s > 0) {" % cv]
            tail = ["    %s = %s - 1;" % (cv, cv), "}"]
        else:
            head = ["%s = %d;" % (cv, trips), "while (%s) {" % cv]
            tail = ["    %s = %s - 1;" % (cv, cv), "}"]
        return head + self.ind(body) + tail

    # ------------------------------------------------------------ functions
    def newctx(self, params, budget, recmax, basedepth, selff=None, counters=3):
        locs = ["v%d" % i for i in range(self.r.randint(1, 5))]
        cnts = ["c%d" % i for i in range(counters)]
        return {
            "vars": list(params) + locs, "pvars": list(params) + locs, "writable": list(params) + locs,
            "locals": locs, "counters": [], "counters_free": cnts, "cnames": cnts,
            "mult": 1, "cost": 0, "budget": budget, "maxcall": 0, "recmax": recmax,
            "basedepth": basedepth, "self": selff, "pl_ok": True,
        }

    def decls(self, ctx, params):
        lines = []
        names = ctx["locals"] + ctx["cnames"]
        for n in names:
            lines.append("var %s;" % n)
        # counters are initialised by their loops; init real locals now
        for n in ctx["locals"]:
            saved = ctx["vars"], ctx["pvars"]
            avail = list(params) + ctx["locals"][:ctx["locals"].index(n)]
            ctx["vars"] = list(avail)
            ctx["pvars"] = list(avail)   # indices must not read uninitialised locals either
            lines.append("%s = %s;" % (n, self.show(self.expr(2, ctx))))
            ctx["vars"], ctx["pvars"] = saved
        # counters hold garbage until their loop: never read outside loops.  but vars list
        # must not include them before loops.
        return lines

    def make_plain(self, idx):
        r = self.r
        k = r.choice([0, 1, 2, 3, 4, 5, 6, 8])
        params = ["p%d" % i for i in range(k)]
        f = Fn("f%d" % idx, params, "plain")
        ctx = self.newctx(params, r.choice([60, 150, 400, 1500]), 20, 0, f)
        ctx["canreturn"] = True
        body = self.decls(ctx, params) + self.stmts(ctx, 0, r.randint(2, 4 + self.size // 3))
        if r.random() < 0.85:
            body.append("return %s;" % self.show(self.expr(3, ctx)))
        f.cost = ctx["cost"] + 3
        f.maxcall = ctx["maxcall"]
        self.fns.append(f)
        return self.render(f, body)

    def make_rec(self, idx, kind):
        r = self.r
        k = r.choice([1, 2, 3, 4, 7])
        params = ["n"] + ["p%d" % i for i in range(k)]
        f = Fn("f%d" % idx, params, kind)
        ctx = self.newctx(params, 80 if kind == "bin" else 120, 20, 0, f, counters=2)
        # base case first (can only use params, no self call)
        ctx["pvars"] = [p for p in params if p != "n"] + ctx["locals"]
        ctx["writable"] = [p for p in params if p != "n"] + ctx["locals"]
        base = ["if (n < 1) { return %s; }" % self.show(self.expr(2, ctx))]
        ctx["canreturn"] = False
        mid = self.stmts(ctx, 1, r.randint(0, 3))
        # recursive return expression with exactly 1 (lin) or 2 (bin) self-calls
        def selfcall():
            args = [("bin", "-", ("var", "n"), ("lit", 1))]
            for _ in range(k):
                args.append(self.expr(2, ctx))
            ctx["cost"] += ctx["mult"]
            return ("call", f.name, args)
        calls = [selfcall() for _ in range(1 if kind == "lin" else 2)]
        e = calls[0]
        if kind == "bin":
            e = ("bin", r.choice(["+", "-", "*"]), calls[0], calls[1])
        e = ("bin", r.choice(["+", "-", "*"]), self.expr(2, ctx), e)
        if r.random() < 0.5:
            e = ("bin", r.choice(["+", "-"]), e, self.expr(1, ctx))
        # NOTE: argument evaluation order inside the self call args may call other functions: fine
        body = self.decls(ctx, params) + base + mid + ["return %s;" % self.show(e)]
        f.cost = ctx["cost"] + 6
        f.maxcall = ctx["maxcall"]
        f.nmax = (DEPTH_CAP - 1 - f.maxcall) if kind == "lin" else min(10, DEPTH_CAP - 1 - f.maxcall)
        if kind == "lin":
            f.nmax = min(f.nmax, 850)
        self.fns.append(f)
        return self.render(f, body)

    def render(self, f, body):
        return ["func %s(%s) {" % (f.name, ", ".join(f.params))] + self.ind(body) + ["}", ""]

    def make_main(self):
        r = self.r
        f = Fn("main", [], "plain")
        ctx = self.newctx([], 250000, 900, 1, None, counters=3)
        ctx["pl_ok"] = True
        ctx["canreturn"] = False
        body = self.decls(ctx, [])
        # optional input loop
        if r.random() < 0.7:
            cvar = "inp"
            body.append("var %s;" % cvar) if False else None
            ctx["locals"].append("cin")
        lines = []
        usecin = "cin" in ctx["locals"]
        if usecin:
            body.insert(0, "var cin;")
            ctx["vars"].append("cin"); ctx["pvars"].append("cin")
            ctx["writable"].remove("cin") if "cin" in ctx["writable"] else None
            lines.append("cin = getc();")
            ctx["mult"] = max(1, self.inlen)
            inner = self.stmts(ctx, 1, r.randint(1, 4))
            ctx["mult"] = 1
            lines.append("while (cin != -1) {")
            lines += self.ind(inner + ["cin = getc();"])
            lines.append("}")
        body += lines
        body += self.stmts(ctx, 0, r.randint(3, 6 + self.size))
        # final dump
        for g in self.gscal:
            body.append("pl(%s);" % g)
        for a, sz in sorted(self.arrays.items()):
            body.append("cin_sum_%s = 0;" % a if False else "")
        dump = []
        dump.append("v_dump = 0;")
        for a, sz in sorted(self.arrays.items()):
            dump += ["v_dump = 0; v_i = 0;", "while (v_i < %d) { v_dump = v_dump * 31 + %s[v_i]; v_i = v_i + 1; }" % (sz, a), "pl(v_dump);"]
        body = [l for l in body if l != ""]
        body.insert(0, "var v_dump; var v_i;")
        last_var = max(i for i, l in enumerate(body) if l.startswith("var "))
        body.insert(last_var + 1, "initpw();")
        body += dump
        return ["func main() {"] + self.ind(body) + ["}", ""]

    def program(self):
        r = self.r
        out = ["// generated by gen_v1.py", LIB]
        for g in self.gscal:
            out.append("var %s;" % g)
        for a, sz in sorted(self.arrays.items()):
            out.append("array %s[%d];" % (a, sz))
        out.append("")
        nf = min(40, 2 + self.size)
        for i in range(nf):
            kind = r.random()
            if i >= 1 and kind < 0.15:
                out += self.make_rec(i, "lin")
            elif i >= 1 and kind < 0.25:
                out += self.make_rec(i, "bin")
            else:
                out += self.make_plain(i)
        out += self.make_main()
        return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--size", type=int, default=10)
    ap.add_argument("-o", "--out")
    ap.add_argument("--input", help="path for generated stdin file (default: OUT with .in)")
    a = ap.parse_args()
    g = Gen(a.seed, a.size)
    src = g.program()
    if a.out:
        with open(a.out, "w") as f:
            f.write(src)
        inp = a.input or (a.out[:-3] + ".in" if a.out.endswith(".v1") else a.out + ".in")
        with open(inp, "wb") as f:
            f.write(g.indata)
    else:
        sys.stdout.write(src)
        if a.input:
            with open(a.input, "wb") as f:
                f.write(g.indata)


if __name__ == "__main__":
    main()
