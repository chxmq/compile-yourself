# tier3.v1: the v1 compiler written in v1

`tier3.v1` is a line-for-line port of `tier2/v1c.py` (the executable spec, `docs/ABI.md` "Final layout (v1c codegen)").
It reads a v1 program on stdin and prints the machine program, one decimal word per line, byte-identical to v1c.
No change to v1c was needed.

## Structure (top to bottom of the file)

1. **State**: global arrays only, sized for the limits (60,000 bytes, 200 functions, 24-char names).
2. **Lexer** (`lex`): single pass over stdin with one char of lookahead. Produces `tv[]`, one packed word per token
   (number v is stored as `-1-v`; other tokens as `kind*65536+value`). Names are interned in a **trie**
   (`tch/tsib/tchild`, node index = name id), so a symbol lookup is one array read and no string compare exists anywhere.
   Keywords, `getc`, `putc`, `main` are pre-inserted into the trie with numeric character codes (v1 has no strings).
3. **Token cursor**: `ck/cv` hold the current token; `adv`, `isop`, `iskw`, `eatop`, `eatname`.
4. **Output**: `outraw` prints a signed decimal with descending powers of ten (no division) and a newline;
   `outw` first resolves label (>= 2^30) and pool (>= 2^29) operands.
5. **Code buffer / cells / labels**: `emit3` / `emit2` (missing `c` = next instruction), `newcell`, `lit`, `newlabel`, `place`, `tmp`.
6. **Instruction sequences**: `mov jnz jz diff lt_jf lt_jt jmpi set_ra push_cells pop_cells ...`, the multiply routine, compare codegen.
7. **Expressions**, **statements**, **declarations**, and the **driver** (`compile_pass`, `main`).

## Two passes

The packed tokens are the in-memory source. `main` runs `compile_pass` twice.
Pass 1 (`pass == 1`): `emit3` only counts `pcc` (code words); `newlabel`/`place` fill `lab[]`; `lit` fills `poolv[]`; `dp` ends at the stack start.
Pass 2: the whole state is reset and the identical code generation runs again. `emit3` now prints, resolving label
operands through `lab[]` (from pass 1) and pool operands as `cend + index`; the pool is printed at the end from `poolv[]`.
The 6-word prefix is printed before pass 2 (`lab[0]` is `LBOOT`). Nothing needs buffering.

Fixed-size replacements for v1c's Python containers:

| v1c | tier3.v1 | size / why |
|---|---|---|
| `code` list | counter `pcc` only (pass 1) / streamed (pass 2) | no buffer |
| `lab` | `lab[60100]` | at most ~1 label per source byte (`a<a<a<...`) |
| `pool` | `poolv[60100]` | at most ~1 pool word per byte |
| dicts `gvars/garrs/funcs/f_locals` | one binding word per name node `gb[]` | value ranges encode the kind (cell, 2^21+base array, 2^22+function, 2^23 builtin, 2^24 keyword, 2^25+cell local); locals shadow by saving the old word in `locsave[]` and restoring at function end |
| `gvarcells` set | `gvc[]` (strictly decreasing cells) + binary search with powers of two | global scalar cells only |
| `tset` | linear scan of `tcells[]` | per-function temps |
| `args` lists | `argstk[]` with a stack pointer `asp` | calls nest |
| `saved` list of a self call | computed on the fly by `savedcell(k)` | no array |
| `bit_length`, bit tests | table `p2[]` of powers of two and subtraction | no shifts/ands |

Data is about 0.43M words, which keeps `base >= 2^19` for every array (v1c's "array too large" rule).

## Recursion constraints (no mutual recursion, direct self-recursion only)

v1c's recursive descent is `expression -> additive -> term -> unary -> primary -> expression/call` and
`statement <-> block`. Both are folded into single self-recursive functions:

* `parse(p)` is **precedence climbing**: it parses a unary/primary operand (including `( ... )`, array index, call arguments,
  unary minus, all by calling `parse` itself) and then loops over operators of precedence >= `p`
  (3 `*`, 2 `+ -`, 1 comparisons), recursing with `parse(pr+1)` for the right operand (left associativity).
  `parse(1)` is `expression`, `parse(2)` is `additive`, `parse(4)` is a unary operand. The temp/depth discipline
  (`d0` at entry, `depth = d0+1`, snapshot of a global left operand, result in temp `d0`) is the same as v1c's, so the output is identical.
* Call arguments are collected on the global `argstk`; the non-recursive `call_finish` emits the call, so `parse` only recurses for the arguments.
* `cond` and `cmp_tail` are ordinary functions defined after `parse` (they only call earlier functions).
* `stmt(mode)`: mode 1 parses a block and loops calling `stmt(0)`; `if` / `while` call `stmt(1)` for their bodies. A call statement
  `f(...);` is parsed by `parse(4)` after peeking at the next token in `tv[]`.
* Everything else (assignment, declarations, function definition) is non-recursive and defined before its first use.

Recursion depth is about two `parse` frames per nesting level of parentheses/calls and one `stmt` frame per nested block,
well below 1000 for realistic programs.

## Errors

v1 has no exit call, so `fail()` stores to `junk[-2000000]`, which makes the VM stop with an "operand outside memory" error
(nonzero exit). It is only reached on invalid input and always before any output is printed (all errors are found in the
lexer or in pass 1). This is deliberate undefined behaviour in the sense of the v1 spec, used only for rejecting invalid programs.

## Testing

`scripts/tier3_diff.sh` (corpus, byte comparison against v1c), `scripts/tier3_fuzz.sh N seed` (fuzz programs, byte comparison),
`scripts/bootstrap.sh` (C1, C2, C3 and all of the above end to end).
