"""Reproducible, original machine-code fixtures. No external game files or assembler needed."""
import struct
from pathlib import Path


def dp(op, rd=0, rn=1, operand=2, immediate=False, flags=True, cond=14):
    return cond << 28 | int(immediate) << 25 | op << 21 | int(flags) << 20 | rn << 16 | rd << 12 | operand


def fixture(path, rela=False):
    image = bytearray(0xA00)
    put = lambda at, word: struct.pack_into('<I', image, at, word)
    # Header trampoline and MOD0 pointer are data outside the translated functions.
    put(0, 0xEA000006)
    put(4, 0x100)
    image[0x100:0x104] = b'MOD0'
    struct.pack_into('<6i', image, 0x104, 0x300, 0x800, 0x900, 0, 0, 0)
    program = [0xE52DE008, 0xE24DD008, # save LR; reserve aligned fifth-argument slot
               0xE3A00001, 0xE3A01002, 0xE3A02003, 0xE3A03004, 0xE3A04005, 0xE58D4000]
    at = 0x20 + len(program) * 4
    program += [0xE59F5000 | (0x804 - at - 8), 0xE12FFF35, 0xE28DD008] # imported add5, BLX, release arg
    at = 0x20 + len(program) * 4
    program += [0xEB000000 | (((0x80 - at - 8) // 4) & 0xFFFFFF)] # BL helper
    at = 0x20 + len(program) * 4
    program += [0xE59F6000 | (0x800 - at - 8), 0xE5860000, 0xE49DE008, 0xE12FFF1E]
    for i, word in enumerate(program):
        put(0x20 + 4 * i, word)
    put(0x80, 0xE2800007) # helper: add r0, r0, #7
    put(0x84, 0xE12FFF1E) # bx lr
    # A literal inside the _start symbol size, explicitly excluded by $d mapping.
    put(0x60, 0xFFFFFFFF)
    symbols = [('', 0, 0, 0, 0), ('_start', 0x12, 1, 0x20, 0x44),
               ('helper', 0x12, 1, 0x80, 8), ('$a', 0, 1, 0x20, 0),
               ('$d', 0, 1, 0x60, 0), ('$a.1', 0, 1, 0x80, 0),
               ('synthetic_add5', 0x12, 0, 0, 0), ('result', 0x11, 3, 0x810, 4)]
    strings = bytearray(b'\0')
    for i, (name, info, section, value, size) in enumerate(symbols):
        offset = len(strings) if name else 0
        if name:
            strings.extend(name.encode() + b'\0')
        struct.pack_into('<IIIBBH', image, 0x500 + i * 16, offset, value, size, info, 0, section)
    image[0x680:0x680 + len(strings)] = strings
    struct.pack_into('<II', image, 0x700, 1, len(symbols))
    relocations = [(0x800, 23, 0x810), (0x804, 6 << 8 | 2, 0), (0x808, 2 << 8 | 2, 0)]
    width = 12 if rela else 8
    for i, (target, info, addend) in enumerate(relocations):
        struct.pack_into('<II', image, 0x740 + width * i, target, info)
        if rela:
            struct.pack_into('<i', image, 0x748 + width * i, addend)
        else:
            put(target, addend)
    dynamic = [(4, 0x700), (5, 0x680), (6, 0x500), (10, len(strings)), (11, 16),
               (7 if rela else 17, 0x740), (8 if rela else 18, len(relocations) * width),
               (9 if rela else 19, width), (0, 0)]
    for i, pair in enumerate(dynamic):
        struct.pack_into('<iI', image, 0x400 + i * 8, *pair)
    raw = bytearray(0xB00)
    raw[:4] = b'NSO0'
    for i, (offset, size) in enumerate(((0, 0x200), (0x400, 0x400), (0x800, 0x200))):
        struct.pack_into('<III', raw, 0x10 + i * 16, 0x100 + offset, offset, size)
        struct.pack_into('<I', raw, 0x60 + i * 4, size)
        raw[0x100 + offset:0x100 + offset + size] = image[offset:offset + size]
    struct.pack_into('<I', raw, 0x3C, 0x100)
    Path(path).write_bytes(raw)
    return raw


def npdm(path, arch):
    raw = bytearray(0x80)
    raw[:4] = b'META'
    raw[0xC] = 1 if arch == 'aarch64' else 0
    Path(path).write_bytes(raw)


def fixture64(directory):
    """A small original AArch64 NSO for the existing loader and test_lift.py regression."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    image = bytearray(0xA00)
    words = [0x14000002, 0x400, 0xD2800020, 0x91000420, 0xB1000420, 0xF1000420,
             0xCA020020, 0x8A020020, 0xAA020020, 0xD3407C20, 0x8B020020, 0xCB020020,
             0xAB020020, 0xEB020020, 0x9A020020, 0xDA020020]
    for i, word in enumerate(words):
        struct.pack_into('<I', image, 4 * i, word)
    image[0x400:0x404] = b'MOD0'
    struct.pack_into('<6i', image, 0x404, 0x40, 0x600, 0x700, -0x400, -0x400, 0)
    strings = b'\0_start\0'
    image[0x680:0x680 + len(strings)] = strings
    struct.pack_into('<IBBHQQ', image, 0x518, 1, 0x12, 0, 1, 8, 0x38)
    struct.pack_into('<II', image, 0x700, 1, 2)
    for i, pair in enumerate(((4, 0x700), (5, 0x680), (6, 0x500), (10, len(strings)), (11, 24), (0, 0))):
        struct.pack_into('<QQ', image, 0x440 + i * 16, *pair)
    raw = bytearray(0xB00)
    raw[:4] = b'NSO0'
    for i, (offset, size) in enumerate(((0, 0x40), (0x400, 0x400), (0x800, 0x200))):
        struct.pack_into('<III', raw, 0x10 + i * 16, 0x100 + offset, offset, size)
        struct.pack_into('<I', raw, 0x60 + i * 4, size)
        raw[0x100 + offset:0x100 + offset + size] = image[offset:offset + size]
    struct.pack_into('<I', raw, 0x3C, 0x100)
    path = directory / 'synthetic64.nso'
    path.write_bytes(raw)
    npdm(directory / 'main.npdm', 'aarch64')
    return path


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('directory')
    args = ap.parse_args()
    out = Path(args.directory)
    out.mkdir(parents=True, exist_ok=True)
    fixture(out / 'synthetic.nso')
    npdm(out / 'main.npdm', 'arm32')
    print(f'Original A32 fixtures written to {out}')
