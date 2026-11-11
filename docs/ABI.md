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
