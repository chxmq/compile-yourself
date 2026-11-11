/*
 * sqvm: exact clone of the official "subtract and branch if negative" VM.
 *
 *   usage: sqvm [--trace FILE | --profile FILE] program.txt < input.txt
 *   env:   SQ_LIMIT   step limit (default 100000000)
 *          VM_STATS   if set, print "halted after N steps" to stderr on normal halt
 *
 * Behaviour (messages, exit codes, evaluation order) mirrors the official
 * binary, whose disassembly settled the corner cases:
 *   - a, b, c are all fetched before anything is written;
 *   - a or b outside 0..N-1 -> "error at PC: operand outside memory (a b c)", exit 1;
 *   - then a == IN -> "error at PC: IN used as first operand (a b c)", exit 1;
 *   - b == IN consumes one byte (or yields -1 at EOF);
 *   - a == OUT prints low 8 bits of r and stores nothing (OUT always reads 0);
 *   - the step limit is checked before a step while pc is valid:
 *     "timeout after N steps", exit 2;
 *   - loader: whitespace separated decimals, each must fit int32, at most
 *     N-2 words.
 *
 * Trace file format (--trace), little-endian uint32 stream:
 *   header: magic 0x31545153 ("SQT1"), code_words (= words loaded from file)
 *   record: x with (x & 1) == 0 : executed instruction at pc = x >> 1
 *           x with (x & 1) == 1 : write to code-region cell addr = x >> 1,
 *                                 followed by one uint32 word = new value
 *   A write is recorded only when addr < code_words.  pc and addr are < 2^20.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define N 1048576
#define IN_ADDR (N - 2)
#define OUT_ADDR (N - 1)

static int32_t mem[N];

static unsigned char inbuf[1 << 16];
static size_t in_pos = 0, in_len = 0;
static int in_eof = 0;
static int read_in(void) {
    if (in_pos == in_len) {
        if (in_eof) return -1;
        in_len = fread(inbuf, 1, sizeof inbuf, stdin);
        in_pos = 0;
        if (in_len == 0) { in_eof = 1; return -1; }
    }
    return inbuf[in_pos++];
}

static FILE *tf = NULL;
static uint32_t tbuf[1 << 16];
static size_t tn = 0;
static inline void tput(uint32_t x) {
    tbuf[tn++] = x;
    if (tn == sizeof tbuf / sizeof tbuf[0]) { fwrite(tbuf, 4, tn, tf); tn = 0; }
}

static int load_program(const char *path, int *nwords_out) {
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "can't open %s\n", path); return -1; }
    int n = 0;
    for (;;) {
        long v;
        int rc = fscanf(f, "%ld", &v);
        if (rc == EOF) break;
        if (rc != 1) { fprintf(stderr, "bad token in program file after %d words\n", n); fclose(f); return -1; }
        if (v < INT32_MIN || v > INT32_MAX) { fprintf(stderr, "word %d in program file doesn't fit in 32 bits\n", n); fclose(f); return -1; }
        if (n > N - 3) { fprintf(stderr, "program doesn't fit in memory (max %d words)\n", N - 2); fclose(f); return -1; }
        mem[n++] = (int32_t)v;
    }
    fclose(f);
    *nwords_out = n;
    return 0;
}

#define ERR_RANGE(pc, a, b, c) fprintf(stderr, "error at %d: operand outside memory (%d %d %d)\n", pc, a, b, c)
#define ERR_IN(pc, a, b, c) fprintf(stderr, "error at %d: IN used as first operand (%d %d %d)\n", pc, a, b, c)

/* returns 0 halted, 1 error, 2 timeout */
static int run_plain(long limit, long *psteps) {
    int32_t pc = 0;
    long steps = 0;
    int rc = 0;
    for (;;) {
        if ((uint32_t)pc > (uint32_t)(N - 3)) break;
        if (steps == limit) { rc = 2; break; }
        steps++;
        int32_t a = mem[pc], b = mem[pc + 1], c = mem[pc + 2];
        if (__builtin_expect((uint32_t)a >= (uint32_t)(N - 2) || (uint32_t)b >= (uint32_t)(N - 2), 0)) {
            if ((uint32_t)a >= (uint32_t)N || (uint32_t)b >= (uint32_t)N) { ERR_RANGE(pc, a, b, c); rc = 1; break; }
            if (a == IN_ADDR) { ERR_IN(pc, a, b, c); rc = 1; break; }
            int32_t vb = (b == IN_ADDR) ? (int32_t)read_in() : mem[b];
            int32_t r = (int32_t)((uint32_t)mem[a] - (uint32_t)vb);
            if (a == OUT_ADDR) putchar_unlocked((unsigned char)(r & 0xff));
            else mem[a] = r;
            pc = (r < 0) ? c : pc + 3;
        } else {
            int32_t r = (int32_t)((uint32_t)mem[a] - (uint32_t)mem[b]);
            mem[a] = r;
            pc = (r < 0) ? c : pc + 3;
        }
    }
    *psteps = steps;
    return rc;
}

static int run_trace(long limit, long *psteps, uint32_t ncode) {
    int32_t pc = 0;
    long steps = 0;
    int rc = 0;
    for (;;) {
        if ((uint32_t)pc > (uint32_t)(N - 3)) break;
        if (steps == limit) { rc = 2; break; }
        steps++;
        int32_t a = mem[pc], b = mem[pc + 1], c = mem[pc + 2];
        tput((uint32_t)pc << 1);
        if ((uint32_t)a >= (uint32_t)N || (uint32_t)b >= (uint32_t)N) { ERR_RANGE(pc, a, b, c); rc = 1; break; }
        if (a == IN_ADDR) { ERR_IN(pc, a, b, c); rc = 1; break; }
        int32_t vb = (b == IN_ADDR) ? (int32_t)read_in() : mem[b];
        int32_t r = (int32_t)((uint32_t)mem[a] - (uint32_t)vb);
        if (a == OUT_ADDR) putchar_unlocked((unsigned char)(r & 0xff));
        else {
            mem[a] = r;
            if ((uint32_t)a < ncode) { tput(((uint32_t)a << 1) | 1); tput((uint32_t)r); }
        }
        pc = (r < 0) ? c : pc + 3;
    }
    *psteps = steps;
    return rc;
}

/* per-pc hit counts: --profile FILE writes "pc count" lines for every executed instruction */
static unsigned long long *prof_hits;
static int run_prof(long limit, long *psteps) {
    int32_t pc = 0;
    long steps = 0;
    int rc = 0;
    for (;;) {
        if ((uint32_t)pc > (uint32_t)(N - 3)) break;
        if (steps == limit) { rc = 2; break; }
        steps++;
        prof_hits[pc / 3]++;
        int32_t a = mem[pc], b = mem[pc + 1], c = mem[pc + 2];
        if ((uint32_t)a >= (uint32_t)N || (uint32_t)b >= (uint32_t)N) { ERR_RANGE(pc, a, b, c); rc = 1; break; }
        if (a == IN_ADDR) { ERR_IN(pc, a, b, c); rc = 1; break; }
        int32_t vb = (b == IN_ADDR) ? (int32_t)read_in() : mem[b];
        int32_t r = (int32_t)((uint32_t)mem[a] - (uint32_t)vb);
        if (a == OUT_ADDR) putchar_unlocked((unsigned char)(r & 0xff));
        else mem[a] = r;
        pc = (r < 0) ? c : pc + 3;
    }
    *psteps = steps;
    return rc;
}

int main(int argc, char **argv) {
    const char *tracefile = NULL, *prog = NULL, *proffile = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--trace") && i + 1 < argc) tracefile = argv[++i];
        else if (!strcmp(argv[i], "--profile") && i + 1 < argc) proffile = argv[++i];
        else if (!prog) prog = argv[i];
    }
    if (!prog) { fprintf(stderr, "usage: %s [--trace FILE] program.txt < input.txt\n", argv[0]); return 1; }
    int nwords = 0;
    if (load_program(prog, &nwords) < 0) return 1;

    long limit = 100000000L;
    const char *lim = getenv("SQ_LIMIT");
    if (lim && *lim) limit = atol(lim);

    long steps = 0;
    int rc;
    if (proffile) {
        prof_hits = calloc(N / 3 + 1, sizeof *prof_hits);
        rc = run_prof(limit, &steps);
        FILE *pf = fopen(proffile, "w");
        if (pf) {
            for (int i = 0; i < N / 3; i++) if (prof_hits[i]) fprintf(pf, "%d %llu\n", i * 3, prof_hits[i]);
            fclose(pf);
        }
    } else if (tracefile) {
        tf = fopen(tracefile, "wb");
        if (!tf) { fprintf(stderr, "can't open %s\n", tracefile); return 1; }
        uint32_t hdr[2] = { 0x31545153u, (uint32_t)nwords };
        fwrite(hdr, 4, 2, tf);
        rc = run_trace(limit, &steps, (uint32_t)nwords);
        fwrite(tbuf, 4, tn, tf);
        fclose(tf);
    } else {
        rc = run_plain(limit, &steps);
    }
    fflush(stdout);
    if (rc == 2) fprintf(stderr, "timeout after %ld steps\n", steps);
    else if (rc == 0 && getenv("VM_STATS")) fprintf(stderr, "halted after %ld steps\n", steps);
    return rc;
}
