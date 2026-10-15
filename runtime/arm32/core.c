#include "cpu.h"
#include <limits.h>
#include <stdio.h>
#include <string.h>

int a32_fail(Cpu32 *c, const char *message) {
    snprintf(c->error, sizeof c->error, "ARM32 pc=0x%08x: %s", c->r[15], message);
    return -1;
}

static int translate(Cpu32 *c, uint32_t address, unsigned width, int write, uint8_t **out) {
    if ((width != 1 && width != 2 && width != 4) || address < c->base ||
        (uint64_t)address + width > (uint64_t)c->base + c->size)
        return a32_fail(c, "guest memory access out of range (32-bit address, checked translation)");
    if (write) for (unsigned i = 0; i < 2; i++) {
        if (!c->readonly_size[i]) continue;
        uint64_t lo = c->readonly_lo[i], hi = lo + c->readonly_size[i];
        if ((uint64_t)address < hi && (uint64_t)address + width > lo)
            return a32_fail(c, "write to read-only guest text/rodata");
    }
    *out = c->memory + (address - c->base);
    return 0;
}

int a32_load(Cpu32 *c, uint32_t address, unsigned width, uint32_t *value) {
    uint8_t *p;
    if (translate(c, address, width, 0, &p)) return -1;
    *value = 0;
    for (unsigned i = 0; i < width; i++) *value |= (uint32_t)p[i] << (8 * i);
    return 0;
}

int a32_store(Cpu32 *c, uint32_t address, unsigned width, uint32_t value) {
    uint8_t *p;
    if (translate(c, address, width, 1, &p)) return -1;
    for (unsigned i = 0; i < width; i++) p[i] = (uint8_t)(value >> (8 * i));
    return 0;
}

int a32_branch(Cpu32 *c, uint32_t target) {
    if (target & 1) return a32_fail(c, "Thumb/T32 interworking is unsupported");
    if (target & 3) return a32_fail(c, "unaligned A32 branch target");
    c->r[15] = target;
    return 0;
}

int a32_condition(const Cpu32 *c, unsigned cond) {
    unsigned n = c->cpsr >> 31, z = c->cpsr >> 30 & 1, cy = c->cpsr >> 29 & 1, v = c->cpsr >> 28 & 1;
    switch (cond) {
    case 0: return z; case 1: return !z; case 2: return cy; case 3: return !cy;
    case 4: return n; case 5: return !n; case 6: return v; case 7: return !v;
    case 8: return cy && !z; case 9: return !cy || z; case 10: return n == v;
    case 11: return n != v; case 12: return !z && n == v; case 13: return z || n != v;
    case 14: return 1; default: return 0;
    }
}

A32Shift a32_shift(uint32_t value, unsigned kind, unsigned amount, int by_register, uint32_t carry) {
    A32Shift s = {value, carry};
    if (by_register) {
        amount &= 255;
        if (!amount) return s;
    } else if (!amount) {
        if (kind == 0) return s;
        if (kind == 3) return (A32Shift){(carry << 31) | (value >> 1), value & 1}; // RRX
        amount = 32; // LSR/ASR #0 encodes #32
    }
    switch (kind) {
    case 0:
        s.carry = amount <= 32 ? (value >> (32 - amount)) & 1 : 0;
        s.value = amount < 32 ? value << amount : 0;
        break;
    case 1:
        s.carry = amount <= 32 ? (value >> (amount - 1)) & 1 : 0;
        s.value = amount < 32 ? value >> amount : 0;
        break;
    case 2:
        s.carry = (value >> ((amount < 32 ? amount : 32) - 1)) & 1;
        s.value = amount < 32 ? (value >> amount) | ((value & 0x80000000u) ? (~0u << (32 - amount)) : 0)
                              : (value & 0x80000000u ? ~0u : 0);
        break;
    case 3:
        amount &= 31;
        s.value = amount ? (value >> amount) | (value << (32 - amount)) : value;
        s.carry = s.value >> 31;
        break;
    }
    return s;
}

uint32_t a32_add(Cpu32 *c, uint32_t a, uint32_t b, uint32_t carry, int flags) {
    uint64_t wide = (uint64_t)a + b + carry;
    uint32_t r = (uint32_t)wide;
    if (flags) {
        int64_t signed_sum = (int64_t)(int32_t)a + (int64_t)(int32_t)b + carry;
        c->cpsr = (c->cpsr & 0x0fffffffu) | (r & 0x80000000u) | ((uint32_t)(r == 0) << 30) |
                  ((uint32_t)(wide >> 32) << 29) | ((uint32_t)(signed_sum > INT32_MAX || signed_sum < INT32_MIN) << 28);
    }
    return r;
}

void a32_logic_flags(Cpu32 *c, uint32_t value, uint32_t carry) {
    c->cpsr = (c->cpsr & 0x1fffffffu) | (value & 0x80000000u) | ((uint32_t)(value == 0) << 30) | (carry << 29);
}

int a32_run(Cpu32 *c, A32Step step, uint64_t budget) {
    if (c->cpsr & 0x20) return a32_fail(c, "Thumb/T32 state is unsupported");
    while (c->r[15] != A32_RETURN) {
        if (c->cpsr & 0x20) return a32_fail(c, "Thumb/T32 state is unsupported");
        if (!budget--) return a32_fail(c, "instruction budget exhausted");
        uint32_t pc = c->r[15];
        if (pc & 3) return a32_fail(c, "unaligned A32 dispatch");
        if (pc >= A32_HLE_BASE && pc < A32_RETURN) {
            size_t index = (pc - A32_HLE_BASE) / 16;
            if ((pc - A32_HLE_BASE) % 16 || index >= c->nimports || !c->imports[index].fn)
                return a32_fail(c, "unknown synthetic HLE service");
            if (c->r[13] & 7) return a32_fail(c, "synthetic AAPCS32 call requires 8-byte aligned SP");
            uint32_t preserved[10]; // r4-r11, SP, LR
            memcpy(preserved, c->r + 4, 8 * sizeof(uint32_t));
            preserved[8] = c->r[13]; preserved[9] = c->r[14];
            if (c->imports[index].fn(c)) {
                if (!c->error[0]) a32_fail(c, "synthetic HLE service failed");
                return -1;
            }
            if (memcmp(preserved, c->r + 4, 8 * sizeof(uint32_t)) || preserved[8] != c->r[13] || preserved[9] != c->r[14])
                return a32_fail(c, "synthetic HLE violated preserved registers/SP/LR");
            if (a32_branch(c, c->r[14])) return -1;
        } else if (step(c)) return -1;
    }
    return 0;
}
