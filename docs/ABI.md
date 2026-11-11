# ABI: the contract between machine, tier-2 compiler (`v1c`) and tier-3 compiler (`tier3.v1`)

Status: **v0 (design contract).** The agent who implements `v1c` appends a "Final layout" section.
`tier2/v1c.py` is then the executable specification that `tier3/tier3.v1` must reproduce byte for byte.

## 0. Machine facts (from the task statement)

- N = 1,048,576 words, signed 32-bit, wraparound. `IN` = N-2, `OUT` = N-1. The program file is loaded at address 0, `pc` starts at 0.
- One instruction `a b c`: `mem[a] -= mem[b]; if mem[a] < 0 then pc = c else pc += 3`.
- Halt: after a step, `pc` outside `0..N-3`. The normal exit is a jump to -1.
- Reading `IN` as `b` yields the next byte (0..255), or -1 at EOF, and consumes it.
- `OUT` as `a`: prints the low 8 bits of `r = 0 - mem[b]`, stores nothing, and still branches on `r < 0`.
- `IN` as `a`, or any operand outside `0..N-1`, is an error.
- Step limits: 10^8 for each Tier-1/2 run, 10^10 for a Tier-3 compiler run.

## 1. Core idioms

Every instruction's `c` field is set to the **next instruction's address** (`pc+3`) unless the instruction is a real branch.
That makes the branch harmless, and keeps the program well-defined whichever way the sign goes.

Reserved cells at fixed low addresses (in the file prefix): `ZERO` = 0, `ONE` = 1, `NEG1` = -1. These are never modified.
`T0, T1, ...` are scratch cells, zeroed between macros unless a macro says otherwise.

| Macro | Expansion | Steps |
|---|---|---|
| `CLR x` | `x x +` | 1 |
| `SUB x, y` (x -= y) | `x y +` | 1 |
| `NEGTO t, y` (t = -y) | `t t +` ; `t y +` | 2 |
| `ADD x, y` (x += y) | `T T +` ; `T y +` ; `x T +` | 3 |
| `MOV x, y` | `x x +` ; `T T +` ; `T y +` ; `x T +` | 4 |
| `JMP L` | `NEG1 ZERO L` (always negative, so always jumps) | 1 |
| `BLT0 x, L` (x < 0 goto L) | `x ZERO L` (also leaves x unchanged) | 1 |
| `HALT` | `NEG1 ZERO -1` | 1 |

- Equality tests, `<=` and `>` are built from two `BLT0` tests on `x` and `-x`.
- **True signed `<`** between two arbitrary 32-bit values must NOT use a single subtraction, because it can overflow:
  - If the signs differ, the negative one is smaller.
  - If the signs are equal, `a - b` cannot overflow, so use it.
  - Test the sign with `BLT0`.
  - Example: `2000000000 < -2000000000` must be 0.
- **Output of v:** put `-v` in a cell `t` (`NEGTO`), then emit `OUT t +`. The machine prints `0 - (-v) = v`.
- **Input:** `T T +` ; `T IN +` gives `T = -byte` (and `T = +1` at EOF). Then `NEGTO` to get the byte, which is -1 at EOF.

## 2. Indirection by self-modification (pointer access)

There is no indexed addressing. To dereference a pointer value `p` held in a cell:

1. Name a **patch word**: the `a`, `b` or `c` field of a later instruction. Its address is a fixed known cell.
2. Overwrite it with `p` (`CLR w` ; `T = -p` ; `w -= T`).
3. Execute the patched instruction.

| Operation | Patched instruction |
|---|---|
| `LOAD x <- [p]` | `T T +` ; `T [p] +` (patch its `b`) ; `NEGTO` into x |
| `STORE [p] <- v` | `[p] [p] +` (patch both `a` and `b`) ; `[p] T +` where `T = -v` (patch `a`) |
| Indirect jump (return) | `NEG1 ZERO [target]` (patch its `c`) |

An array access `arr[e]` computes `pointer = base + e` and then does a LOAD or STORE.
No bounds checks are needed, because out-of-range indexing is undefined in v1.

## 3. Calling convention: static frames

**Key fact:** v1 requires "a function must be defined before it is used".
A call graph is therefore a DAG plus direct self-recursion; there is no mutual recursion.

1. Each function `f` has **static cells**: its params, locals, expression temporaries, and a return-address cell `RA_f`.
   Cells are allocated top-down from address N-3, so no code-size-dependent address ever appears in the data.
   Every operand is a plain direct address.
2. Globals and global arrays live in the same top-down data region.
   The **software stack** for recursion lives below the data region and grows downward.
   Code grows upward from the file prefix.
3. **Return value** goes through one global cell `RV`.
4. **Call `f(e1..ek)`:**
   - Evaluate **all** args left to right into caller-side temps.
   - Copy the temps into `f`'s param cells.
   - Set the return address and `JMP f`.
   - After return, `MOV tmp, RV`.
   - Evaluating all args before writing any param is required, so an arg expression that itself calls `f` cannot clobber half-written params.
5. **Return:** `MOV RV, e`, then an indirect jump through `RA_f` (patch the `c` word, then execute the jump).
6. **Self-call site only:**
   - Push every frame cell of the caller (params, locals, live temps, `RA_f`) onto the software stack, using STORE through `sp`.
   - Do the call.
   - Pop afterwards, in reverse order, using LOAD.
   - Non-recursive functions never touch the stack.
7. `main` is just called at boot. After `main` returns, execute `HALT`.
8. **Left-to-right evaluation** of operands and args is mandatory. Where an operand's value can be changed by a later call, snapshot it into a temp first.

## 4. Runtime routines (emitted once per program, only if used)

- `MUL`: 32-iteration shift-and-add on the bit pattern. This is correct modulo 2^32 for any signs.
  Reaching bit i needs doubling `y`, which is `ADD y, y`. Testing the low bit of `x` needs a halving trick.
  The implementer picks the exact method. The tier-2/3 contract is only: `MUL` result == wrapped 32-bit product.
- `CMP`: signed `<` as described in section 1.
- Prefer inline expansion for compare against constant 0 and multiply by small constants.

## 5. Determinism and the two-compiler rule

- `v1c` and `tier3.v1` must emit **byte-identical machine programs** for every valid v1 program.
  Differential testing enforces this, and it enables the Diverse-Double-Compiling demo.
- All numbering (labels, temps, cells) is by deterministic first use, in source order.
- Any optimization added to one compiler must be mirrored in the other.
  If the deadline forces a split, `v1c --canon` (no extra optimization) is the contract, and the divergence is documented.
- `tier3.v1` works in **two passes** over the in-memory source (`array src[60001]`). This avoids buffering output.
  - Pass 1 runs codegen with output suppressed, just counting `pc` and recording every label's address in an array.
  - Pass 2 runs the identical codegen, writes numbers with `putc`, and reads label addresses from the array.
- Output format: whitespace-separated decimal integers. A single space or newline is fine, as long as it is the same in both compilers.

## 6. Tier-3 resource budget (tier3.v1 as a v1 program)

- The compiled `tier3.v1` (C1) must fit in far less than 1,048,574 words (code plus data plus stack).
- A run on a 60 KB input must finish in under 10^10 steps. Our target is under 3×10^9.
- Number printing has no division. Subtract descending powers of ten (a table of 10^9 .. 10^0) per digit.

## Final layout (assembler)

Implemented by `toolkit/sqasm.py` (`Asm`, importable; the tier-2 compiler should emit through it). Verified by `tests/test_sqasm.py` and the tier-1 programs.

**File prefix (fixed):**

| Address | Content |
|---|---|
| 0, 1, 2 | boot jump `NEG1 ZERO <entry>` = `5 3 <entry>`. It is always taken (-1 - 0 < 0). The entry is `Asm.entry` (a label) or 6 by default. |
| 3 | `ZERO` = 0 |
| 4 | `ONE` = 1 |
| 5 | `NEG1` = -1 |
| 6.. | code, 3 words per instruction, in emission order |

`ZERO`, `ONE` and `NEG1` are never written. Their `Asm` names are `"ZERO"`, `"ONE"`, `"NEG1"`, and `const(0/1/-1)` returns them.

**After the code (assembler default):**

1. Initialised cells and arrays (nonzero or symbolic init), in declaration order.
2. Zero-initialised cells and `bss` space, including the scratch cells `T`, `U` and `T1`.
   These are never written to the file: trailing zero words are trimmed, since memory starts zeroed.
3. Optionally, cells pinned with `addr=` (this is how a compiler gets the top-down region at N-3 and below).
   The file is then dense up to the highest nonzero pinned word.

**Scratch conventions.** Macros clear their own scratch before use, so no cell is assumed to hold any value on entry.

| Macro | Scratch used |
|---|---|
| `add`, `mov`, `putc`, `jmpi` | `T` |
| `load` | `T` |
| `store`, `neg` | `T`, `U` |
| compares and `beq`, `bne` | `T1`, `T` |

- `getc x` needs `x != T`.
- `negto` and `mov` require distinct operands. `mov x, x` emits nothing.
- `slt dst, x, y` needs `dst` distinct from `x` and `y`.
- `store p, v` needs `v` not to be `T` or `U`.

**Pointer access, exact patched words** (each site allocates its own patch words, so sites never interfere):

| Macro | Expansion |
|---|---|
| `load x,p` | `T=-p`; `[L+1] := p` (CLR, then SUB T); `T=0`; `L: T <b> +` with b patched; `x = -T`. 8 steps. |
| `store p,v` | `T=-p`; patch `a` and `b` of `I1: <a> <b> +` and `a` of `I2: <a> U +`; `U=-v`; `I1; I2`. 12 steps. |
| `jmpi t` | `T=-t`; `[J+2] := t`; `J: NEG1 ZERO <c>`. 5 steps. |

`Program.patches` and the `--map` JSON list every patched word, which the visualiser and `sqdis` use.

**Calls.** `call f, ra` is `ra = const(-retlabel)` negated (2 steps: `ra ra +` then `ra -= K_neg_ret`), then `JMP f`.
`ret ra` is `jmpi ra`. This is a plain static-frame call: 3 steps to enter, 5 to return.

**Compares.** `blt/bge/bgt/ble x,y,L` are the true signed compares (sign test first, subtract only when the signs agree).
`beq`/`bne` subtract and test for zero (wrapping `x-y` is 0 only when `x == y`).
`bz`, `bnz`, `bgt0` and `ble0` test the sign first, so `INT_MIN` is handled correctly.

**Output formats.** `sqasm.py SRC -o out.txt [--map out.map] [--list]`. `SRC` is a `.py` file defining `build()` (or `asm`) or a `.sqa` text file.
`sqdis.py out.txt --map out.map` disassembles with idiom names and flags patch writes.

**VM facts checked against the official binary's disassembly** (see `vm/sqvm.c` header):
- The step limit is the constant 10^8 in the official VM. `sqvm` honours `SQ_LIMIT`.
- Exit codes: 1 for an error, 2 for a timeout.
- `a`, `b` and `c` are all fetched before the write, so an instruction that patches its own `c` still uses the old `c`.
- A program may have at most N-2 words.

## Final layout (v1c codegen)

`tier2/v1c.py` is the executable specification. `tier3.v1` must emit byte-identical output.
Below, `emit(a,b,c)` appends an instruction. A missing `c` means "the address of the next instruction".
`pc` is the address of the next word to be emitted. Code starts at address 6, so `pc = 6 + len(code)`.
All patch-word addresses are absolute and known at emission time (the patched instruction sits at a fixed offset from `pc`).

**Output format.** Words 0 .. `code_end + len(pool) - 1`, one decimal word per line, each followed by `\n`.

| Addresses | Content |
|---|---|
| 0..5 | `5 3 <LBOOT>`, then `0 1 -1` (see the assembler layout above). |
| 6..8 | The return instruction of the multiply routine, `NEG1 ZERO 0` (its `c` word, address 8, is patched). Present in every program. |
| 9..code_end-1 | Code. |
| code_end.. | Literal pool, in allocation order. |
| N-3 downwards | Zero-initialised data cells. They are not in the file (the file ends at the pool). |

`code_end = 6 + len(code)`. Operand encoding during compilation:

| Value | Meaning |
|---|---|
| `>= 2^30` | Label `v - 2^30`. |
| `>= 2^29` | Pool cell `v - 2^29`. |
| otherwise | Absolute address. |

Labels and pool refs are resolved after the last function is emitted. Only code words are decoded; pool values are literal.

**Pool.** `lit(v)` returns ZERO, ONE or NEG1 for 0, 1 or -1.
Otherwise it appends `v` to the pool and returns a new pool cell (no deduplication). Pool cell 0 is the stack pointer `SP`.
Its initial value is `dp` at the end of compilation (the first free data cell), and the stack grows downwards from there.
The same pool is used for return-address constants (`set_ra`).

**Data cells.** `dp` starts at N-3. `newcell()` returns `dp`, then decrements it. Allocation order is exactly:

1. `T`, `U`, `T1`, `RV` (the cells N-3, N-4, N-5, N-6).
2. Source order of declarations:
   - `var g;` takes one cell.
   - `array a[s];` has `base = dp - s + 1`, `dp = base - 1`, and element `i` is at `base + i`.
     **Array elements are stored negated** (`mem[base+i] = -a[i]`; an untouched element is 0 either way).
   - For `func`: the parameters in order, then each `var` at its declaration, and temporaries at first use.
     There is no return-address data cell: see "Calls".
3. Multiply-routine cells `MA, MB, MR, MNA` at first use (in that order) of a non-constant `*`.

**Labels.** Numbered from 0 in creation order. Label 0 is `LBOOT`, created before the first declaration.
Each `if` creates a label for else/end, and `while` creates 2 labels. The comparison value code creates 2 per comparison.
The multiply routine creates its labels when it is emitted (see "Multiplication").

**Temporaries.** `depth` counts reserved temps. Temp `i` is a function-local cell, created at first need.

An expression returns an operand cell:
- a variable cell,
- a literal cell,
- or temp `d0` (where `d0` is the depth at entry), or, for the last operation of a statement (see "Destination"), the destination cell.

A binary operator does the following:
1. Parse `L`. Reserve temp `d0` (`depth = d0+1`; the cell is created at that point).
2. If `L` is a global scalar **and the rest of the statement contains a call** (`cz[tp]`, see below), copy it into temp `d0`.
3. Parse `R`.
4. Write the result to `result_cell` (temp `d0`, or the destination), then set `depth = d0+1`.

The result cell never aliases `R` (except for `+`, which then swaps its operands). Each statement resets `depth` to 0.
The unary minus of a literal is folded into the literal.

**`cz` (call ahead).** After lexing, `cz[i] = 1` when a call `name (` occurs in tokens `i ..` up to (not including) the next `;`, `{` or `}`.
It is conservative (it includes calls after the operand in the same statement) and purely syntactic (`getc`/`putc` count too).

**Destination.** The statements `x = e;` (destination `x`), `return e;` (destination `RV`) and each argument of a non-self call
whose rest of the statement has no call (`cz == 0`; destination: the callee's parameter cell) set `want` and `wantp`.
`pend` counts the operators whose right operand (or a parenthesis, index or argument) is being parsed.
An operation is *final* when `want != 0`, `pend == wantp` and the next token is `;`, `,` or `)`.
A final `+ - *`, array read, `getc()` or call writes its result into `want` instead of temp `d0`, with one exception:
`l - r` with `r == want != l` uses the temp (a later `mov` finishes the statement). A value that is already in `x` needs no `mov`.
The call of a statement `f(...);` does not copy `RV` at all (`stmtcall`).

**Sequences** (`T`, `U`, `T1` are the scratch cells; every sequence clears them itself):

| Name | Sequence |
|---|---|
| `mov x,y` | `x x; T T; T y; x T` (nothing if `x == y`). |
| `setto x,e` | `mov x,e`, but a literal `c` is `x x; x lit(-c)` (just `x x` for 0). |
| `jmp l` | `NEG1 ZERO l`. |
| `jnz d,l` | `d ZERO l; T T; T d l`. |
| `jz d,l` | `d ZERO pc+12; T T; T d pc+12; jmp l`. |
| `diff t,x,y` (t = x-y) | `t t; T T; T x; t T; t y`. |
| `add res,l,r`: `r == res`, `l != res` | swap `l` and `r`. |
| `add`, `l == res` | `res lit(-c)` if `r` is a literal `c` (so `res NEG1` for 1, `res ONE` for -1), else `T T; T r; res T`. |
| `add`, else | `T T; T l; T r; res res; res T` (5 steps; also correct when `res` aliases `l` or `r`). |
| `sub`, `l == res` | `res r`. |
| `sub`, `l` literal `c`, `r` not | `res res; res r; res lit(-c)` (the last one only if `c != 0`). |
| `sub`, else | `res res; T T; T l; res T; res r`. |
| `set_ra w,addr` | `w w; w lit(-addr)` (`w` is the c word of a return instruction). |

**Compare codegen** (`cmp_jf(op,l,r,lf)`: jump to `lf` if false, fall through when true).

- `==`, `!=`: if `l` is a literal and `r` is not, they are swapped. Then:
  - `r == ZERO`: `d = l`, then `jnz d,lf` for `==` and `jz d,lf` for `!=`.
  - `r` a literal `c != 0` and `l` not a literal (in place, `l` is restored; `nk = lit(-c)`), at `p`:
    `l r; l ZERO p+18; T T; T l p+18;` then for `==`: `l nk; jmp p+24; l nk; jmp lf`, for `!=`: `l nk; jmp lf; l nk`.
  - otherwise `diff T1,l,r`, `d = T1`, then `jnz`/`jz`.
- `r == ZERO` with `<`: `l ZERO pc+6; jmp lf`.
- `r == ZERO` with `>=`: `l ZERO lf`.
- `r == ZERO` with `>`: `l ZERO lf; T T; T l pc+12; jmp lf`.
- `r == ZERO` with `<=`: `l ZERO pc+9; T T; T l lf`.
- `r` a literal `c` with `0 < c < INT_MAX` and `l` not a literal. `<=` and `>` become `<` and `>=` with `c+1` (a new pool cell `k`;
  otherwise `k = r`); `nk = lit(-c)`. `l` is changed and restored, at `p`:
  - `<`: `l ZERO p+15; l k p+12; l nk; jmp lf; l nk` (4 steps when false, 1 or 3 when true).
  - `>=`: `l ZERO lf; l k p+12; l nk; jmp p+18; l nk; jmp lf` (4 steps either way, 1 when `l < 0`).
- Otherwise `<` is `lt_jf(l,r,lf)`, `>=` is `lt_jt(l,r,lf)`, `>` is `lt_jf(r,l,lf)`, and `<=` is `lt_jt(r,l,lf)`.
  - `lt_jf(x,y,lf)` is, at `p`: `x ZERO p+27; y ZERO lf; diff T1,x,y; T1 ZERO p+30; jmp lf; y ZERO p+6`, with the true case falling out at `p+30`.
  - `lt_jt(x,y,lt)` is: `x ZERO p+27; y ZERO p+33; diff T1,x,y; T1 ZERO lt; jmp p+33; y ZERO p+6; jmp lt`.
- A comparison used as a value is: `cmp_jf to Lf; clr res; res NEG1; jmp Le; Lf: clr res; Le:`.
  The `if`/`while` condition is parsed with `cond(lf)`, which fuses the first comparison with the branch.
  Otherwise it does `jz value,lf`. A chained comparison materialises the first value.

**Statements.**
- `x = e` is `setto x,e` (nothing when the final operation already wrote `x`).
- `if (c) A else B` is `cond(Lelse); A; jmp Lend; Lelse: B; Lend:`. Without an else, `Lelse` is placed after `A`.
- `while (c) A` is `Ltop: cond(Lend); A; jmp Ltop; Lend:`.
- `return e` is `setto RV,e; jmp RET` where `RET` is the function's return instruction (see "Calls"). The function end is `clr RV; jmp RET`.
- `putc(e)` is `T T; T e; OUT T`, with value ZERO. `getc()` is `T T; T IN; res res; res T`.

**Arrays** (`base`, `size`: compile-time; `w` is a patched word). Elements are stored negated.
- `a[i]` read, `i` a literal `c` with `0 <= c < size`: `res res; res base+c` (2 steps).
- `a[i]` read otherwise, with `p0 = pc`: `T T; T i; w w; w T; w lit(-base); res res; res <patched b>` (7 steps), `w = p0+19`.
- `a[i] = v`, `i` a literal `c` in bounds: `v` is evaluated, then `base+c base+c; base+c v` (2 steps).
- `a[i] = v` otherwise. The pointer is made first, in one of three ways, and `src` is the cell that holds `-pointer`:
  - `v` is a literal, a variable or `-literal` (`simple_value`, no code is emitted for it): `v` is parsed first, then `T T; T i; T lit(base)`; `src = T`.
  - else `pt` = temp `d0`. If `i` is already `pt`: `pt lit(-base)`, then `v` is evaluated, then `T T; T pt`, `src = T`.
  - else `pt pt; pt i; pt lit(base)` (so `pt = -(i+base)`), then `v` is evaluated, `src = pt`.
  - Then, with `p0 = pc`: `w1 w1; w1 src; w2 w2; w2 src; w3 w3; w3 src; <0 0>; <0 v>`, where `w1 = p0+18`, `w2 = p0+19`, `w3 = p0+21`.
    (`<0 0>` clears the element, `<0 v>` subtracts `v`: the element becomes `-v`.)

**Calls** `f(e1..ek)`. A function `f` starts with its **return instruction** `NEG1 ZERO 0` at address `r`; `RA_f = r+2` is its `c` word,
and the entry (the first instruction of the body) is `r+3`. A caller patches `RA_f` with the return address, and every return jumps to `r`.
1. Each arg is evaluated left to right into an operand. For a non-self call each argument whose `cz` is 0 is computed straight into the
   callee's parameter cell (see "Destination"). An argument operand is copied into a fresh temp when it is a global scalar and `cz[tp] != 0`
   after the argument, or, for a self-call, when it is the parameter cell of an earlier parameter (that `mov` would clobber it).
2. Self-calls only: push `S = [RA_f] + live params and locals + temps 0..d0-1`.
   - A param or local is live when its name occurs as a name token anywhere in tokens `s0 .. fend-1`, where `fend` is the closing brace
     of the function, and `s0` is the first token of the current statement, or of the outermost enclosing `while` if there is one
     (an operand cell may be pending, and a loop runs again). If `fend - s0 > 4000` every param and local is saved. The order is the
     declaration order.
   - The push is `T T; T SP`.
   - Then, per cell `c`: `w1 w1; w1 T; w2 w2; w2 T; w3 w3; w3 T; <0 0>; <0 c>; T NEG1` with `w1 = b+18`, `w2 = b+19`, `w3 = b+21`,
     `b` the start of the cell's block (9 steps; the stored value is `-c`).
   - Finally `SP lit(m)`.
3. `setto param_i, arg_i` for each `i` (nothing if the argument is already in the parameter cell).
4. `set_ra RA_f, pc+9` (the address just after the jump), then `jmp entry`.
5. Self-calls only: pop. This is `SP lit(-m); T T; T SP`, then per cell: `w w; w T; c c; c <0>; T NEG1`, with `w = b+10`, in the same cell order as the push (5 steps per cell).
6. For a call statement nothing more. Otherwise `depth = d0+1; mov res, RV` with `res` = `result_cell` (temp `d0` or the destination).

**Multiplication.**
- If either operand is a literal `c`, it is inlined as Horner over the bits of `m = |c|`, `nb` = bit length.
  - `c == 0` is `clr res`.
  - `res != x`, `nb == 1`: `mov res,x`, or for `c = -1` `res res; res x`.
  - `res != x`, `nb >= 2`: `k = U` after `U U; U x` (`U = -x`), or for `c < 0` `k = x` (the product is built negated: no final negation).
    Then `clr res`, `res k` repeated `m >> (nb-2)` (2 or 3) times, and for `bit = nb-3 .. 0`: `T T; T res; res T` (`res += res`), and `res k` if the bit is set.
  - `res == x`: `U U; U x` if `m` has more than one set bit, then for `bit = nb-2 .. 0`: `res += res` and `res U` if the bit is set.
    If `c < 0`, `res` is then negated in place (`T T; U U; T res; U T; res res; res U`).
- Otherwise: `mov MA,l; mov MB,r; set_ra MRA,pc+9; jmp LMUL; mov res,MR`, with `MRA = 8`.
  - The routine is emitted once, after the last function. The first code instruction (address 6, always present) is its return instruction
    `NEG1 ZERO 0`; `MRA = 8` is its patched `c` word, and the routine ends with `jmp 6`.
  - Pool: first `mp[j] = lit(2^j)` for `j = 0..30`, then `mn[j] = lit(-2^j)` (labels: `lneg, ldisp, lmin, ld30`, then `lent[0..30]`, then one `lsm[e]` per level).
  - Sign fix: `MB < 0` goes to `lneg`: `MB = -MB` (`U U; U MB; mov MB,U`), `mov MNA,MA` (so `MR -= MNA` adds `-MA`), and if `MB` is still negative (INT_MIN)
    `MB += 2^31` (twice `MB mn[30]`), `MR -= MNA` and a jump to the doubling of bit 30. Otherwise `MNA = -MA`.
  - Dispatch on the magnitude of `MB` (0..2^31-1): for `e` in 3, 6, 10, 14, 18, 22, 26, 30: `MB mp[e] -> lsm[e]; MB mn[e]` (subtract 2^e; if
    negative `MB < 2^e`: restore and enter the bit tests at bit `e-1`); after the last level `jmp lent[30]`. For `e = 3` the entry is a
    repeated addition (`MB ONE ret; MR MNA; jmp`).
  - Bit test of bit `j`, preceded (except at the entry) by `MR += MR` (3 steps): `MB mp[j] -> +18; MR MNA; jmp +21; MB mn[j]` (subtract 2^j; if negative
    the bit is clear and it is added back; else add the multiplicand). About 5.5 steps per bit, 31 blocks from bit 30 down to bit 0.
  - Exact code: see `emit_mul_routine`.

**Program end.**
- After the last function, `LMUL` code (if used).
- `LBOOT: set_ra RA_main, pc+9; jmp main_entry; NEG1 ZERO -1`.
- Then the pool.

**Verification status.** The corpus and fuzz results are recorded in `tier2/BUILD.md`.
