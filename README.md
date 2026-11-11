# low: programs for a one-instruction machine

The machine has one instruction, `a b c`: `mem[a] -= mem[b]; if mem[a] < 0 goto c`, with 2^20 signed 32-bit words,
`IN`/`OUT` at the top two addresses. This repo contains hand-built programs, a compiler for a small language
(v1) written in Python, and the same compiler written in v1 itself, which compiles itself on the machine.

## How the bootstrap works
    tier3.v1 --(v1c.py, Python)--> C1 --(runs on the machine, compiles tier3.v1)--> C2 --(same)--> C3
    required: C1 == C2 == C3, byte for byte; and v1c(tier3.v1) == C2 (diverse double-compiling check)

## Build and run
Needs a C compiler, Python 3, and (for the viewer tests only) Node. Docker is only needed for the official-VM check.

    make -C vm                                        # builds vm/sqvm (our exact clone of the official VM)
    python3 tier2/v1c.py < prog.v1 > prog.txt         # compile v1 -> machine program (one word per line)
    vm/sqvm prog.txt < input.txt                      # run (SQ_LIMIT=n sets the step limit, VM_STATS=1 prints steps)
    vm/sqvm --trace t.trace prog.txt < input.txt      # also write an execution trace for tools/viewer.html

Compile with the tier-3 compiler (the compiler running on the machine):

    python3 tier2/v1c.py < tier3/tier3.v1 > c1.txt
    SQ_LIMIT=10000000000 vm/sqvm c1.txt < prog.v1 > prog.txt

## Layout
| Path | What |
|---|---|
| `vm/sqvm.c` | VM clone with trace mode (trace format documented in the header comment) |
| `toolkit/sqasm.py`, `sqdis.py` | macro assembler and disassembler used for tier 1 |
| `toolkit/superopt.py`, `superopt_c.c` | bounded exhaustive superoptimizer for the machine (checks the codegen idioms; results in `docs/SUPEROPT.md`) |
| `tier1/` | `echo.txt`, `reverse.txt`, `bigmul.txt`, `hanoi.txt` (+ sources: `*.sqa.py`, `*.v1`) |
| `tier2/v1c.py` | v1 to machine compiler (the executable specification), `BUILD.md` |
| `tier3/tier3.v1` | the compiler written in v1, `NOTES.md`; `tier3.selfcompiled.txt` is its self-compiled binary (DDC candidate) |
| `ref/v1interp.py` | strict reference interpreter for v1 |
| `tests/` | corpus (`tests/corpus`, valid + `invalid*`), fuzz generator, VM/assembler/viewer/DDC tests |
| `scripts/` | `bootstrap.sh`, `ddc.sh`, `tier3_diff.sh`, `tier3_fuzz.sh`, `difftest.sh`, `official_check.sh`, `official_invalid.sh`, `official_stress.py`, `make_demo_trace.sh` |
| `tools/viewer.html` | single-file trace viewer (no network), demo source in `tools/demo/` |
| `docs/` | `ABI.md` (machine conventions + codegen spec), `DESIGN_LOG.md`, `PERF.md`, `SUPEROPT.md` (superoptimizer results) |

## Running the tests
    make test          # everything quick, about 1.5 minutes: VM and assembler tests, tier1, corpus (interpreter and compiled),
                       # tier3 vs v1c on the corpus, 40 fuzz programs, viewer tests, DDC and DDC-sensitivity
    make bootstrap     # scripts/bootstrap.sh: C1 = v1c(tier3), corpus + 60 fuzz through C1, C2 = C1(tier3), C3 = C2(tier3), C2 == C3
    make ddc           # scripts/ddc.sh [candidate.txt]: Diverse Double-Compiling check, prints MATCH / MISMATCH
    make full          # test + bootstrap + 100 tier3 fuzz + 300 v1c fuzz programs
    scripts/official_check.sh     # same programs on the organisers' VM binaries via Docker (amd64)

Individual pieces: `python3 tests/test_vm.py`, `python3 tests/test_sqasm.py`, `tests/check_tier1.sh` (the four tier-1 files exist
and pass), `tests/run_corpus_compiled.sh`, `tests/run_corpus_interp.sh`, `tests/test_ddc_detects_divergence.sh`,
`node tests/test_viewer_parse.js`, `node tests/test_viewer_dom.js` (these two need `make demo` first, which `make test` does).

## Trace viewer
`make demo` writes `build/demo/demo.trace` and `demo.txt`. Open `tools/viewer.html` in a browser, choose the trace (and
optionally the program for original values). Heatmap of the code over time, scrubber/play, current instruction in yellow,
words patched by running code (pointer access, return addresses) in magenta.

## Results
Run on Apple M1, with the official binaries through Docker (amd64).

| Check | Result |
|---|---|
| Tier 1: echo, reverse, bigmul, hanoi | pass on `sqvm` and the official `VM_tier1and2` |
| Echo and reverse stress | empty, 1 byte, newline only, random inputs up to 5000 bytes: expected output on the official VM |
| Tier 2 corpus | 54 valid programs match their expected output on both VMs; 83 invalid programs rejected |
| Fuzzing | thousands of random programs match the reference interpreter |
| Tier 3 vs Tier 2 | `tier3.v1` and `v1c.py` emit byte-identical output on the corpus and on fuzz programs |
| Bootstrap | C1 == C2 == C3 on `sqvm` and on the official `VM_tier3`: 54,167,125 steps and 38,627 words per self-compile (limit 10^10) |
| Invalid programs on the self-hosted compiler | all rejected by the compiler running on `VM_tier3` |
| DDC | `scripts/ddc.sh` reports MATCH for the shipped binary and MISMATCH for a modified compiler |
| Speed | slowest corpus program 37M of the 100M allowed steps ([PERF.md](docs/PERF.md)) |
| Trace viewer | demo trace loads in Chrome: heatmap, current instruction, patched words, write log |
