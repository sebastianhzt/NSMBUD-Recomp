// This executable is a synthetic AAPCS32 harness, not a Horizon kernel or a game launcher.
#include "arm32/cpu.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern int a32_step(Cpu32 *c);

static int synthetic_add5(Cpu32 *c) {
    uint32_t fifth;
    if (a32_load(c, c->r[13], 4, &fifth)) return -1;
    c->r[0] = c->r[0] + c->r[1] + c->r[2] + c->r[3] + fifth;
    return 0;
}
static const A32Import imports[] = {{"synthetic_add5", synthetic_add5}};

static int number(const char *s, uint32_t *out) {
    errno = 0;
    char *end;
    unsigned long long value = strtoull(s, &end, 0);
    if (errno || !*s || *end || value > UINT32_MAX) return -1;
    *out = (uint32_t)value;
    return 0;
}

int main(int argc, char **argv) {
    uint32_t base, entry;
    if (argc != 8 || strcmp(argv[1], "--guest-arch") || strcmp(argv[2], "arm32") ||
        strcmp(argv[3], "--game-profile") || strcmp(argv[4], "generic") || number(argv[6], &base) || number(argv[7], &entry)) {
        fprintf(stderr, "usage: runner --guest-arch arm32 --game-profile generic synthetic.nso BASE ENTRY_OFFSET\n");
        return 2;
    }
    Cpu32 c;
    int status = a32_load_nso(&c, argv[5], base, 0x10000, imports, sizeof imports / sizeof imports[0]);
    if (!status) {
        if (entry >= c.image_size || (uint64_t)base + entry > UINT32_MAX) status = a32_fail(&c, "entry outside image");
        else {
            c.r[13] = base + c.size - 16;
            c.r[14] = A32_RETURN;
            c.r[15] = base + entry;
            status = a32_run(&c, a32_step, 100000);
        }
    }
    if (status) fprintf(stderr, "%s\n", c.error);
    else printf("ARM32 PASS r0=%u sp=0x%08x pc=0x%08x\n", c.r[0], c.r[13], c.r[15]);
    free(c.memory);
    return status ? 1 : 0;
}
