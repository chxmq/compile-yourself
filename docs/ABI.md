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
| 6..code_end-1 | Code. |
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
   - For `func`: `RA`, then the parameters in order, then each `var` at its declaration, and temporaries at first use.
3. Multiply-routine cells `MA, MB, MR, MC, MNA, MRA` at first use (in that order) of a non-constant `*`.

**Labels.** Numbered from 0 in creation order. Label 0 is `LBOOT`, created before the first declaration.
Each `if` creates a label for else/end, and `while` creates 2 labels. The comparison value code creates 2 per comparison.

**Temporaries.** `depth` counts reserved temps. Temp `i` is a function-local cell, created at first need.

An expression returns an operand cell:
- a variable cell,
- a literal cell,
- or temp `d0` (where `d0` is the depth at entry).

A binary operator does the following:
1. Parse `L`. Reserve temp `d0` (`depth = d0+1`).
2. If `L` is a global scalar, copy it into temp `d0` (a call in `R` could change it).
3. Parse `R`.
4. Write the result to temp `d0`, then set `depth = d0+1`.

The result cell never aliases `R`. Each statement resets `depth` to 0. The unary minus of a literal is folded into the literal.

**Sequences** (`T`, `U`, `T1` are the scratch cells; every sequence clears them itself):

| Name | Sequence |
|---|---|
| `mov x,y` | `x x; T T; T y; x T` (nothing if `x == y`). |
| `jmp l` | `NEG1 ZERO l`. |
| `jnz d,l` | `d ZERO l; T T; T d l`. |
| `jz d,l` | `d ZERO pc+12; T T; T d pc+12; jmp l`. |
| `diff t,x,y` (t = x-y) | `t t; T T; T x; t T; t y`. |
| `add res,l,r`, `l == res` | `T T; T r; res T`. If `r` is ONE it is `res NEG1`; if NEG1, `res ONE`. |
| `add`, else | `res res; T T; T r; res T; T T; T l; res T`. |
| `sub`, `l == res` | `res r`. |
| `sub`, else | `res res; T T; T l; res T; res r`. |
| `jmpi cell` | `T T; T cell; w w; w T; NEG1 ZERO 0`, where `w` is the address of the last instruction's `c` word, i.e. `p+14`. |
| `set_ra ra,addr` | `ra ra; ra lit(-addr)`. |

**Compare codegen** (`cmp_jf(op,l,r,lf)`: jump to `lf` if false, fall through when true).
Only the `r == ZERO` case is special-cased, plus `==`/`!=`, which swap if `l == ZERO`.

- `==`, `!=`: use `d = l` if `r == ZERO`, else `diff T1,l,r` and `d = T1`. Then `jnz d,lf` for `==` and `jz d,lf` for `!=`.
- `r == ZERO` with `<`: `l ZERO pc+6; jmp lf`.
- `r == ZERO` with `>=`: `l ZERO lf`.
- `r == ZERO` with `>`: `l ZERO lf; T T; T l pc+12; jmp lf`.
- `r == ZERO` with `<=`: `l ZERO pc+9; T T; T l lf`.
- Otherwise `<` is `lt_jf(l,r,lf)`, `>=` is `lt_jt(l,r,lf)`, `>` is `lt_jf(r,l,lf)`, and `<=` is `lt_jt(r,l,lf)`.
  - `lt_jf(x,y,lf)` is, at `p`: `x ZERO p+27; y ZERO lf; diff T1,x,y; T1 ZERO p+30; jmp lf; y ZERO p+6`, with the true case falling out at `p+30`.
  - `lt_jt(x,y,lt)` is: `x ZERO p+27; y ZERO p+33; diff T1,x,y; T1 ZERO lt; jmp p+33; y ZERO p+6; jmp lt`.
- A comparison used as a value is: `cmp_jf to Lf; clr res; res NEG1; jmp Le; Lf: clr res; Le:`.
  The `if`/`while` condition is parsed with `cond(lf)`, which fuses the first comparison with the branch.
  Otherwise it does `jz value,lf`. A chained comparison materialises the first value.

**Statements.**
- `x = e` is `clr x` if `e` is ZERO, else `mov x,e`.
- `if (c) A else B` is `cond(Lelse); A; jmp Lend; Lelse: B; Lend:`. Without an else, `Lelse` is placed after `A`.
- `while (c) A` is `Ltop: cond(Lend); A; jmp Ltop; Lend:`.
- `return e` is `mov RV,e; jmpi RA`. The function end is `clr RV; jmpi RA`.
- `putc(e)` is `T T; T e; OUT T`, with value ZERO. `getc()` is `T T; T IN; res res; res T`.

**Arrays** (`base` is a compile-time absolute address; `w` is the patched word of the `T` instruction; 9 steps):
- `a[i]` read, with `p0 = pc`: `T T; T i; w w; w T; w lit(-base); T T; T <patched b>; res res; res T`. Here `w = p0+19`.
- `a[i] = v`: first `pt` = temp `d0` is computed after the index and before `v`.
  - If `i` is already `pt`: `pt lit(-base)`.
  - Otherwise: `pt pt; T T; T i; pt T; pt lit(-base)`.
  - Then `v` is evaluated.
  - Then, with `p0 = pc`: `T T; T pt; w1 w1; w1 T; w2 w2; w2 T; w3 w3; w3 T; U U; U v; <0 0>; <0 U>`.
  - Here `w1 = p0+30`, `w2 = p0+31`, `w3 = p0+33` (12 steps).

**Calls** `f(e1..ek)`:
1. Each arg is evaluated left to right into an operand. Self-calls copy every non-stable arg operand into a fresh temp.
   Non-self calls copy only global scalars into a temp. Stable means a literal or a temp.
2. Self-calls only: push `S = [RA] + params + locals declared so far + temps 0..d0-1`.
   - The push is `T T; T SP`.
   - Then, per cell `c`: `w1 w1; w1 T; w2 w2; w2 T; w3 w3; w3 T; U U; U c; <0 0>; <0 U>; T NEG1`, with `w1 = b+24`, `w2 = b+25`, `w3 = b+27` and `b` the start of the cell's block.
   - Finally `SP lit(m)`.
3. `mov param_i, arg_i` for each `i`.
4. `set_ra RA_f, pc+9` (that is the address just after the jump), then `jmp entry`.
5. Self-calls only: pop. This is `SP lit(-m); T T; T SP`, then per cell: `w w; w T; U U; <U 0>; c c; c U; T NEG1`, with `w = b+10`, in the same cell order as the push.
6. `depth = d0+1; mov res(temp d0), RV`.

**Multiplication.**
- If either operand is a literal `c`, it is inlined as Horner over the bits of `m = |c|`.
  - `c == 0` is `clr res`.
  - If `m` has more than one set bit, `U = -x` first (`U U; U x`).
  - `mov res,x` if `res != x`.
  - For `bit = nb-2 .. 0`: `T T; T res; res T` (that is, `res += res`), and if the bit is set, `res U`.
  - If `c < 0`, `res` is then negated in place (`T T; U U; T res; U T; res res; res U`).
- Otherwise: `mov MA,l; mov MB,r; set_ra MRA,pc+9; jmp LMUL; mov res,MR`.
  - The routine is emitted once, after the last function.
  - If `MB` or `MA` is in 0..23, it is the multiplier of a repeated addition (swapping if needed).
  - Otherwise it is MSB-first shift-and-add for 32 bits.
  - Exact code: see `emit_mul_routine`.

**Program end.**
- After the last function, `LMUL` code (if used).
- `LBOOT: set_ra RA_main, pc+9; jmp main_entry; NEG1 ZERO -1`.
- Then the pool.

**Verification status.** The corpus and fuzz results are recorded in `tier2/BUILD.md`.
