/*
 * superopt_c: C core of toolkit/superopt.py -- bounded exhaustive search over
 * straight-line / short branching programs for the subleq-style machine
 *     mem[a] -= mem[b]; if (mem[a] < 0) pc = c else pc += 1      (instruction index)
 * Inspired by Massalin, "Superoptimizer: A Look at the Smallest Program" (ASPLOS 1987).
 *
 * Cell model (indices): 0=X 1=Y 2=T 3=U (writable, arbitrary initial T,U), 4=ZERO 5=ONE 6=NEG1 (read-only).
 * An instruction writes only to a cell in the spec's writable set; constants may appear only as b,
 * except the no-op write `NEG1 -= ZERO` (the idiom for an unconditional jump).
 * c is an instruction index in pc+1..L (L = fall-through exit F) or L+1 (jump-out exit J).
 *
 * usage: superopt_c SPEC K WMASK K BRANCH NTHREADS     (see main)
 * Output: one line per length L with at least one solution:
 *   L <len> count <n> steps <worst> wF <worstF> wJ <worstJ> seq <text>
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>

#define MAXL 9
#define MAXV 20000
typedef struct { uint8_t a, b; int8_t c; } Ins;
typedef struct { int32_t init[4], exp[4]; int cmask, ex; } Vec;

static Vec vecs[MAXV];
static int nvec;
static int spec, kparam, wmask, maxk, branch, nthreads;
static uint8_t pa[64], pb[64];
static int npairs;

enum { CLEAR, MOV, NEG, NEGTO, ADD, SUB, DBL, ADD3, SUB3, CONST, JMP,
       JLT0, JGE0, JGT0, JLE0, JEQ0, JNE0, JEQ_XY, JNE_XY, JLT_XY, JGE_XY };

static int32_t wr(int64_t v) { return (int32_t)(uint32_t)(uint64_t)v; }

static void spec_eval(int32_t x, int32_t y, Vec *v)
{
    int32_t *e = v->exp;
    e[0] = x; e[1] = y; e[2] = 0; e[3] = 0;
    v->ex = 0;
    switch (spec) {
    case CLEAR: e[0] = 0; v->cmask = 1; break;
    case MOV: e[0] = y; v->cmask = 3; break;
    case NEG: e[0] = wr(-(int64_t)x); v->cmask = 1; break;
    case NEGTO: e[0] = wr(-(int64_t)y); v->cmask = 3; break;
    case ADD: e[0] = wr((int64_t)x + y); v->cmask = 3; break;
    case SUB: e[0] = wr((int64_t)x - y); v->cmask = 3; break;
    case DBL: e[0] = wr(2 * (int64_t)x); v->cmask = 1; break;
    case ADD3: e[3] = wr((int64_t)x + y); v->cmask = 11; break;
    case SUB3: e[3] = wr((int64_t)x - y); v->cmask = 11; break;
    case CONST: e[0] = kparam; v->cmask = 1; break;
    case JMP: v->cmask = 1; v->ex = 1; break;
    case JLT0: v->cmask = 1; v->ex = x < 0; break;
    case JGE0: v->cmask = 1; v->ex = x >= 0; break;
    case JGT0: v->cmask = 1; v->ex = x > 0; break;
    case JLE0: v->cmask = 1; v->ex = x <= 0; break;
    case JEQ0: v->cmask = 1; v->ex = x == 0; break;
    case JNE0: v->cmask = 1; v->ex = x != 0; break;
    case JEQ_XY: v->cmask = 3; v->ex = x == y; break;
    case JNE_XY: v->cmask = 3; v->ex = x != y; break;
    case JLT_XY: v->cmask = 3; v->ex = x < y; break;
    case JGE_XY: v->cmask = 3; v->ex = x >= y; break;
    }
}

static uint64_t rs = 88172645463325252ULL;
static uint32_t rnd(void) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return (uint32_t)(rs >> 16); }

static void build_vectors(void)
{
    int32_t V[256]; int n = 0;
    int32_t base[] = { 0, 1, -1, 2, -2, 3, -3, 5, -5, 100, -100, 12345, -12345,
                       INT32_MIN, INT32_MIN + 1, INT32_MIN + 2, INT32_MAX, INT32_MAX - 1 };
    for (size_t i = 0; i < sizeof base / sizeof *base; i++) V[n++] = base[i];
    int ks[] = { 2, 3, 4, 7, 8, 15, 16, 29, 30 };
    for (size_t i = 0; i < sizeof ks / sizeof *ks; i++) { V[n++] = 1 << ks[i]; V[n++] = -(1 << ks[i]); V[n++] = (1 << ks[i]) - 1; }
    for (int i = 0; i < 6; i++) V[n++] = (int32_t)rnd();
    int usey = (wmask & 2) != 0;
    nvec = 0;
    for (int i = 0; i < n; i++)
        for (int j = 0; j < (usey ? n : 1); j++)
            for (int s = 0; s < 2; s++) {
                if (nvec >= MAXV) break;
                Vec *v = &vecs[nvec++];
                v->init[0] = V[i]; v->init[1] = usey ? V[j] : 0;
                if (s == 0) { v->init[2] = (int32_t)rnd(); v->init[3] = (int32_t)rnd(); }
                else { v->init[2] = (rnd() & 1) ? INT32_MIN : INT32_MAX; v->init[3] = (rnd() & 1) ? 0 : -7; }
                spec_eval(V[i], v->init[1], v);
            }
    for (int i = nvec - 1; i > 0; i--) { int j = rnd() % (i + 1); Vec t = vecs[i]; vecs[i] = vecs[j]; vecs[j] = t; }
}

typedef struct { Ins p[MAXL]; int L; int64_t count; int64_t bestkey, bestsum; int bw, bwf, bwj, bc[3], sc[3], sw, swf, swj; Ins best[MAXL], bests[MAXL]; } Ctx;

static inline int run_one(const Ins *p, int L, const Vec *v, int *steps)
{
    int32_t m[7];
    m[0] = v->init[0]; m[1] = v->init[1]; m[2] = v->init[2]; m[3] = v->init[3];
    m[4] = 0; m[5] = 1; m[6] = -1;
    int pc = 0, s = 0;
    while (pc < L) {
        const Ins *i = &p[pc];
        int32_t r = (int32_t)((uint32_t)m[i->a] - (uint32_t)m[i->b]);
        m[i->a] = r; s++;
        pc = r < 0 ? i->c : pc + 1;
    }
    *steps = s;
    int ex = pc - L;
    if (ex != v->ex) return 0;
    for (int k = 0; k < 4; k++) if ((v->cmask >> k & 1) && m[k] != v->exp[k]) return 0;
    return 1;
}

static void leaf(Ctx *cx)
{
    int st;
    for (int i = 0; i < nvec; i++) if (!run_one(cx->p, cx->L, &vecs[i], &st)) return;
    int wf = 0, wj = 0, wc[3] = { 0, 0, 0 };
    for (int i = 0; i < nvec; i++) {
        run_one(cx->p, cx->L, &vecs[i], &st);
        if (vecs[i].ex) { if (st > wj) wj = st; } else if (st > wf) wf = st;
        int32_t x = vecs[i].init[0];
        int cl = x < 0 ? 0 : (x == 0 ? 1 : 2);
        if (st > wc[cl]) wc[cl] = st;
    }
    int w = wf > wj ? wf : wj;
    int64_t key = (int64_t)w * 1000 + wf + wj;
    int64_t sum = (int64_t)(wc[0] + wc[1] + wc[2]) * 1000 + w;
    cx->count++;
    if (cx->bestkey < 0 || key < cx->bestkey) {
        cx->bestkey = key; cx->bw = w; cx->bwf = wf; cx->bwj = wj;
        memcpy(cx->bc, wc, sizeof wc);
        memcpy(cx->best, cx->p, sizeof cx->p);
    }
    if (cx->bestsum < 0 || sum < cx->bestsum) {
        cx->bestsum = sum; cx->sw = w; cx->swf = wf; cx->swj = wj;
        memcpy(cx->sc, wc, sizeof wc);
        memcpy(cx->bests, cx->p, sizeof cx->p);
    }
}

static void dfs(Ctx *cx, int pos)
{
    int L = cx->L;
    if (pos == L) { leaf(cx); return; }
    for (int q = 0; q < npairs; q++) {
        int a = pa[q], b = pb[q];
        int clo = pos + 1, chi = branch ? L + 1 : pos + 1;
        if (a == 6) { if (!branch) continue; clo = pos + 2; if (clo > chi) continue; }   /* NEG1-=ZERO always branches */
        if (a == b && a < 4) chi = clo;                    /* result is 0: never branches */
        for (int c = clo; c <= chi; c++) {
            cx->p[pos].a = a; cx->p[pos].b = b; cx->p[pos].c = c;
            dfs(cx, pos + 1);
        }
    }
}

static const char *cn = "XYTUZON";
static void print_seq(const Ins *p, int L, char *out)
{
    out[0] = 0;
    for (int i = 0; i < L; i++) {
        char t[32];
        if (p[i].c == i + 1) sprintf(t, "%c %c", cn[p[i].a], cn[p[i].b]);
        else if (p[i].c == L + 1) sprintf(t, "%c %c>J", cn[p[i].a], cn[p[i].b]);
        else sprintf(t, "%c %c>%d", cn[p[i].a], cn[p[i].b], p[i].c);
        if (i) strcat(out, ";");
        strcat(out, t);
    }
}

static int curL;
static volatile int next_top;
static int ntop;
static struct { uint8_t q; int8_t c; } top[4096];
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static Ctx results[64];

static void *worker(void *arg)
{
    Ctx *cx = (Ctx *)arg;
    for (;;) {
        pthread_mutex_lock(&mu);
        int t = next_top++;
        pthread_mutex_unlock(&mu);
        if (t >= ntop) break;
        int q = top[t].q;
        cx->p[0].a = pa[q]; cx->p[0].b = pb[q]; cx->p[0].c = top[t].c;
        dfs(cx, 1);
    }
    return NULL;
}

int main(int argc, char **argv)
{
    if (argc < 7) { fprintf(stderr, "usage: superopt_c SPEC KPARAM WMASK MAXK BRANCH NTHREADS\n"); return 2; }
    spec = atoi(argv[1]); kparam = atoi(argv[2]); wmask = atoi(argv[3]);
    maxk = atoi(argv[4]); branch = atoi(argv[5]); nthreads = atoi(argv[6]);
    if (maxk > MAXL) maxk = MAXL;
    build_vectors();
    npairs = 0;
    for (int a = 0; a < 4; a++) {
        if (!(wmask >> a & 1)) continue;
        for (int b = 0; b < 7; b++) {
            if (b < 4 && !(wmask >> b & 1)) continue;
            pa[npairs] = a; pb[npairs] = b; npairs++;
        }
    }
    pa[npairs] = 6; pb[npairs] = 4; npairs++;
    for (int L = 1; L <= maxk; L++) {
        curL = L; next_top = 0; ntop = 0;
        int chi = branch ? L + 1 : 1;
        for (int q = 0; q < npairs; q++) {
            int clo = 1;
            if (pa[q] == 6) { if (!branch) continue; clo = 2; }
            int chq = (pa[q] == pb[q] && pa[q] < 4) ? clo : chi;
            for (int c = clo; c <= chq; c++) { top[ntop].q = q; top[ntop].c = c; ntop++; }
        }
        pthread_t th[64];
        if (nthreads > 64) nthreads = 64;
        for (int t = 0; t < nthreads; t++) {
            memset(&results[t], 0, sizeof results[t]);
            results[t].L = L; results[t].bestkey = -1; results[t].bestsum = -1;
            pthread_create(&th[t], NULL, worker, &results[t]);
        }
        for (int t = 0; t < nthreads; t++) pthread_join(th[t], NULL);
        int64_t cnt = 0; int best = -1, bsum = -1;
        for (int t = 0; t < nthreads; t++) {
            cnt += results[t].count;
            if (results[t].bestkey >= 0 && (best < 0 || results[t].bestkey < results[best].bestkey)) best = t;
            if (results[t].bestsum >= 0 && (bsum < 0 || results[t].bestsum < results[bsum].bestsum)) bsum = t;
        }
        if (cnt) {
            char s[512]; print_seq(results[best].best, L, s);
            printf("L %d count %lld MM steps %d wF %d wJ %d cls %d %d %d seq %s\n", L, (long long)cnt,
                   results[best].bw, results[best].bwf, results[best].bwj,
                   results[best].bc[0], results[best].bc[1], results[best].bc[2], s);
            print_seq(results[bsum].bests, L, s);
            printf("L %d count %lld SUM steps %d wF %d wJ %d cls %d %d %d seq %s\n", L, (long long)cnt,
                   results[bsum].sw, results[bsum].swf, results[bsum].swj,
                   results[bsum].sc[0], results[bsum].sc[1], results[bsum].sc[2], s);
        } else printf("L %d count 0\n", L);
        fflush(stdout);
    }
    return 0;
}
