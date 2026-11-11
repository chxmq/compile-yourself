#!/usr/bin/env python3
"""v1c: compiler from language v1 to the subtract-and-branch-if-negative machine.

    python3 tier2/v1c.py < prog.v1 > prog.txt

Single pass of recursive descent per function that emits straight into a flat word list;
labels are resolved at the end.  The exact scheme is specified in docs/ABI.md, section
"Final layout (v1c codegen)"; tier3/tier3.v1 must reproduce this output byte for byte.
The code is written in a deliberately plain style (global state, flat arrays, no AST) so it
can be ported to v1 itself.  Python niceties (dict lookups of names) never influence the
order of allocation or emission.
"""
import sys

N = 1 << 20
IN_ADDR = N - 2
ZERO, ONE, NEG1 = 3, 4, 5
CODE0 = 6
POOLB = 1 << 29          # operand >= POOLB: literal-pool cell (index = v - POOLB)
LBLB = 1 << 30           # operand >= LBLB: label (index = v - LBLB)
INT_MAX = 2147483647
MAX_SRC, MAX_FUNCS, MAX_NAME, MAX_PARAMS = 60000, 200, 24, 8
KEYWORDS = ("var", "array", "func", "if", "else", "while", "return")


class CompileError(Exception):
    pass


def err(msg):
    raise CompileError("line %d: %s" % (tline[tp] if tp < len(tline) else 0, msg))


# ------------------------------------------------------------------ lexer
# token kinds
K_EOF, K_NUM, K_NAME, K_KW, K_OP = 0, 1, 2, 3, 4
tkind = []
tval = []
tline = []
cz = []
tp = 0
pend = 0         # operators whose right operand is being parsed (the value of the current operand is not final)
want = 0         # cell that should receive the value of the expression being parsed (0: none)
wantp = 0        # value of pend at which `want` applies
stmtcall = False # the call being parsed is a whole statement (its result is unused)


def lex(src):
    i = 0
    n = len(src)
    line = 1
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
        elif "0" <= c <= "9":
            j = i
            v = 0
            while j < n and "0" <= src[j] <= "9":
                v = v * 10 + ord(src[j]) - 48
                if v > INT_MAX:
                    raise CompileError("line %d: literal exceeds 2147483647" % line)
                j += 1
            if j < n and (src[j].isalpha() or src[j] == "_" or "0" <= src[j] <= "9"):
                raise CompileError("line %d: malformed number" % line)
            tkind.append(K_NUM); tval.append(v); tline.append(line)
            i = j
        elif ("a" <= c <= "z") or ("A" <= c <= "Z") or c == "_":
            j = i
            while j < n and (("a" <= src[j] <= "z") or ("A" <= src[j] <= "Z") or src[j] == "_" or ("0" <= src[j] <= "9")):
                j += 1
            name = src[i:j]
            if j - i > MAX_NAME:
                raise CompileError("line %d: name longer than %d characters" % (line, MAX_NAME))
            tkind.append(K_KW if name in KEYWORDS else K_NAME); tval.append(name); tline.append(line)
            i = j
        elif c in "<>=!" and i + 1 < n and src[i + 1] == "=":
            tkind.append(K_OP); tval.append(c + "="); tline.append(line)
            i += 2
        elif c in "<>+-*=(){}[];,":
            tkind.append(K_OP); tval.append(c); tline.append(line)
            i += 1
        else:
            raise CompileError("line %d: unexpected character %r" % (line, c))
    tkind.append(K_EOF); tval.append(""); tline.append(line)
    # cz[i] = 1 when a call "name (" occurs in tokens i .. (next ; { or }) : such an expression can change globals
    flag = 0
    for i in range(len(tkind) - 1, -1, -1):
        if tkind[i] == K_OP and tval[i] in (";", "{", "}"):
            flag = 0
        elif tkind[i] == K_OP and tval[i] == "(" and i > 0 and tkind[i - 1] == K_NAME:
            flag = 1
        cz.append(flag)
    cz.reverse()


def isop(s):
    return tkind[tp] == K_OP and tval[tp] == s


def iskw(s):
    return tkind[tp] == K_KW and tval[tp] == s


def eatop(s):
    global tp
    if not isop(s):
        err("expected %r" % s)
    tp += 1


def eatname():
    global tp
    if tkind[tp] != K_NAME:
        err("expected identifier")
    tp += 1
    return tval[tp - 1]


# ------------------------------------------------------------------ code buffer, cells, labels
code = []        # flat word list; address of code[i] is CODE0 + i
lab = []         # label index -> address (-1 until placed)
pool = []        # literal pool values; pool[0] is the stack pointer cell (initial value set at the end)
dp = N - 3       # next free data cell (cells are allocated downwards)
T = U = T1 = RV = 0
gvarcells = {}   # cell address -> True for global scalars (the only operand cells a call can change)
mul_used = False
MA = MB = MR = MC = MNA = MRA = 0
LMUL = 0
LBOOT = 0

# symbol tables (lookups only, never iterated)
gvars = {}       # name -> cell
garrs = {}       # name -> (base, size)
funcs = {}       # name -> [entry, nparams, [param cells], ra cell]
nfuncs = 0

# per function state
fname = ""
f_ra = 0
f_params = []
f_locals = {}    # name -> cell (params and locals)
f_local_order = []   # cells of params then declared locals, declaration order
tcells = []      # temp index -> cell
tset = {}        # temp cell -> True
depth = 0        # number of temps currently reserved


def pc():
    return CODE0 + len(code)


def emit(a, b, c=None):
    if c is None:
        c = pc() + 3
    code.append(a); code.append(b); code.append(c)


def newcell():
    global dp
    c = dp
    dp -= 1
    return c


def lit(v):
    if v == 0:
        return ZERO
    if v == 1:
        return ONE
    if v == -1:
        return NEG1
    pool.append(v)
    return POOLB + len(pool) - 1


def newlabel():
    lab.append(-1)
    return LBLB + len(lab) - 1


def place(l):
    lab[l - LBLB] = pc()


def tmp(i):
    while len(tcells) <= i:
        c = newcell()
        tcells.append(c)
        tset[c] = True
    return tcells[i]


# ------------------------------------------------------------------ instruction sequences
def jmp(l):
    emit(NEG1, ZERO, l)


def clr(x):
    emit(x, x)


def mov(x, y):
    if x == y:
        return
    emit(x, x); emit(T, T); emit(T, y); emit(x, T)


def setto(x, e):
    """x = e; a literal is loaded as 0 - (-c): 2 steps instead of 4"""
    c = constval(e)
    if c is None:
        mov(x, e)
    else:
        clr(x)
        if c != 0:
            emit(x, lit(-c))


def jnz(d, l):
    """goto l if cell d != 0 (3 steps)"""
    emit(d, ZERO, l); emit(T, T); emit(T, d, l)


def jz(d, l):
    """goto l if cell d == 0 (4 steps)"""
    p = pc()
    emit(d, ZERO, p + 12); emit(T, T); emit(T, d, p + 12); jmp(l)


def diff(t, x, y):
    """t = x - y (5 steps); t must differ from x and y"""
    emit(t, t); emit(T, T); emit(T, x); emit(t, T); emit(t, y)


def lt_jf(x, y, lf):
    """if !(x < y) goto lf, else fall through.  True signed compare."""
    p = pc()
    emit(x, ZERO, p + 27)
    emit(y, ZERO, lf)
    diff(T1, x, y)                 # same sign: no overflow
    emit(T1, ZERO, p + 30)
    jmp(lf)
    emit(y, ZERO, p + 6)           # p+27: both negative -> same
    # p+30: true (x < 0 <= y) falls here


def lt_jt(x, y, lt):
    """if x < y goto lt, else fall through."""
    p = pc()
    emit(x, ZERO, p + 27)
    emit(y, ZERO, p + 33)
    diff(T1, x, y)
    emit(T1, ZERO, lt)
    jmp(p + 33)
    emit(y, ZERO, p + 6)           # p+27
    jmp(lt)                        # p+30: x < 0 <= y
    # p+33: false falls here


def jmpi(cell):
    """goto mem[cell]; patched c word of the last instruction.  5 steps."""
    p = pc()
    w = p + 12 + 2
    emit(T, T); emit(T, cell); emit(w, w); emit(w, T)
    emit(NEG1, ZERO, 0)


def set_ra(ra, retaddr):
    """ra = retaddr (2 steps).  The caller emits the jump right after."""
    emit(ra, ra)
    emit(ra, lit(-retaddr))


def push_cells(cells):
    """push cells onto the software stack (SP = POOLB+0, grows downwards): 2 + 11m + 1 steps."""
    SP = POOLB
    emit(T, T); emit(T, SP)                        # T = -SP
    for ck in cells:
        b = pc()
        w1, w2, w3 = b + 24, b + 25, b + 27
        emit(w1, w1); emit(w1, T)
        emit(w2, w2); emit(w2, T)
        emit(w3, w3); emit(w3, T)
        emit(U, U); emit(U, ck)                    # U = -ck
        emit(0, 0)                                 # [p] = 0   (a, b patched)
        emit(0, U)                                 # [p] -= -ck (a patched)
        emit(T, NEG1)                              # T = -(p-1)
    emit(SP, lit(len(cells)))                      # SP -= m


def pop_cells(cells):
    """inverse of push_cells: 1 + 2 + 7m steps."""
    SP = POOLB
    emit(SP, lit(-len(cells)))                     # SP += m
    emit(T, T); emit(T, SP)
    for ck in cells:
        b = pc()
        w = b + 9 + 1
        emit(w, w); emit(w, T)
        emit(U, U)
        emit(U, 0)                                 # U -= mem[b]  (b patched)
        emit(ck, ck); emit(ck, U)
        emit(T, NEG1)


# ------------------------------------------------------------------ expressions
def is_stable(c):
    """operand cell that a call or a later parameter copy cannot disturb"""
    return c < 6 or c >= POOLB or c in tset


def isfinal():
    """true when the operation just parsed is the last one of an expression whose value goes to `want`"""
    return want != 0 and pend == wantp and tkind[tp] == K_OP and tval[tp] in (";", ",", ")")


def result_cell(d0, l, r, op):
    """cell for the result of an operation on l and r: the destination when final and safe, else temp d0"""
    t = tmp(d0)
    if isfinal():
        if op == "-" and r == want and l != want:
            return t
        return want
    return t


def snapshot_global(c, d0):
    """left operand that is a global scalar must be copied before a later call can change it"""
    if c in gvarcells and cz[tp]:
        t = tmp(d0)
        mov(t, c)
        return t
    return c


def lookup_scalar(name):
    if name in f_locals:
        return f_locals[name]
    if name in gvars:
        return gvars[name]
    if name in garrs:
        err("array %r used without index" % name)
    if name in funcs:
        err("%r is a function, not a variable" % name)
    err("undeclared variable %r" % name)


def lookup_array(name):
    if name in f_locals:
        err("%r is a scalar, cannot be indexed" % name)
    if name in garrs:
        return garrs[name]
    if name in gvars:
        err("%r is a scalar, cannot be indexed" % name)
    err("undeclared array %r" % name)


def primary():
    global tp, depth, pend
    k = tkind[tp]
    v = tval[tp]
    if k == K_NUM:
        tp += 1
        return lit(v)
    if k == K_OP and v == "(":
        tp += 1
        pend += 1
        c = expression()
        pend -= 1
        eatop(")")
        return c
    if k != K_NAME:
        err("expected expression")
    tp += 1
    if isop("("):
        return call(v)
    if isop("["):
        tp += 1
        base, size = lookup_array(v)
        d0 = depth
        pend += 1
        i = expression()
        pend -= 1
        eatop("]")
        depth = d0 + 1
        res = result_cell(d0, 0, 0, "")
        p0 = pc()
        wpatch = p0 + 18 + 1
        emit(T, T); emit(T, i)
        emit(wpatch, wpatch); emit(wpatch, T); emit(wpatch, lit(-base))
        emit(T, T); emit(T, 0)
        emit(res, res); emit(res, T)
        return res
    return lookup_scalar(v)


def unary():
    global tp, depth, pend
    if isop("-"):
        tp += 1
        if tkind[tp] == K_NUM:
            v = tval[tp]
            tp += 1
            return lit(-v)
        d0 = depth
        pend += 1
        e = unary()
        pend -= 1
        if depth == d0:
            depth = d0 + 1
            res = tmp(d0)
            emit(res, res); emit(res, e)           # res = -e
            return res
        emit(T, T); emit(U, U); emit(T, e); emit(U, T)   # in place: e = -e
        emit(e, e); emit(e, U)
        return e
    return primary()


def constval(c):
    """value of a literal operand cell, or None when the cell is not a literal"""
    if c == ZERO:
        return 0
    if c == ONE:
        return 1
    if c == NEG1:
        return -1
    if c >= POOLB:
        return pool[c - POOLB]
    return None


def mul_const(res, x, c):
    """res = x * c for a compile-time constant c: Horner over the bits of |c|, then negate if c < 0"""
    if c == 0:
        clr(res)
        return
    neg = c < 0
    m = (-c) & 0xFFFFFFFF if neg else c
    nb = m.bit_length()
    if m & (m - 1):
        emit(U, U); emit(U, x)                     # U = -x
    if res != x:
        if m & (m - 1):
            emit(res, res); emit(res, U)           # res = x  (U = -x)
        else:
            mov(res, x)
    for bit in range(nb - 2, -1, -1):
        emit(T, T); emit(T, res); emit(res, T)     # res += res
        if (m >> bit) & 1:
            emit(res, U)                           # res += x
    if neg:
        emit(T, T); emit(U, U); emit(T, res); emit(U, T)
        emit(res, res); emit(res, U)               # res = -res


def gen_mul(res, l, r):
    global mul_used, MA, MB, MR, MC, MNA, MRA, LMUL
    cr = constval(r)
    cl = constval(l)
    if cr is not None:
        mul_const(res, l, cr)
        return
    if cl is not None:
        mul_const(res, r, cl)
        return
    if not mul_used:
        mul_used = True
        MA = newcell(); MB = newcell(); MR = newcell(); MC = newcell(); MNA = newcell(); MRA = newcell()
        LMUL = newlabel()
    mov(MA, l)
    mov(MB, r)
    set_ra(MRA, pc() + 6 + 3)
    jmp(LMUL)
    mov(res, MR)


def small_jt(x, limit, lt):
    """if 0 <= x < limit goto lt, else fall through"""
    lno = newlabel()
    emit(x, ZERO, lno)
    diff(T1, x, lit(limit))
    emit(T1, ZERO, lt)
    place(lno)


def emit_mul_routine():
    """MR = MA * MB mod 2^32.
    If either operand is in 0..23 it is the multiplier of a repeated addition (3 steps per unit);
    otherwise MSB-first shift-and-add over the 32 bits of MB (~10 steps per bit)."""
    lswap = newlabel(); lsmall = newlabel(); lfull = newlabel(); lret = newlabel()
    place(LMUL)
    clr(MR)
    small_jt(MB, 24, lsmall)
    small_jt(MA, 24, lswap)
    jmp(lfull)
    place(lswap)
    mov(U, MA); mov(MA, MB); mov(MB, U)
    place(lsmall)
    emit(MNA, MNA); emit(MNA, MA)                  # MNA = -MA
    p = pc()
    emit(MB, ONE, lret)                            # --MB < 0 -> done
    emit(MR, MNA)                                  # MR += MA
    jmp(p)
    place(lfull)
    emit(MNA, MNA); emit(MNA, MA)
    mov(MC, lit(31))
    top = pc()
    emit(T, T); emit(T, MR); emit(MR, T)           # MR += MR
    q = pc()
    emit(MB, ZERO, q + 18)                         # MB < 0 -> add block
    emit(T, T); emit(T, MB); emit(MB, T)           # q+3: MB += MB
    emit(MC, ONE, lret)                            # q+12: --MC < 0 -> done
    jmp(top)                                       # q+15
    emit(MR, MNA)                                  # q+18: MR -= -MA
    jmp(q + 3)                                     # q+21
    place(lret)
    jmpi(MRA)


def add_sub(res, l, r, op):
    """res = l op r.  res never equals r, except for '+' (then l and r are swapped); for '-' res == r is excluded"""
    cl = constval(l)
    cr = constval(r)
    if op == "+":
        if r == res and l != res:
            l, r = r, l
            cl, cr = cr, cl
        if l == res:
            if cr is not None:
                emit(res, lit(-cr))                # res += c
            else:
                emit(T, T); emit(T, r); emit(res, T)
        else:
            if cl is not None and cr is None:
                l, r = r, l
                cl, cr = cr, cl
            if cr is not None:
                emit(res, res); emit(T, T); emit(T, l); emit(res, T); emit(res, lit(-cr))
            else:
                emit(res, res); emit(T, T); emit(T, r); emit(res, T); emit(T, T); emit(T, l); emit(res, T)
    else:
        if l == res:
            emit(res, r)
        elif cl is not None and cr is None:
            emit(res, res); emit(res, r)           # res = -r
            if cl != 0:
                emit(res, lit(-cl))                # res += c
        else:
            emit(res, res); emit(T, T); emit(T, l); emit(res, T); emit(res, r)


def term():
    global tp, depth, pend
    d0 = depth
    l = unary()
    while isop("*"):
        tp += 1
        depth = d0 + 1
        tmp(d0)
        l = snapshot_global(l, d0)
        pend += 1
        r = unary()
        pend -= 1
        res = result_cell(d0, l, r, "*")
        gen_mul(res, l, r)
        depth = d0 + 1
        l = res
    return l


def additive():
    global tp, depth, pend
    d0 = depth
    l = term()
    while isop("+") or isop("-"):
        op = tval[tp]
        tp += 1
        depth = d0 + 1
        tmp(d0)
        l = snapshot_global(l, d0)
        pend += 1
        r = term()
        pend -= 1
        res = result_cell(d0, l, r, op)
        add_sub(res, l, r, op)
        depth = d0 + 1
        l = res
    return l


def iscmp():
    return tkind[tp] == K_OP and tval[tp] in ("<", "<=", ">", ">=", "==", "!=")


def cmp_jf(op, l, r, lf):
    """emit: jump to lf if (l op r) is false, fall through when true"""
    if op == "==" or op == "!=":
        if l == ZERO and r != ZERO:
            l, r = r, l
        if r == ZERO:
            d = l
        else:
            diff(T1, l, r)
            d = T1
        if op == "==":
            jnz(d, lf)
        else:
            jz(d, lf)
        return
    if r == ZERO:
        if op == "<":                              # false if l >= 0
            p = pc()
            emit(l, ZERO, p + 6); jmp(lf)
        elif op == ">=":                           # false if l < 0
            emit(l, ZERO, lf)
        elif op == ">":                            # false if l <= 0
            p = pc()
            emit(l, ZERO, lf); emit(T, T); emit(T, l, p + 12); jmp(lf)
        else:                                      # "<=": false if l > 0
            p = pc()
            emit(l, ZERO, p + 9); emit(T, T); emit(T, l, lf)
        return
    if op == "<":
        lt_jf(l, r, lf)
    elif op == ">=":
        lt_jt(l, r, lf)
    elif op == ">":
        lt_jf(r, l, lf)
    else:
        lt_jt(r, l, lf)


def cmp_value(op, l, r, res):
    lf = newlabel()
    le = newlabel()
    cmp_jf(op, l, r, lf)
    clr(res); emit(res, NEG1); jmp(le)
    place(lf)
    clr(res)
    place(le)


def cmp_tail(l, d0):
    """continue a left-associative chain of comparisons; l is the value so far"""
    global tp, depth, pend
    while iscmp():
        op = tval[tp]
        tp += 1
        depth = d0 + 1
        tmp(d0)
        l = snapshot_global(l, d0)
        pend += 1
        r = additive()
        pend -= 1
        res = tmp(d0)
        cmp_value(op, l, r, res)
        depth = d0 + 1
        l = res
    return l


def expression():
    d0 = depth
    return cmp_tail(additive(), d0)


def cond(lf):
    """parse an expression; jump to lf if it is false (zero), fall through otherwise"""
    global tp, depth
    d0 = depth
    l = additive()
    if not iscmp():
        jz(l, lf)
        depth = d0
        return
    op = tval[tp]
    tp += 1
    depth = d0 + 1
    tmp(d0)
    l = snapshot_global(l, d0)
    r = additive()
    if iscmp():
        res = tmp(d0)
        cmp_value(op, l, r, res)
        depth = d0 + 1
        l = cmp_tail(res, d0)
        jz(l, lf)
    else:
        cmp_jf(op, l, r, lf)
    depth = d0


# ------------------------------------------------------------------ calls
def call(name):
    """name '(' args ')' ; returns the operand cell holding the result"""
    global tp, depth, pend, want, wantp, stmtcall
    disc = stmtcall                                # statement call: the result is not used
    stmtcall = False
    if name == "getc" or name == "putc":
        tp += 1                                    # '('
        d0 = depth
        if name == "getc":
            eatop(")")
            depth = d0 + 1
            res = result_cell(d0, 0, 0, "")
            emit(T, T); emit(T, IN_ADDR); emit(res, res); emit(res, T)
            return res
        pend += 1
        v = expression()
        pend -= 1
        eatop(")")
        emit(T, T); emit(T, v); emit(N - 1, T)
        depth = d0
        return ZERO
    if name in f_locals:
        err("%r is a variable, not a function" % name)
    if name not in funcs:
        if name in gvars or name in garrs:
            err("%r is not a function" % name)
        err("call to undefined (or not yet defined) function %r" % name)
    info = funcs[name]
    entry, nparams, pcells, ra = info[0], info[1], info[2], info[3]
    selfcall = (name == fname)
    tp += 1                                        # '('
    d0 = depth
    args = []
    sw, swp = want, wantp
    if not isop(")"):
        while True:
            j = len(args)
            pend += 1
            if selfcall or cz[tp] or j >= nparams:
                want = 0
            else:
                want = pcells[j]                   # compute the argument straight into the parameter cell
                wantp = pend
            a = expression()
            pend -= 1
            want, wantp = sw, swp
            if (selfcall and a in pcells[:j]) or (a in gvarcells and cz[tp]):
                t = tmp(depth)
                depth += 1
                mov(t, a)
                a = t
            args.append(a)
            if isop(","):
                tp += 1
            else:
                break
    eatop(")")
    if len(args) != nparams:
        err("function %r expects %d argument(s), got %d" % (name, nparams, len(args)))
    if selfcall:
        saved = [f_ra] + f_local_order + [tcells[i] for i in range(d0)]
        push_cells(saved)
    for i in range(nparams):
        setto(pcells[i], args[i])
    set_ra(ra, pc() + 6 + 3)
    jmp(entry)
    if selfcall:
        pop_cells(saved)
    if disc:
        depth = d0
        return ZERO
    depth = d0 + 1
    res = result_cell(d0, 0, 0, "")
    mov(res, RV)
    return res


# ------------------------------------------------------------------ statements
def block():
    global tp
    eatop("{")
    while not isop("}"):
        if tkind[tp] == K_EOF:
            err("unexpected end of file in block")
        statement()
    eatop("}")


def declare_local(name):
    if name in f_locals:
        err("redeclaration of local %r" % name)
    if name in funcs:
        err("local %r conflicts with a function name" % name)
    c = newcell()
    f_locals[name] = c
    f_local_order.append(c)


def do_return_value(v):
    setto(RV, v)
    jmpi(f_ra)


stmtmap = []     # (pc, source line) at each statement start; only written when V1C_MAP is set (profiling aid)


def statement():
    global tp, depth, pend, want, wantp, stmtcall
    depth = 0
    pend = 0
    want = 0
    stmtcall = False
    stmtmap.append((pc(), tline[tp]))
    k = tkind[tp]
    v = tval[tp]
    if k == K_KW:
        if v == "var":
            tp += 1
            declare_local(eatname())
            eatop(";")
        elif v == "if":
            tp += 1
            eatop("(")
            lelse = newlabel()
            cond(lelse)
            eatop(")")
            block()
            if iskw("else"):
                tp += 1
                if iskw("if"):
                    err("'else if' is not allowed; use else { if ... }")
                lend = newlabel()
                jmp(lend)
                place(lelse)
                block()
                place(lend)
            else:
                place(lelse)
        elif v == "while":
            tp += 1
            eatop("(")
            ltop = newlabel()
            lend = newlabel()
            place(ltop)
            cond(lend)
            eatop(")")
            block()
            jmp(ltop)
            place(lend)
        elif v == "return":
            tp += 1
            if isop(";"):
                err("bare 'return;' is not allowed")
            want = RV
            wantp = 0
            e = expression()
            want = 0
            eatop(";")
            do_return_value(e)
        else:
            err("unexpected keyword")
        return
    if k != K_NAME:
        err("expected statement")
    tp += 1
    if isop("="):
        tp += 1
        x = lookup_scalar(v)
        want = x
        wantp = 0
        e = expression()
        want = 0
        eatop(";")
        setto(x, e)
    elif isop("["):
        tp += 1
        base, size = lookup_array(v)
        i = expression()
        eatop("]")
        eatop("=")
        d0 = depth
        depth = d0 + 1
        pt = tmp(d0)
        if i == pt:
            emit(pt, lit(-base))                   # pt += base
        else:
            emit(pt, pt); emit(T, T); emit(T, i); emit(pt, T); emit(pt, lit(-base))
        val = expression()
        eatop(";")
        p0 = pc()
        w1, w2, w3 = p0 + 30, p0 + 31, p0 + 33
        emit(T, T); emit(T, pt)
        emit(w1, w1); emit(w1, T); emit(w2, w2); emit(w2, T); emit(w3, w3); emit(w3, T)
        emit(U, U); emit(U, val)
        emit(0, 0)
        emit(0, U)
    elif isop("("):
        stmtcall = True
        call(v)
        eatop(";")
    else:
        err("expected '=', '[' or '(' after identifier")


def function():
    global tp, nfuncs, fname, f_ra, f_params, f_locals, f_local_order, tcells, tset, depth
    tp += 1
    name = eatname()
    nfuncs += 1
    if nfuncs > MAX_FUNCS:
        err("more than %d functions" % MAX_FUNCS)
    if name in funcs or name in gvars or name in garrs or name == "getc" or name == "putc":
        err("redefinition of global name %r" % name)
    eatop("(")
    pnames = []
    if not isop(")"):
        while True:
            pnames.append(eatname())
            if isop(","):
                tp += 1
            else:
                break
    eatop(")")
    if len(pnames) > MAX_PARAMS:
        err("more than %d parameters" % MAX_PARAMS)
    fname = name
    tcells = []
    tset = {}
    f_locals = {}
    f_local_order = []
    depth = 0
    f_ra = newcell()
    pcells = []
    entry = pc()
    funcs[name] = [entry, len(pnames), pcells, f_ra]
    for pn in pnames:
        if pn in f_locals:
            err("duplicate parameter %r" % pn)
        if pn in funcs:
            err("parameter %r conflicts with a function name" % pn)
        c = newcell()
        f_locals[pn] = c
        f_local_order.append(c)
        pcells.append(c)
    block()
    clr(RV)
    jmpi(f_ra)


def global_decl():
    global tp, dp
    if iskw("var"):
        tp += 1
        name = eatname()
        eatop(";")
        if name in funcs or name in gvars or name in garrs or name == "getc" or name == "putc":
            err("redefinition of global name %r" % name)
        c = newcell()
        gvars[name] = c
        gvarcells[c] = True
    elif iskw("array"):
        tp += 1
        name = eatname()
        eatop("[")
        if tkind[tp] != K_NUM:
            err("array size must be a decimal literal")
        size = tval[tp]
        if size <= 0:
            err("array size must be positive")
        tp += 1
        eatop("]")
        eatop(";")
        if name in funcs or name in gvars or name in garrs or name == "getc" or name == "putc":
            err("redefinition of global name %r" % name)
        base = dp - size + 1
        if base < 1 << 19:
            err("array too large")
        dp = base - 1
        garrs[name] = (base, size)
    elif iskw("func"):
        function()
    else:
        err("expected top-level declaration")


def compile_v1(src):
    global T, U, T1, RV, LBOOT, f_locals, tp
    if len(src) > MAX_SRC:
        raise CompileError("source larger than %d bytes" % MAX_SRC)
    lex(src)
    T = newcell(); U = newcell(); T1 = newcell(); RV = newcell()
    pool.append(0)                                  # pool[0] = SP, filled in below
    LBOOT = newlabel()
    f_locals = {}
    tp = 0
    while tkind[tp] != K_EOF:
        global_decl()
    if "main" not in funcs:
        raise CompileError("no main function")
    if funcs["main"][1] != 0:
        raise CompileError("main must take no parameters")
    if mul_used:
        emit_mul_routine()
    place(LBOOT)
    entry, ra = funcs["main"][0], funcs["main"][3]
    set_ra(ra, pc() + 6 + 3)
    jmp(entry)
    emit(NEG1, ZERO, -1)                            # halt
    pool[0] = dp                                    # first free cell: the stack grows down from here
    code_end = CODE0 + len(code)
    words = [NEG1, ZERO, lab[LBOOT - LBLB], 0, 1, -1] + code + pool
    for i in range(6, code_end):
        v = words[i]
        if v >= LBLB:
            words[i] = lab[v - LBLB]
        elif v >= POOLB:
            words[i] = code_end + v - POOLB
    for i in range(6, code_end):
        if not (0 <= words[i] < N) and (i - CODE0) % 3 != 2 and words[i] != 0:
            raise CompileError("internal error: operand out of range at %d" % i)
    return words


def main():
    sys.setrecursionlimit(100000)
    data = sys.stdin.buffer.read()
    try:
        src = data.decode("latin-1")
        words = compile_v1(src)
    except CompileError as e:
        sys.stderr.write("v1c: %s\n" % e)
        return 1
    sys.stdout.write("\n".join(str(w) for w in words) + "\n")
    import os
    if os.environ.get("V1C_MAP"):
        with open(os.environ["V1C_MAP"], "w") as f:
            for a, l in stmtmap:
                f.write("%d %d\n" % (a, l))
    return 0


if __name__ == "__main__":
    import threading
    threading.stack_size(256 * 1024 * 1024)
    res = []
    t = threading.Thread(target=lambda: res.append(main()))
    t.start()
    t.join()
    sys.exit(res[0] if res else 1)
