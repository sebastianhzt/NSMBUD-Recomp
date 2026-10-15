// Runs lifted single-instruction tests; prints final state per case.
#ifdef __APPLE__
#include <CommonCrypto/CommonDigest.h>
#else
#include <openssl/evp.h>
#endif
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>

#include "cpu.h"

extern GuestFn tests[];
extern int ntests;

#define BUF 0x7200000000ull
#define BUF_SIZE 0x40000

void rt_dispatch(Cpu *c) { (void)c; }
void rt_svc(Cpu *c, uint32_t imm, uint64_t pc) { (void)c; (void)imm; (void)pc; }
void rt_brk(Cpu *c, uint32_t imm, uint64_t pc) { (void)c; (void)imm; (void)pc; abort(); }
void rt_udf(Cpu *c, uint64_t pc) { (void)c; (void)pc; abort(); }
uint64_t rt_ticks(void) { return 0; }

// Both implementations compute the same 16-byte MD5 of the complete guest buffer.
// This is a differential-test checksum, not an authentication/security primitive.
static void memory_md5(const void *data, size_t size, unsigned char out[16]) {
#ifdef __APPLE__
    if (!CC_MD5(data, (CC_LONG)size, out)) { fprintf(stderr, "CommonCrypto MD5 failed\n"); exit(1); }
#else
    unsigned int length = 0;
    if (EVP_Digest(data, size, out, &length, EVP_md5(), NULL) != 1 || length != 16) {
        fprintf(stderr, "OpenSSL EVP MD5 unavailable/failed\n");
        exit(1);
    }
#endif
}

static void *map_file(const char *path, uint64_t addr, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(1); }
    fseek(f, 0, SEEK_END);
    *size = ftell(f);
    fseek(f, 0, SEEK_SET);
    size_t len = (*size + 0x3fff) & ~0x3fffull;
    void *p = mmap((void *)addr, len, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0);
    if (p != (void *)addr) { perror("mmap"); exit(1); }
    if (fread(p, 1, *size, f) != *size) { fprintf(stderr, "short read: %s\n", path); exit(1); }
    fclose(f);
    return p;
}

int main(int argc, char **argv) {
    if (argc != 5) { fprintf(stderr, "usage: harness image buffer cases base\n"); return 2; }
    uint64_t base = strtoull(argv[4], 0, 16);
    size_t isz, bsz;
    map_file(argv[1], base, &isz);
    map_file(argv[2], BUF, &bsz);
    uint8_t *pristine = malloc(BUF_SIZE);
    if (!pristine || bsz != BUF_SIZE) { fprintf(stderr, "invalid buffer/allocation\n"); return 1; }
    memcpy(pristine, (void *)BUF, BUF_SIZE);
    FILE *f = fopen(argv[3], "rb");
    if (!f) { perror(argv[3]); return 1; }
    struct { uint64_t k, pc, x[32], sp, nzcv; __uint128_t v[32]; } in;
    static Cpu c;
    while (fread(&in, sizeof in, 1, f) == 1) {
        if (in.k >= (uint64_t)ntests) { fprintf(stderr, "invalid test index\n"); return 1; }
        memcpy((void *)BUF, pristine, BUF_SIZE);
        memset(&c, 0, sizeof c);
        memcpy(c.x, in.x, sizeof c.x);
        c.sp = in.sp;
        c.nzcv = in.nzcv;
        memcpy(c.v, in.v, sizeof c.v);
        c.excl_addr = ~0ull;
        tests[in.k](&c);
        unsigned char md[16];
        memory_md5((void *)BUF, BUF_SIZE, md);
        for (int i = 0; i < 31; i++) printf("%" PRIx64 " ", c.x[i]);
        printf("%" PRIx64 " %" PRIx64 " %" PRIx64 " ", c.sp, c.nzcv & UINT64_C(0xf0000000), c.pc);
        for (int i = 0; i < 16; i++) printf("%02x", md[i]);
        for (int i = 0; i < 32; i++) printf(" %016" PRIx64 "%016" PRIx64, (uint64_t)(c.v[i] >> 64), (uint64_t)c.v[i]);
        printf("\n");
    }
    return 0;
}
