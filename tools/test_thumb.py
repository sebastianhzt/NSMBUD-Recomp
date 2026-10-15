"""Synthetic Thumb/interworking regression. No game files or automatic installations."""
import ctypes as C
import os
from pathlib import Path
import random
import struct
import subprocess
import unittest

from arm32.fixtures import interworking_fixture, thumb_call, thumb_wide
from arm32.image import Image32
from arm32.thumb import ThumbLifter, Unsupported, instruction_size
from gen_arm32 import generate
from test_arm32 import BASE, SIZE, Cpu, Import, ROOT, compile_c, U, A

WORK = ROOT / 'build/test_thumb'
MASK = 0xF600FC20  # defined NZCV, IT[7:0], T


def half(value):
    return struct.pack('<H', value)


class ThumbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.fixtures = []
        sources = []
        for immediate in (False, True):
            path = WORK / ('immediate.nso' if immediate else 'register.nso')
            interworking_fixture(path, immediate_exchange=immediate)
            image = Image32(path)
            source = WORK / f'program_{int(immediate)}.c'
            source.write_text(generate(image, BASE, thumb=True).replace('int a32_step(', f'int fixture_step_{int(immediate)}('))
            sources.append(source)
            cls.fixtures.append((path, image))
        values = [kind << 11 | amount << 6 | 1 << 3 for kind in range(3) for amount in (0, 1, 31)]
        values += [0x1800 | immediate << 10 | sub << 9 | 2 << 6 | 1 << 3 for immediate in (0, 1) for sub in (0, 1)]
        values += [0x2000 | op << 11 | imm for op in range(4) for imm in (0, 1, 255)]
        values += [0x4000 | op << 6 | 1 << 3 for op in range(16) if op != 13]
        values += [0x4488, 0x4588, 0x4688, 0x4678, 0x4708, 0x4788, 0x4800, 0x48FF,
                   0x9001, 0x9801, 0xA001, 0xA801, 0xB004, 0xB084, 0xBF00, 0xE001, 0xE7FD]
        values += [0x5000 | op << 9 | 5 << 6 | 6 << 3 for op in range(8)]
        values += [op | 3 << 6 | 6 << 3 for op in (0x6000, 0x6800, 0x7000, 0x7800, 0x8000, 0x8800)]
        values += [0xD001 | cond << 8 for cond in range(14)]
        cls.unit = [(BASE + i * 8 + (2 if i & 1 else 0), half(h)) for i, h in enumerate(values)]
        next_pc = BASE + len(values) * 8
        for top in (False, True):
            for value in (0, 1, 0x1234, 0x8000, 0xFFFF):
                cls.unit.append((next_pc + 2, thumb_wide(0, value, top))); next_pc += 8
        for exchange in (False, True):
            for delta in (-12, 12):
                pc = next_pc + 2
                target = (pc + delta) & ~3 if exchange else pc + delta
                cls.unit.append((pc, thumb_call(pc, target, exchange))); next_pc += 8
        cls.blocks = []
        for condition in range(14):
            for mask in range(1, 16):
                start = BASE + 0x4000 + len(cls.blocks) * 32
                count = 4 - ((mask & -mask).bit_length() - 1)
                cells = [half(0x2007), half(0x2800), thumb_wide(3, 0x1234), half(0x3103)][:count]
                block = half(0xBF00 | condition << 4 | mask) + b''.join(cells)
                cls.blocks.append((start, block))
        lifter = ThumbLifter()
        cls.code = bytearray(SIZE)
        cases = []
        all_instructions = list(cls.unit)
        for start, block in cls.blocks:
            offset = 0
            while offset < len(block):
                width = instruction_size(int.from_bytes(block[offset:offset + 2], 'little'))
                all_instructions.append((start + offset, block[offset:offset + width])); offset += width
        cls.control_blocks = [(BASE + 0x6000, half(0xBF08) + half(0x4708)),
                              (BASE + 0x6020, half(0xBF08) + half(0x4788))]
        for start, block in cls.control_blocks:
            all_instructions += [(start, block[:2]), (start + 2, block[2:])]
        for pc, data in all_instructions:
            cls.code[pc - BASE:pc - BASE + len(data)] = data
            cases.append(f'case {pc}u: {{ {lifter.lift(data, pc)} }}')
        (WORK / 'unit.c').write_text('#include "arm32/cpu.h"\nint test_step(Cpu32 *c) {switch(c->r[15]) {\n'
                                    + '\n'.join(cases) + '\ndefault:return a32_fail(c,"unknown test instruction");}}\n')
        compile_c(WORK / 'libthumb.so', ROOT / 'runtime/arm32/core.c', ROOT / 'runtime/arm32/loader.c',
                  WORK / 'unit.c', *sources, shared=True)
        cls.lib = C.CDLL(str(WORK / 'libthumb.so'))
        cls.lib.test_step.argtypes = [C.POINTER(Cpu)]
        for name in ('fixture_step_0', 'fixture_step_1'):
            getattr(cls.lib, name).argtypes = [C.POINTER(Cpu)]
        cls.lib.a32_load_nso.argtypes = [C.POINTER(Cpu), C.c_char_p, C.c_uint32, C.c_uint32, C.c_void_p, C.c_size_t]
        cls.lib.a32_run.argtypes = [C.POINTER(Cpu), C.c_void_p, C.c_uint64]
        cls.lib.a32_exchange.argtypes = [C.POINTER(Cpu), C.c_uint32]
        cls.lib.t32_start_it.argtypes = [C.POINTER(Cpu), C.c_uint, C.c_uint]
        cls.libc = C.CDLL(None); cls.libc.free.argtypes = [C.c_void_p]

    def load(self, index):
        c = Cpu()
        imports = (Import * 1)(Import(b'synthetic_add5', 1)) # loader-only marker; fixture never calls it
        status = self.lib.a32_load_nso(C.byref(c), os.fsencode(self.fixtures[index][0]), BASE, SIZE, imports, 1)
        self.assertEqual(status, 0, c.error)
        c.r[13] = BASE + c.size - 16; c.r[14] = 0xFFFFFFF0; c.r[15] = BASE + 0x20; c.cpsr = 0x10
        return c, imports

    def test_native_interworking_register_and_immediate(self):
        for index in (0, 1):
            with self.subTest(index=index):
                c, imports = self.load(index)
                initial_sp = c.r[13]
                try:
                    self.assertEqual(self.lib.a32_run(C.byref(c), getattr(self.lib, f'fixture_step_{index}'), 100), 0, c.error)
                    self.assertEqual(c.r[0], 20)
                    self.assertEqual(c.r[13], initial_sp)
                    self.assertEqual(c.r[15], 0xFFFFFFF0)
                    self.assertEqual(c.cpsr & 0x0600FC20, 0)
                    self.assertEqual(struct.unpack('<II', C.string_at(c.memory + 0x810, 8)), (20, 0x56781234))
                finally:
                    self.libc.free(c.memory)

    @unittest.skipIf(U is None, 'Unicorn absent: interworking reference not executed')
    def test_complete_fixture_against_unicorn(self):
        regs = [getattr(A, f'UC_ARM_REG_R{i}') for i in range(13)] + [A.UC_ARM_REG_SP, A.UC_ARM_REG_LR, A.UC_ARM_REG_PC]
        for index in (0, 1):
            c, imports = self.load(index)
            try:
                mu = U.Uc(U.UC_ARCH_ARM, U.UC_MODE_ARM)
                mu.mem_map(BASE, c.size); mu.mem_write(BASE, C.string_at(c.memory, c.size))
                mu.reg_write(A.UC_ARM_REG_CPSR, c.cpsr)
                for reg, value in zip(regs, c.r): mu.reg_write(reg, value)
                mu.emu_start(c.r[15], 0xFFFFFFF0, count=100)
                seen = []
                for _ in range(100):
                    if c.r[15] == 0xFFFFFFF0 and not c.cpsr & 0x20: break
                    seen.append((c.r[15], bool(c.cpsr & 0x20)))
                    self.assertEqual(getattr(self.lib, f'fixture_step_{index}')(C.byref(c)), 0, c.error)
                else: self.fail('native fixture did not return')
                self.assertEqual(list(c.r), [mu.reg_read(reg) for reg in regs])
                self.assertEqual(c.cpsr & MASK, mu.reg_read(A.UC_ARM_REG_CPSR) & MASK)
                self.assertEqual(C.string_at(c.memory, c.size), bytes(mu.mem_read(BASE, c.size)))
                self.assertIn((BASE + 0x80, True), seen)
                self.assertIn((BASE + 0xB0, True), seen)
                self.assertIn((BASE + 0xC0, False), seen)
            finally:
                self.libc.free(c.memory)

    @unittest.skipIf(U is None, 'Unicorn absent: Thumb instruction reference not executed')
    def test_individual_thumb_encodings(self):
        rng = random.Random(0x732026)
        memory = bytearray(self.code); memory[0x8000:0x9000] = rng.randbytes(4096)
        native = C.create_string_buffer(bytes(memory), SIZE)
        mu = U.Uc(U.UC_ARCH_ARM, U.UC_MODE_THUMB); mu.mem_map(BASE, SIZE)
        regs = [getattr(A, f'UC_ARM_REG_R{i}') for i in range(13)] + [A.UC_ARM_REG_SP, A.UC_ARM_REG_LR, A.UC_ARM_REG_PC]
        edge = (0, 1, 31, 32, 33, 255, 256, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF)
        count = 0
        for pc, data in self.unit:
            for flags in range(16):
                with self.subTest(pc=hex(pc), data=data.hex(), flags=flags):
                    C.memmove(native, bytes(memory), SIZE); mu.mem_write(BASE, bytes(memory))
                    c = Cpu(); c.memory = C.addressof(native); c.base = BASE; c.size = SIZE; c.cpsr = flags << 28 | 0x30
                    for i in range(15): c.r[i] = rng.choice(edge) if flags < 10 else rng.getrandbits(32)
                    c.r[5] = 16; c.r[6] = BASE + 0x8000; c.r[13] = BASE + 0xE000; c.r[15] = pc
                    if data in (half(0x4708), half(0x4788)):
                        c.r[1] = BASE + 0x7000 + (flags & 1) * (1 + 2 * (flags >> 1 & 1))
                    mu.reg_write(A.UC_ARM_REG_CPSR, c.cpsr)
                    for reg, value in zip(regs, c.r): mu.reg_write(reg, value)
                    mu.emu_start(pc | 1, BASE + SIZE, count=1)
                    self.assertEqual(self.lib.test_step(C.byref(c)), 0, c.error)
                    self.assertEqual(list(c.r), [mu.reg_read(reg) for reg in regs])
                    self.assertEqual(c.cpsr & MASK, mu.reg_read(A.UC_ARM_REG_CPSR) & MASK)
                    self.assertEqual(native.raw, bytes(mu.mem_read(BASE, SIZE)))
                    count += 1
        print(f'Thumb differential: {len(self.unit)} encodings, {count} cases, seed=0x732026 PASS', flush=True)

    @unittest.skipIf(U is None, 'Unicorn absent: IT block reference not executed')
    def test_all_supported_it_conditions_masks_and_flags(self):
        native = C.create_string_buffer(bytes(self.code), SIZE)
        mu = U.Uc(U.UC_ARCH_ARM, U.UC_MODE_THUMB); mu.mem_map(BASE, SIZE); mu.mem_write(BASE, bytes(self.code))
        regs = [getattr(A, f'UC_ARM_REG_R{i}') for i in range(13)] + [A.UC_ARM_REG_SP, A.UC_ARM_REG_LR, A.UC_ARM_REG_PC]
        count = 0
        for start, block in self.blocks:
            for flags in range(16):
                with self.subTest(start=hex(start), flags=flags):
                    c = Cpu(); c.memory = C.addressof(native); c.base = BASE; c.size = SIZE
                    c.r[0] = 5; c.r[1] = 0xFFFFFFFF; c.r[3] = 0xAAAAAAAA; c.r[15] = start; c.cpsr = flags << 28 | 0x30
                    mu.reg_write(A.UC_ARM_REG_CPSR, c.cpsr)
                    for reg, value in zip(regs, c.r): mu.reg_write(reg, value)
                    end = start + len(block)
                    mu.emu_start(start | 1, end, count=16)
                    for _ in range(8):
                        if c.r[15] == end: break
                        self.assertEqual(self.lib.test_step(C.byref(c)), 0, c.error)
                    else: self.fail('IT block did not complete')
                    self.assertEqual(list(c.r), [mu.reg_read(reg) for reg in regs])
                    self.assertEqual(c.cpsr & MASK, mu.reg_read(A.UC_ARM_REG_CPSR) & MASK)
                    count += 1
        self.assertEqual(count, 3360)
        print(f'Thumb IT differential: 210 condition/mask blocks, {count} flag combinations PASS', flush=True)

    def test_invalid_encodings_mapping_boundaries_and_modes(self):
        for data in (half(0xDF00), half(0xBE00), half(0xBD00), half(0xBFEC),
                     struct.pack('<HH', 0xF3EF, 0x8000), half(0xF240)):
            with self.subTest(data=data.hex()), self.assertRaisesRegex(Unsupported, '0x00012340'):
                ThumbLifter().lift(data, 0x12340)
        image = self.fixtures[0][1]
        with self.assertRaisesRegex(ValueError, 'Thumb'):
            generate(image, BASE)
        # End the Thumb function in the middle of MOVW's second halfword.
        old = image.syms[2]
        image.syms[2] = old[:4] + (14,)
        try:
            with self.assertRaisesRegex(ValueError, 'truncated'):
                generate(image, BASE, thumb=True)
        finally: image.syms[2] = old
        image.syms.append(('$d.split', 0, 1, 0x8E, 0))
        try:
            with self.assertRaisesRegex(ValueError, 'mapping'):
                generate(image, BASE, thumb=True)
        finally: image.syms.pop()
        c, imports = self.load(0)
        try:
            c.r[15] = BASE + 0x80
            self.assertEqual(self.lib.fixture_step_0(C.byref(c)), -1)
            self.assertIn(b'wrong ARM/Thumb', c.error)
            c.cpsr = 0x30; c.r[15] = BASE + 0x8E # second halfword of MOVW
            self.assertEqual(self.lib.fixture_step_0(C.byref(c)), -1)
            self.assertIn(b'no compiled', c.error)
            self.assertEqual(self.lib.a32_exchange(C.byref(c), BASE + 2), -1)
            self.assertIn(b'unaligned ARM', c.error)
        finally: self.libc.free(c.memory)

    @unittest.skipIf(U is None, 'Unicorn absent: last-IT-slot interworking reference not executed')
    def test_last_it_slot_interworking_against_unicorn(self):
        regs = [getattr(A, f'UC_ARM_REG_R{i}') for i in range(13)] + [A.UC_ARM_REG_SP, A.UC_ARM_REG_LR, A.UC_ARM_REG_PC]
        for start, block in self.control_blocks:
            for nzcv in range(16):
                for tagged_target in (BASE + 0x7000, BASE + 0x7003):
                    with self.subTest(start=hex(start), nzcv=nzcv, target=hex(tagged_target)):
                        native = C.create_string_buffer(bytes(self.code), SIZE)
                        c = Cpu(); c.memory = C.addressof(native); c.base = BASE; c.size = SIZE
                        c.r[1] = tagged_target; c.r[15] = start; c.cpsr = nzcv << 28 | 0x30
                        mu = U.Uc(U.UC_ARCH_ARM, U.UC_MODE_THUMB)
                        mu.mem_map(BASE, SIZE); mu.mem_write(BASE, bytes(self.code))
                        mu.reg_write(A.UC_ARM_REG_CPSR, c.cpsr)
                        for reg, value in zip(regs, c.r): mu.reg_write(reg, value)
                        stops = (start + len(block), tagged_target & ~1)
                        # Stop at a translation-block boundary: stopping a CODE hook on an ARM
                        # target inside a large TB can leave Unicorn's PC at that TB's end.
                        mu.hook_add(U.UC_HOOK_BLOCK, lambda uc, address, size, _: uc.emu_stop() if address in stops else None)
                        mu.emu_start(start | 1, BASE + SIZE, count=8)
                        for _ in range(2):
                            self.assertEqual(self.lib.test_step(C.byref(c)), 0, c.error)
                        self.assertEqual(list(c.r), [mu.reg_read(reg) for reg in regs])
                        self.assertEqual(c.cpsr & MASK, mu.reg_read(A.UC_ARM_REG_CPSR) & MASK)

    def test_it_restrictions_predicated_memory_and_bad_targets(self):
        c = Cpu(); c.cpsr = 0x30; c.r[15] = BASE
        self.assertEqual(self.lib.t32_start_it(C.byref(c), 0, 4), 0) # two slots
        self.assertEqual(self.lib.t32_start_it(C.byref(c), 0, 8), -1)
        self.assertIn(b'nested', c.error)
        bx_pc = next(pc for pc, data in self.unit if data == half(0x4708))
        c.r[15] = bx_pc; c.r[1] = BASE + 0x7000
        self.assertEqual(self.lib.test_step(C.byref(c)), -1)
        self.assertIn(b'last IT slot', c.error)
        c = Cpu(); c.cpsr = 0x30
        c.r[15] = next(pc for pc, data in self.unit if data == half(0xD001))
        self.assertEqual(self.lib.t32_start_it(C.byref(c), 0, 8), 0)
        self.assertEqual(self.lib.test_step(C.byref(c)), -1)
        self.assertIn(b'conditional branch inside IT', c.error)
        load_pc = next(pc for pc, data in self.unit if data == half(0x68F0))
        for execute in (False, True):
            c = Cpu(); c.cpsr = (0x40000000 if execute else 0) | 0x30; c.r[15] = load_pc
            self.assertEqual(self.lib.t32_start_it(C.byref(c), 0, 8), 0)
            c.r[6] = 0xFFFFFFFF
            result = self.lib.test_step(C.byref(c))
            self.assertEqual(result, -1 if execute else 0)
            if execute: self.assertIn(b'out of range', c.error)
        c, imports = self.load(0)
        try:
            c.cpsr = 0x30; c.r[15] = 0xFFFFFFF0
            self.assertEqual(self.lib.a32_run(C.byref(c), self.lib.fixture_step_0, 10), -1)
            self.assertTrue(c.error) # a Thumb jump cannot accidentally hit the ARM-only exit sentinel
            c.cpsr = 0x810; c.r[15] = 0xFFFFFFF0 # ITSTATE while in ARM state
            self.assertEqual(self.lib.a32_run(C.byref(c), self.lib.fixture_step_0, 10), -1)
            self.assertIn(b'IT state in ARM', c.error)
        finally: self.libc.free(c.memory)


if __name__ == '__main__':
    unittest.main()
