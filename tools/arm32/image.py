"""Bounded NSO/ELF32 reader for the synthetic A32 milestone, not a game compatibility claim.

The supported subset uses uncompressed NSO segments, ELF32 dynamic symbols and REL/RELA
R_ARM_ABS32/R_ARM_RELATIVE. ARM mapping symbols explicitly distinguish code and literal data.
"""
import struct
from pathlib import Path

MAX_IMAGE = 16 * 1024 * 1024


class Image32:
    def __init__(self, path):
        raw = Path(path).read_bytes()
        if len(raw) < 0x100 or len(raw) > MAX_IMAGE or raw[:4] != b'NSO0':
            raise ValueError('malformed NSO header')
        if struct.unpack_from('<I', raw, 0xC)[0]:
            raise ValueError('synthetic ARM32 loader requires uncompressed NSO without segment hash flags')
        self.segments = [struct.unpack_from('<III', raw, 0x10 + 16 * i) for i in range(3)]
        self.bss = struct.unpack_from('<I', raw, 0x3C)[0]
        end = 0
        for i, (src, dst, size) in enumerate(self.segments):
            if src < 0x100 or src + size > len(raw) or dst < end or dst + size > MAX_IMAGE:
                raise ValueError(f'malformed NSO segment {i}')
            end = dst + size
        if end + self.bss > MAX_IMAGE:
            raise ValueError('NSO BSS exceeds image limit')
        self.size = (end + self.bss + 0xFFF) & ~0xFFF
        self.image = bytearray(self.size)
        for src, dst, size in self.segments:
            self.image[dst:dst + size] = raw[src:src + size]
        self.text = self.segments[0][1:]
        mod = self.u32(4)
        self.bounds(mod, 28)
        if self.image[mod:mod + 4] != b'MOD0':
            raise ValueError('malformed MOD0')
        dynamic = mod + struct.unpack_from('<i', self.image, mod + 4)[0]
        self.dyn = {}
        while True:
            self.bounds(dynamic, 8)
            tag, val = struct.unpack_from('<iI', self.image, dynamic)
            dynamic += 8
            if tag == 0:
                break
            if tag in self.dyn:
                raise ValueError(f'duplicate ELF32 dynamic tag {tag}')
            self.dyn[tag] = val
        d = self.dyn
        if not all(t in d for t in (4, 5, 6, 10, 11)) or d[11] != 16:
            raise ValueError('ELF32 DT_HASH/STRTAB/SYMTAB/STRSZ/SYMENT=16 required')
        self.bounds(d[4], 8)
        nsyms = self.u32(d[4] + 4)
        self.bounds(d[6], nsyms * 16)
        self.bounds(d[5], d[10])
        self.syms = []
        for i in range(nsyms):
            name, value, size, info, other, section = struct.unpack_from('<IIIBBH', self.image, d[6] + i * 16)
            if name >= d[10]:
                raise ValueError('ELF32 symbol name outside string table')
            start = d[5] + name
            stop = self.image.find(0, start, d[5] + d[10])
            if stop < 0:
                raise ValueError('unterminated ELF32 symbol name')
            self.syms.append((self.image[start:stop].decode('utf-8'), info, section, value, size))
        self.relocs = []
        tables = []
        for ptr, size, ent, width in ((17, 18, 19, 8), (7, 8, 9, 12)):
            if any(t in d for t in (ptr, size, ent)):
                if not all(t in d for t in (ptr, size, ent)) or d[ent] != width:
                    raise ValueError('malformed ELF32 relocation table')
                tables.append((d[ptr], d[size], width))
        if any(t in d for t in (23, 2, 20)):
            if not all(t in d for t in (23, 2, 20)) or d[20] not in (17, 7):
                raise ValueError('malformed ELF32 PLT relocations')
            tables.append((d[23], d[2], 8 if d[20] == 17 else 12))
        occupied = set()
        data_lo, data_size = self.segments[2][1:]
        for ptr, size, width in tables:
            self.bounds(ptr, size)
            if size % width:
                raise ValueError('mis-sized ELF32 relocation table')
            for off in range(ptr, ptr + size, width):
                target, info = struct.unpack_from('<II', self.image, off)
                typ, sym = info & 255, info >> 8
                if target < data_lo or target + 4 > data_lo + data_size + self.bss or target % 4:
                    raise ValueError('relocation destination outside aligned writable data')
                if target in occupied:
                    raise ValueError('duplicate relocation destination')
                occupied.add(target)
                if typ not in (2, 23) or sym >= len(self.syms) or (typ == 23 and sym):
                    raise ValueError(f'unsupported/malformed ARM relocation {typ} at 0x{target:08x}')
                addend = self.u32(target) if width == 8 else struct.unpack_from('<i', self.image, off + 8)[0]
                self.relocs.append((target, typ, sym, addend))

    def bounds(self, offset, size):
        if offset < 0 or size < 0 or offset + size > len(self.image):
            raise ValueError(f'image access out of range: 0x{offset:x}+0x{size:x}')

    def u32(self, offset):
        self.bounds(offset, 4)
        return struct.unpack_from('<I', self.image, offset)[0]

    def code_words(self, base):
        if base < 0 or base % 0x1000 or base + self.size > 0xF0000000:
            raise ValueError('ARM32 base must be page aligned and fit below reserved HLE addresses')
        mappings = sorted((v, n[:2]) for n, _, s, v, _ in self.syms
                          if s and (n in ('$a', '$d', '$t') or n.startswith(('$a.', '$d.', '$t.'))))
        if len({v for v, _ in mappings}) != len(mappings):
            raise ValueError('contradictory/duplicate ARM mapping symbols')
        funcs = [(n, v, z) for n, info, s, v, z in self.syms if s and info & 15 == 2 and z]
        if not funcs:
            raise ValueError('bounded ELF32 function symbols and ARM mapping symbols required')
        words = {}
        for name, start, size in funcs:
            if start & 1:
                raise ValueError(f'Thumb function {name} is unsupported')
            if start % 4 or size % 4 or start < self.text[0] or start + size > sum(self.text):
                raise ValueError(f'function outside aligned text: {name}')
            state = None
            for at in range(start, start + size, 4):
                for point, mode in mappings:
                    if point <= at:
                        state = mode
                if state == '$t':
                    raise ValueError(f'Thumb mapping at 0x{base + at:08x} is unsupported')
                if state is None:
                    raise ValueError(f'missing ARM/data mapping at 0x{base + at:08x}')
                if state == '$a':
                    words[base + at] = self.u32(at)
        return words

    def relocated(self, base, imports):
        """Python reference for the C loader; addresses remain uint32, never host pointers."""
        self.code_words(base)
        out = bytearray(self.image)
        for target, typ, sym, add in self.relocs:
            if typ == 23:
                value = base + add
            else:
                name, _, section, value, _ = self.syms[sym]
                if section:
                    if section >= 0xFF00:
                        raise ValueError('special ELF32 symbol sections are unsupported')
                    self.bounds(value, 1)
                    value += base
                elif name in imports:
                    value = imports[name]
                else:
                    raise ValueError(f'unresolved ARM32 import {name}')
                value += add
            if not 0 <= value <= 0xFFFFFFFF:
                raise ValueError('ARM32 relocation overflow')
            struct.pack_into('<I', out, target, value)
        return out
