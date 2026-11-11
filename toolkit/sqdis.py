#!/usr/bin/env python3
"""sqdis: disassembler for the subtract-and-branch-if-negative machine.

    sqdis.py program.txt [--map program.map] [--code-end ADDR]

With a map file (from `sqasm.py --map`) it knows where code ends, uses symbol names and
labels, and flags self-modifying code.  Without a map it assumes the sqasm prefix layout
(ZERO=3, ONE=4, NEG1=5) and treats everything up to the last word as code unless --code-end
is given (data then prints as garbage instructions; pass --code-end to avoid that).

Annotations:
  CLR x / SUB x,y / JMP c / BLT0 x,c / HALT / OUT <- y / x <- IN idioms
  "patches b of instr @A"  when an instruction's `a` operand is a word inside the code region
  (the destination of a patch is a pointer-access / indirect-jump idiom; see docs/ABI.md).
"""
import argparse, json, sys

N = 1 << 20
IN, OUT = N - 2, N - 1


def load_words(path):
    with open(path) as f:
        return [int(t) for t in f.read().split()]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("prog")
    ap.add_argument("--map")
    ap.add_argument("--code-end", type=int)
    args = ap.parse_args(argv)
    w = load_words(args.prog)
    names, code_start, code_end = {}, 6, len(w)
    if args.map:
        m = json.load(open(args.map))
        code_start, code_end = m["code_start"], m["code_end"]
        for k, v in m["symbols"].items():
            names.setdefault(v, k)
    if args.code_end is not None:
        code_end = args.code_end
    for k, v in (("ZERO", 3), ("ONE", 4), ("NEG1", 5), ("IN", IN), ("OUT", OUT)):
        names[v] = k

    def nm(x):
        pi = patch_info(x)
        if pi:
            return "<%s>" % pi
        if x in names:
            return names[x]
        return "@%d" % x

    def cd(x):  # code address operand
        if x in names:
            return names[x]
        return str(x)

    def patch_info(x):
        if code_start <= x < code_end:
            off = x - code_start
            return "field %s of instr @%d" % ("abc"[off % 3], x - off % 3)
        return None

    # header
    print("; boot: [0..2] = %s" % " ".join(map(str, w[:3])))
    i = code_start if w[:3] and len(w) > 5 else 0
    if len(w) > 5:
        print("%7d  .word %d, %d, %d  ; ZERO ONE NEG1" % (3, w[3], w[4], w[5]))
    else:
        i = 0
    while i < min(code_end, len(w)):
        lab = names.get(i)
        if lab and not lab.startswith(("K_", "A_", "T", "U")) and (i - code_start) % 3 == 0:
            print("%s:" % lab)
        if i + 2 >= len(w):
            print("%7d  .word %s" % (i, " ".join(map(str, w[i:])))); break
        a, b, c = w[i:i + 3]
        nxt = i + 3
        notes = []
        if not (0 <= a < N and 0 <= b < N):
            txt = "?? bad operands"
        elif a == b and c == nxt and a != OUT:
            txt = "CLR %s" % nm(a)
        elif a == OUT and c == nxt:
            txt = "OUT <- -%s   (prints low 8 bits of -%s)" % (nm(b), nm(b))
        elif a == 5 and b == 3:
            txt = "HALT" if c == -1 else "JMP %s" % cd(c)
        elif b == 3 and c != nxt:
            txt = "BLT0 %s, %s" % (nm(a), cd(c))
        elif b == IN and c == nxt:
            txt = "%s = -next_byte   (EOF: +1)" % nm(a)
        elif c == nxt:
            txt = "SUB %s, %s" % (nm(a), nm(b))
        else:
            txt = "%s -= %s ; if <0 goto %s" % (nm(a), nm(b), cd(c))
        pa = patch_info(a) if 0 <= a < N else None
        if pa:
            notes.append("PATCH: writes %s" % pa)
        pb = patch_info(b) if 0 <= b < N else None
        if pb and b != a:
            notes.append("reads code word %s" % pb)
        print("%7d  %-14s %-52s %s" % (i, "%d %d %d" % (a, b, c), txt, ("; " + "; ".join(notes)) if notes else ""))
        i += 3
    if code_end < len(w):
        print("; data from %d" % code_end)
        for j in range(code_end, len(w)):
            n = names.get(j)
            print("%7d  .word %d%s" % (j, w[j], ("   ; " + n) if n else ""))


if __name__ == "__main__":
    main()
