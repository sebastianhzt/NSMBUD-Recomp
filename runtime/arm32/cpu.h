// Experimental ARMv7 A32/Thumb state. Never include the legacy guest=host cpu.h here.
#pragma once
#include <stddef.h>
#include <stdint.h>

#define A32_RETURN 0xfffffff0u
#define A32_HLE_BASE 0xf0000000u
typedef struct Cpu32 Cpu32;
typedef int (*A32Step)(Cpu32 *);
typedef int (*A32HostFn)(Cpu32 *);
typedef struct { const char *name; A32HostFn fn; } A32Import;

struct Cpu32 {
    uint32_t r[16];  // r13=SP, r14=LR, r15=current address; PC reads: ARM +8, Thumb +4
    uint32_t cpsr;
    uint8_t *memory; // arbitrary host allocation, never narrowed to a guest address
    uint32_t base, size, image_size;
    uint32_t readonly_lo[2], readonly_size[2];
    const A32Import *imports;
    size_t nimports;
    char error[256];
};
typedef struct { uint32_t value, carry; } A32Shift;

int a32_fail(Cpu32 *c, const char *message);
int a32_load(Cpu32 *c, uint32_t address, unsigned width, uint32_t *value);
int a32_store(Cpu32 *c, uint32_t address, unsigned width, uint32_t value);
int a32_branch(Cpu32 *c, uint32_t target);
int a32_exchange(Cpu32 *c, uint32_t target);
unsigned t32_itstate(const Cpu32 *c);
int t32_start_it(Cpu32 *c, unsigned condition, unsigned mask);
// control: 0 ordinary, 1 control flow (last IT slot only), 2 conditional branch (outside IT).
int t32_predicate(Cpu32 *c, unsigned control, int *execute, int *implicit_flags);
int t32_branch(Cpu32 *c, uint32_t target);
int a32_condition(const Cpu32 *c, unsigned cond);
A32Shift a32_shift(uint32_t value, unsigned kind, unsigned amount, int by_register, uint32_t carry);
uint32_t a32_add(Cpu32 *c, uint32_t a, uint32_t b, uint32_t carry, int flags);
void a32_logic_flags(Cpu32 *c, uint32_t value, uint32_t carry);
int a32_run(Cpu32 *c, A32Step step, uint64_t budget);
int a32_load_nso(Cpu32 *c, const char *path, uint32_t base, uint32_t extra,
                 const A32Import *imports, size_t nimports);
