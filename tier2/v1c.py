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
MA = MB = MR = MNA = 0
MRA = CODE0 + 2        # patch word (c field) of the multiply routine's return instruction, the first code instruction
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
f_local_names = []   # their names
fend = 0         # token index of the closing brace of the current function
loop0 = -1       # token index of the outermost enclosing while (-1: not in a loop)
stmt0 = 0        # token index where the current statement starts
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


def istemp(c):
    """expression temp: dead after the comparison that consumes it, so it may be overwritten"""
    return c in tset


def lt_jf(x, y, lf):
    """if !(x < y) goto lf, else fall through.  True signed compare.
    The difference is computed in x itself (1 step) when x is a temp, else into T1 (5 steps)."""
    p = pc()
    nd = 1 if istemp(x) else 5
    a2 = p + 12 + 3 * nd                       # both operands negative: same sign
    emit(x, ZERO, a2)
    emit(y, ZERO, lf)
    if nd == 1:
        emit(x, y)
        d = x
    else:
        diff(T1, x, y)                         # same sign: no overflow
        d = T1
    emit(d, ZERO, a2 + 3)
    jmp(lf)
    emit(y, ZERO, p + 6)                       # a2
    # a2+3: true (x < 0 <= y) falls here


def lt_jt(x, y, lt):
    """if x < y goto lt, else fall through."""
    p = pc()
    nd = 1 if istemp(x) else 5
    a2 = p + 12 + 3 * nd
    emit(x, ZERO, a2)
    emit(y, ZERO, a2 + 6)
    if nd == 1:
        emit(x, y)
        d = x
    else:
        diff(T1, x, y)
        d = T1
    emit(d, ZERO, lt)
    jmp(a2 + 6)
    emit(y, ZERO, p + 6)                       # a2
    jmp(lt)                                    # a2+3: x < 0 <= y
    # a2+6: false falls here


def set_ra(ra, retaddr):
    """ra = retaddr (2 steps).  The caller emits the jump right after."""
    emit(ra, ra)
    emit(ra, lit(-retaddr))


def push_cells(cells):
    """push cells onto the software stack (SP = POOLB+0, grows downwards); each cell is stored negated.
    2 + 9m + 1 steps."""
    SP = POOLB
    emit(T, T); emit(T, SP)                        # T = -SP
    for ck in cells:
        b = pc()
        w1, w2, w3 = b + 18, b + 19, b + 21
        emit(w1, w1); emit(w1, T)
        emit(w2, w2); emit(w2, T)
        emit(w3, w3); emit(w3, T)
        emit(0, 0)                                 # [p] = 0   (a, b patched)
        emit(0, ck)                                # [p] -= ck (a patched): [p] = -ck
        emit(T, NEG1)                              # T = -(p-1)
    emit(SP, lit(len(cells)))                      # SP -= m


def pop_cells(cells):
    """inverse of push_cells: 1 + 2 + 5m steps."""
    SP = POOLB
    emit(SP, lit(-len(cells)))                     # SP += m
    emit(T, T); emit(T, SP)
    for ck in cells:
        b = pc()
        w = b + 3 + 1 + 3 + 3
        emit(w, w); emit(w, T)
        emit(ck, ck)
        emit(ck, 0)                                # ck -= mem[b+9]  (b patched): ck = -(-ck)
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
        ci = constval(i)
        if ci is not None and 0 <= ci < size:
            emit(res, res); emit(res, base + ci)   # constant index: res = -(stored -a[c])
            return res
        p0 = pc()
        wpatch = p0 + 18 + 1
        emit(T, T); emit(T, i)
        emit(wpatch, wpatch); emit(wpatch, T); emit(wpatch, lit(-base))
        emit(res, res); emit(res, 0)               # arrays hold the negated values
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
    """res = x * c for a compile-time constant c: Horner over the bits of |c|.
    res != x: the top two bits are loaded as 2 or 3 subtractions of -x (or, for c < 0, of x: the result
    is built negated, so there is no final negation).  res == x: in place, then negate if c < 0."""
    if c == 0:
        clr(res)
        return
    neg = c < 0
    m = -c if neg else c
    nb = m.bit_length()
    if res != x:
        if nb == 1:
            if neg:
                clr(res); emit(res, x)             # res = -x
            else:
                mov(res, x)
            return
        if neg:
            k = x                                  # res -= x adds -x
        else:
            emit(U, U); emit(U, x)                 # U = -x
            k = U
        clr(res)
        for _ in range(m >> (nb - 2)):             # 2 or 3
            emit(res, k)
        for bit in range(nb - 3, -1, -1):
            emit(T, T); emit(T, res); emit(res, T)     # res += res
            if (m >> bit) & 1:
                emit(res, k)
        return
    if m & (m - 1):
        emit(U, U); emit(U, x)                     # U = -x
    for bit in range(nb - 2, -1, -1):
        emit(T, T); emit(T, res); emit(res, T)     # res += res
        if (m >> bit) & 1:
            emit(res, U)                           # res += x
    if neg:
        emit(T, T); emit(U, U); emit(T, res); emit(U, T)
        emit(res, res); emit(res, U)               # res = -res


def gen_mul(res, l, r):
    global mul_used, MA, MB, MR, MNA, LMUL
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
        MA = newcell(); MB = newcell(); MR = newcell(); MNA = newcell()
        LMUL = newlabel()
    mov(MA, l)
    mov(MB, r)
    set_ra(MRA, pc() + 6 + 3)
    jmp(LMUL)
    mov(res, MR)


MUL_LEVELS = (3, 6, 10, 14, 18, 22, 26, 30)     # magnitude dispatch: MB < 2^e enters the bit test of bit e-1


def emit_mul_routine():
    """MR = MA * MB mod 2^32 (see docs/ABI.md).
    Sign fix: MB < 0 becomes -MB and the multiplicand sign is flipped (MNA = -MA, or MA after the flip), so the
    multiplier is in 0..2^31-1 (INT_MIN is the one value that stays negative; it is MA << 31).
    MB < 8: repeated addition.  Otherwise the unrolled MSB-first bit tests of MB against 2^j (subtract; if negative
    add back), entered at the highest bit that can be set: about 5.5 steps per bit."""
    mp = [lit(1 << j) for j in range(31)]
    mn = [lit(-(1 << j)) for j in range(31)]
    lneg = newlabel(); ldisp = newlabel(); lmin = newlabel(); ld30 = newlabel()
    lret = CODE0                                   # the return instruction at the start of the code
    lent = [newlabel() for j in range(31)]
    lsm = {}
    for e in MUL_LEVELS:
        lsm[e] = newlabel()
    place(LMUL)
    clr(MR)
    emit(MB, ZERO, lneg)                           # MB < 0 ?
    emit(MNA, MNA); emit(MNA, MA)                  # MNA = -MA
    place(ldisp)
    for e in MUL_LEVELS:
        emit(MB, mp[e], lsm[e])                    # MB < 2^e ?  (MB -= 2^e, negative)
        emit(MB, mn[e])                            # no: restore
    jmp(lent[30])
    for e in reversed(MUL_LEVELS):
        place(lsm[e])
        emit(MB, mn[e])                            # restore
        if e == 3:
            p = pc()
            emit(MB, ONE, lret)                    # --MB < 0 -> done
            emit(MR, MNA)                          # MR += multiplicand
            jmp(p)
        else:
            jmp(lent[e - 1])
    place(lneg)
    emit(U, U); emit(U, MB)                        # U = -MB
    mov(MB, U)                                     # MB = -MB
    mov(MNA, MA)                                   # MNA = MA: MR -= MNA adds -MA
    emit(MB, ZERO, lmin)                           # still negative: INT_MIN
    jmp(ldisp)
    place(lmin)
    emit(MB, mn[30]); emit(MB, mn[30])             # MB += 2^31 -> 0
    emit(MR, MNA)                                  # MR = +-MA, then 31 doublings
    jmp(ld30)
    for j in range(30, -1, -1):
        if j == 30:
            place(ld30)
        b = pc()
        emit(T, T); emit(T, MR); emit(MR, T)       # MR += MR
        place(lent[j])
        emit(MB, mp[j], b + 18)                    # bit j clear: MB < 2^j
        emit(MR, MNA)                              # bit j set: MR += multiplicand
        jmp(b + 21)
        emit(MB, mn[j])                            # b+18: restore
    jmp(lret)


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
            emit(T, T); emit(T, l); emit(T, r)     # T = -(l + r)
            emit(res, res); emit(res, T)
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
        if constval(l) is not None and constval(r) is None:
            l, r = r, l
        if r == ZERO:
            d = l
        elif constval(l) is None and constval(r) is not None:
            # compare with a literal c: subtract c in place, test for zero, add it back
            nk = lit(-constval(r))
            p = pc()
            emit(l, r)
            emit(l, ZERO, p + 18)
            emit(T, T); emit(T, l, p + 18)
            if op == "==":                         # false (-> lf) unless x - c == 0
                emit(l, nk); jmp(p + 24)
                emit(l, nk); jmp(lf)
            else:                                  # false (-> lf) if x - c == 0
                emit(l, nk); jmp(lf)
                emit(l, nk)
            return
        else:
            if istemp(r) and not istemp(l):
                l, r = r, l
            if istemp(l):
                emit(l, r)                         # difference in the temp
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
    cr = constval(r)
    if cr is not None and cr > 0 and cr < INT_MAX and constval(l) is None:
        # compare with a positive literal c: subtract c in place, test the sign, add it back (4 steps)
        k = r
        if op == "<=" or op == ">":                # x <= c  is  x < c+1 ;  x > c  is  x >= c+1
            cr += 1
            k = lit(cr)
            op = "<" if op == "<=" else ">="
        nk = lit(-cr)
        p = pc()
        if op == "<":                              # false if x >= c
            emit(l, ZERO, p + 15)                  # x < 0: true
            emit(l, k, p + 12)                     # x - c < 0: true (after restoring)
            emit(l, nk); jmp(lf)
            emit(l, nk)                            # p+12
        else:                                      # ">=": false if x < c
            emit(l, ZERO, lf)
            emit(l, k, p + 12)
            emit(l, nk); jmp(p + 18)
            emit(l, nk); jmp(lf)                   # p+12: x < c, restore
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


LIVE_SCAN_MAX = 4000    # longer ranges are not scanned: every local is saved


def live_locals():
    """cells of the params and locals that a self call must save: those whose name occurs from the start of the
    current statement (pending operands) to the end of the function, or from the start of the outermost
    enclosing loop (the loop body runs again)"""
    s0 = loop0 if loop0 >= 0 else stmt0
    if fend - s0 > LIVE_SCAN_MAX:
        return list(f_local_order)
    refs = set()
    for i in range(s0, fend):
        if tkind[i] == K_NAME:
            refs.add(tval[i])
    return [c for c, nm in zip(f_local_order, f_local_names) if nm in refs]


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
        saved = [f_ra] + live_locals() + [tcells[i] for i in range(d0)]
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
    f_local_names.append(name)


def do_return_value(v):
    setto(RV, v)
    jmp(f_ra - 2)


stmtmap = []     # (pc, source line) at each statement start; only written when V1C_MAP is set (profiling aid)


def simple_value():
    """the value of an array assignment is a literal or a variable (parsing it emits no code)"""
    k = tkind[tp]
    if k == K_NUM or k == K_NAME:
        return tkind[tp + 1] == K_OP and tval[tp + 1] == ";"
    if k == K_OP and tval[tp] == "-":
        return tkind[tp + 1] == K_NUM and tkind[tp + 2] == K_OP and tval[tp + 2] == ";"
    return False


def statement():
    global tp, depth, pend, want, wantp, stmtcall, loop0, stmt0
    depth = 0
    stmt0 = tp
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
            saved_l0 = loop0
            if loop0 < 0:
                loop0 = tp
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
            loop0 = saved_l0
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
        ci = constval(i)
        if ci is not None and 0 <= ci < size:
            val = expression()
            eatop(";")
            emit(base + ci, base + ci); emit(base + ci, val)   # a[c] = -v
            return
        if simple_value():
            val = expression()                     # no code: the pointer can go straight into T
            eatop(";")
            emit(T, T); emit(T, i); emit(T, lit(base))         # T = -(i + base)
            src = T
        else:
            d0 = depth
            depth = d0 + 1
            pt = tmp(d0)
            if i == pt:
                emit(pt, lit(-base))               # pt = i + base
                val = expression()
                eatop(";")
                emit(T, T); emit(T, pt)            # T = -pt
                src = T
            else:
                emit(pt, pt); emit(pt, i); emit(pt, lit(base))  # pt = -(i + base), kept across the value
                val = expression()
                eatop(";")
                src = pt
        p0 = pc()
        w1, w2, w3 = p0 + 18, p0 + 19, p0 + 21
        emit(w1, w1); emit(w1, src); emit(w2, w2); emit(w2, src); emit(w3, w3); emit(w3, src)
        emit(0, 0)
        emit(0, val)                               # [p] = -val
    elif isop("("):
        stmtcall = True
        call(v)
        eatop(";")
    else:
        err("expected '=', '[' or '(' after identifier")


def function():
    global tp, nfuncs, fname, f_ra, f_params, f_locals, f_local_order, f_local_names, fend, tcells, tset, depth
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
    f_local_names = []
    j = tp
    d = 0
    while tkind[j] != K_EOF:                       # find the end of the function body
        if tkind[j] == K_OP and tval[j] == "{":
            d += 1
        elif tkind[j] == K_OP and tval[j] == "}":
            d -= 1
            if d == 0:
                break
        j += 1
    fend = j
    depth = 0
    f_ra = pc() + 2                                 # the c word of the return instruction, patched by the callers
    emit(NEG1, ZERO, 0)
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
        f_local_names.append(pn)
        pcells.append(c)
    block()
    clr(RV)
    jmp(f_ra - 2)


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
    emit(NEG1, ZERO, 0)                             # return instruction of the multiply routine (address CODE0)
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
