// Bounded NSO segment + ELF32 dynamic loader for the experimental synthetic runner.
// No Elf64Sym/Elf64Rela, AArch64 relocation numbers, guest=host or LAS symbols.
#include "cpu.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_IMAGE (16u * 1024u * 1024u)
static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
static int within(uint64_t off, uint64_t size, uint64_t end) { return off <= end && size <= end - off; }
static uint32_t read32(Cpu32 *c, uint32_t off) {
    if (!within(off, 4, c->image_size)) { a32_fail(c, "ELF32 table access out of range"); return 0; }
    return le32(c->memory + off);
}

static int symbol(Cpu32 *c, uint32_t *d, uint32_t index, uint64_t *value) {
    uint32_t at = d[6] + index * 16;
    uint32_t name = read32(c, at), offset = read32(c, at + 4);
    unsigned section = (unsigned)c->memory[at + 14] | (unsigned)c->memory[at + 15] << 8;
    if (name >= d[10] || !memchr(c->memory + d[5] + name, 0, d[10] - name))
        return a32_fail(c, "ELF32 symbol name outside terminated string table");
    if (section) {
        if (section >= 0xff00) return a32_fail(c, "special ELF32 symbol sections are unsupported");
        if (!within(offset, 1, c->image_size)) return a32_fail(c, "ELF32 symbol value out of range");
        *value = (uint64_t)c->base + offset;
        return 0;
    }
    const char *name_str = (const char *)c->memory + d[5] + name;
    for (size_t i = 0; i < c->nimports; i++) if (!strcmp(name_str, c->imports[i].name) && c->imports[i].fn) {
        *value = A32_HLE_BASE + 16u * i;
        return 0;
    }
    return a32_fail(c, "unresolved ARM32 import (no silent HLE stubs)");
}

static int relocs(Cpu32 *c, uint32_t *d, uint32_t nsyms, uint32_t start, uint32_t size,
                  unsigned width, uint32_t data_lo, uint32_t data_end, uint8_t *seen) {
    if (size % width || !within(start, size, c->image_size)) return a32_fail(c, "malformed ELF32 relocation table");
    for (uint32_t off = start; off < start + size; off += width) {
        uint32_t target = read32(c, off), info = read32(c, off + 4);
        unsigned type = info & 255, si = info >> 8;
        if (target < data_lo || !within(target, 4, data_end) || target % 4)
            return a32_fail(c, "ARM32 relocation destination outside aligned writable data");
        if (seen[target / 4]++) return a32_fail(c, "duplicate ARM32 relocation destination");
        if ((type != 2 && type != 23) || si >= nsyms || (type == 23 && si))
            return a32_fail(c, "unsupported/malformed ARM32 relocation type/symbol");
        int64_t addend = width == 8 ? (int64_t)read32(c, target) : (int64_t)(int32_t)read32(c, off + 8);
        uint64_t value = c->base;
        if (type == 2 && symbol(c, d, si, &value)) return -1;
        int64_t result = (int64_t)value + addend;
        if (result < 0 || result > UINT32_MAX) return a32_fail(c, "ARM32 relocation overflow");
        if (a32_store(c, c->base + target, 4, (uint32_t)result)) return -1;
    }
    return c->error[0] ? -1 : 0;
}

int a32_load_nso(Cpu32 *c, const char *path, uint32_t base, uint32_t extra,
                 const A32Import *imports, size_t nimports) {
    memset(c, 0, sizeof *c);
    FILE *f = fopen(path, "rb");
    if (!f) return a32_fail(c, "cannot open NSO");
    if (fseek(f, 0, SEEK_END)) { fclose(f); return a32_fail(c, "cannot size NSO"); }
    long length = ftell(f);
    if (length < 0x100 || length > MAX_IMAGE || fseek(f, 0, SEEK_SET)) {
        fclose(f); return a32_fail(c, "malformed/oversized NSO");
    }
    uint8_t *raw = malloc((size_t)length);
    if (!raw) { fclose(f); return a32_fail(c, "NSO allocation failed"); }
    size_t got = fread(raw, 1, (size_t)length, f);
    fclose(f);
    if (got != (size_t)length || memcmp(raw, "NSO0", 4) || le32(raw + 12)) {
        free(raw); return a32_fail(c, "NSO requires valid header, uncompressed segments and no hash flags");
    }
    uint32_t src[3], dst[3], size[3], bss = le32(raw + 0x3c);
    uint64_t end = 0;
    for (int i = 0; i < 3; i++) {
        src[i] = le32(raw + 0x10 + i * 16); dst[i] = le32(raw + 0x14 + i * 16); size[i] = le32(raw + 0x18 + i * 16);
        if (src[i] < 0x100 || !within(src[i], size[i], (uint64_t)length) || dst[i] < end ||
            !within(dst[i], size[i], MAX_IMAGE)) {
            free(raw); return a32_fail(c, "malformed NSO segment");
        }
        end = (uint64_t)dst[i] + size[i];
    }
    if (end + bss > MAX_IMAGE) { free(raw); return a32_fail(c, "NSO BSS exceeds image limit"); }
    c->image_size = (uint32_t)((end + bss + 4095) & ~4095ull);
    uint64_t total = (uint64_t)c->image_size + extra;
    if (base % 4096 || total > MAX_IMAGE || (uint64_t)base + total > A32_HLE_BASE || nimports > 4096) {
        free(raw); return a32_fail(c, "ARM32 image/base exceeds supported address range");
    }
    c->base = base; c->size = (uint32_t)total; c->imports = imports; c->nimports = nimports;
    c->memory = calloc(c->size, 1);
    if (!c->memory) { free(raw); return a32_fail(c, "guest memory allocation failed"); }
    for (int i = 0; i < 3; i++) memcpy(c->memory + dst[i], raw + src[i], size[i]);
    free(raw);
    uint32_t mod = read32(c, 4);
    if (!within(mod, 28, c->image_size) || memcmp(c->memory + mod, "MOD0", 4)) return a32_fail(c, "malformed MOD0");
    int64_t dynamic = (int64_t)mod + (int32_t)read32(c, mod + 4);
    uint32_t d[24] = {0}, present = 0;
    for (;;) {
        if (dynamic < 0 || !within((uint64_t)dynamic, 8, c->image_size)) return a32_fail(c, "unterminated ELF32 dynamic table");
        uint32_t tag = read32(c, (uint32_t)dynamic), val = read32(c, (uint32_t)dynamic + 4);
        dynamic += 8;
        if (!tag) break;
        if (tag >= 24 || (present & (1u << tag))) return a32_fail(c, "unsupported/duplicate ELF32 dynamic tag");
        present |= 1u << tag; d[tag] = val;
    }
    const uint32_t required = (1u << 4) | (1u << 5) | (1u << 6) | (1u << 10) | (1u << 11);
    if ((present & required) != required || d[11] != 16 || !within(d[4], 8, c->image_size))
        return a32_fail(c, "ELF32 symbol metadata/SYMENT=16 required");
    uint32_t nsyms = read32(c, d[4] + 4);
    if (!within(d[6], (uint64_t)nsyms * 16, c->image_size) || !within(d[5], d[10], c->image_size))
        return a32_fail(c, "ELF32 symbol/string table out of range");
    // Validate all names, including unreferenced symbols, as the Python reader does.
    for (uint32_t i = 0; i < nsyms; i++) {
        uint32_t name = read32(c, d[6] + 16 * i);
        if (name >= d[10] || !memchr(c->memory + d[5] + name, 0, d[10] - name))
            return a32_fail(c, "malformed ELF32 symbol name");
    }
    uint8_t *seen = calloc(c->image_size / 4, 1);
    if (!seen) return a32_fail(c, "relocation allocation failed");
    int status = 0;
    const unsigned tags[2][4] = {{17, 18, 19, 8}, {7, 8, 9, 12}};
    for (unsigned i = 0; i < 2 && !status; i++) {
        const unsigned *t = tags[i];
        uint32_t mask = (1u << t[0]) | (1u << t[1]) | (1u << t[2]);
        if (present & mask) {
            if ((present & mask) != mask || d[t[2]] != t[3]) status = a32_fail(c, "malformed ELF32 relocation metadata");
            else status = relocs(c, d, nsyms, d[t[0]], d[t[1]], t[3], dst[2], dst[2] + size[2] + bss, seen);
        }
    }
    uint32_t plt = (1u << 23) | (1u << 2) | (1u << 20);
    if (!status && (present & plt)) {
        if ((present & plt) != plt || (d[20] != 17 && d[20] != 7)) status = a32_fail(c, "malformed ELF32 PLT metadata");
        else status = relocs(c, d, nsyms, d[23], d[2], d[20] == 17 ? 8 : 12, dst[2], dst[2] + size[2] + bss, seen);
    }
    free(seen);
    for (unsigned i = 0; i < 2; i++) { c->readonly_lo[i] = base + dst[i]; c->readonly_size[i] = size[i]; }
    return status || c->error[0] ? -1 : 0;
}
