"""Original A32 fixtures -> C -> native execution, plus fixed-seed Unicorn comparisons.

Run: tools/venv/bin/python tools/test_arm32.py -v
Uses only already installed dependencies; Unicorn comparisons are explicitly skipped if absent.
"""
import ctypes as C
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import unittest

from arm32.fixtures import dp, fixture, fixture64, npdm
from arm32.image import Image32
from arm32.lifter import Lifter32, Unsupported
from gen_arm32 import generate
from guest_arch import select_arch
from nso import Nso

try:
    import unicorn as U
    from unicorn import arm_const as A
except ImportError:
    U = None

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / 'build/test_arm32'
BASE, SIZE = 0x10000, 0x10000


class Cpu(C.Structure):
    _fields_ = [('r', C.c_uint32 * 16), ('cpsr', C.c_uint32), ('memory', C.c_void_p),
                ('base', C.c_uint32), ('size', C.c_uint32), ('image_size', C.c_uint32),
                ('readonly_lo', C.c_uint32 * 2), ('readonly_size', C.c_uint32 * 2),
                ('imports', C.c_void_p), ('nimports', C.c_size_t), ('error', C.c_char * 256)]


class Import(C.Structure):
    _fields_ = [('name', C.c_char_p), ('fn', C.c_void_p)]


def compile_c(output, *sources, shared=False):
    flags = ['-shared', '-fPIC'] if shared else []
    subprocess.run([os.environ.get('CC', 'clang'), '-std=c11', '-O1', '-g', '-Wall', '-Wextra',
                    '-Werror', '-I', str(ROOT / 'runtime'), *flags,
                    *(str(p) for p in sources), '-o', str(output)], check=True)


class Arm32Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.path = WORK / 'synthetic.nso'
        fixture(cls.path)
        cls.image = Image32(cls.path)
        (WORK / 'program.c').write_text(generate(cls.image, BASE))
        compile_c(WORK / 'runner', ROOT / 'runtime/arm32/core.c', ROOT / 'runtime/arm32/loader.c',
                  ROOT / 'tools/arm32/runner.c', WORK / 'program.c')
        words = []
        for op in range(16):
            rn = 0 if op in (13, 15) else 1
            for imm in (0, 1, 255, 0x102, 0x4FF):
                words.append(dp(op, rn=rn, operand=imm, immediate=True))
            for kind in range(4):
                for amount in (0, 1, 31):
                    words.append(dp(op, rn=rn, operand=2 | kind << 5 | amount << 7))
                words.append(dp(op, rn=rn, operand=2 | kind << 5 | 1 << 4 | 3 << 8))
        # Conditions, PC reads, branch/link/exchange, byte/halfword/sign extension/writeback.
        words += [dp(13, rn=0, operand=7, immediate=True, flags=False, cond=cond) for cond in range(15)]
        words += [0xE1A0000F, 0xE28F0004, 0xEA000002, 0xEB000002, 0xE12FFF11, 0xE12FFF31,
                  0xE1A0F001, 0xE5960004, 0xE5860004, 0xE5D60001, 0xE5C60001,
                  0xE1D600B2, 0xE1C600B2, 0xE1D600D1, 0xE1D600F2, 0xE4960004, 0xE5360004,
                  0xE7960105, 0xE59F0000, 0xE596F004]
        cls.words = list(dict.fromkeys(words))
        lifter = Lifter32()
        cases = []
        for i, word in enumerate(cls.words):
            pc = BASE + 4 * i
            cases.append(f'case {pc}u: {{ {lifter.lift(word, pc)} }}')
        (WORK / 'steps.c').write_text('#include "arm32/cpu.h"\nint test_step(Cpu32 *c) { switch(c->r[15]) {\n'
                                     + '\n'.join(cases) + '\ndefault: return a32_fail(c, "unknown test PC"); }}\n')
        compile_c(WORK / 'libtests.so', ROOT / 'runtime/arm32/core.c', ROOT / 'runtime/arm32/loader.c',
                  WORK / 'steps.c', WORK / 'program.c', shared=True)
        cls.lib = C.CDLL(str(WORK / 'libtests.so'))
        cls.lib.test_step.argtypes = [C.POINTER(Cpu)]
        cls.lib.a32_step.argtypes = [C.POINTER(Cpu)]
        cls.lib.a32_load_nso.argtypes = [C.POINTER(Cpu), C.c_char_p, C.c_uint32, C.c_uint32, C.c_void_p, C.c_size_t]
        cls.lib.a32_run.argtypes = [C.POINTER(Cpu), C.c_void_p, C.c_uint64]
        cls.lib.a32_store.argtypes = [C.POINTER(Cpu), C.c_uint32, C.c_uint, C.c_uint32]
        cls.lib.a32_load.argtypes = [C.POINTER(Cpu), C.c_uint32, C.c_uint, C.POINTER(C.c_uint32)]
        cls.lib.a32_branch.argtypes = [C.POINTER(Cpu), C.c_uint32]
        cls.libc = C.CDLL(None)
        cls.libc.free.argtypes = [C.c_void_p]

    def load_c(self, path, base=BASE, import_name=b'synthetic_add5'):
        c = Cpu()
        # Non-null test-only import marker, never invoked by these loader-only comparisons.
        imports = (Import * 1)(Import(import_name, 1))
        status = self.lib.a32_load_nso(C.byref(c), os.fsencode(path), base, SIZE, imports, 1)
        return status, c, imports

    def test_architecture_requires_evidence_and_rejects_contradictions(self):
        with self.assertRaisesRegex(ValueError, 'unknown'):
            select_arch()
        p = WORK / 'arm32.npdm'
        npdm(p, 'arm32')
        self.assertEqual(select_arch(npdm=p, module_arches=['arm32', 'arm32']), 'arm32')
        for manual, modules in [('aarch64', []), ('arm32', ['aarch64'])]:
            with self.assertRaisesRegex(ValueError, 'contradictory'):
                select_arch(manual, p, modules)
        # Address-space bits are independent of instruction-width bit 0.
        data = bytearray(p.read_bytes()); data[12] = 0xE; p.write_bytes(data)
        self.assertEqual(select_arch(npdm=p), 'arm32')
        with self.assertRaisesRegex(ValueError, 'contradictory'):
            Nso(self.path, npdm=p)
        p.write_bytes(b'META')
        with self.assertRaisesRegex(ValueError, 'malformed'):
            select_arch(npdm=p)

    def test_existing_aarch64_nso_reader(self):
        path = fixture64(WORK / 'aarch64')
        n = Nso(path)
        self.assertEqual(n.text, (0, 0x40))
        self.assertEqual(n.exports(), [('_start', 0x12, 1, 8, 0x38)])
        self.assertEqual(n.eh_start, 0)
        self.assertEqual(n.u32(8), 0xD2800020)

    def test_mapping_symbols_exclude_embedded_data_and_reject_thumb(self):
        words = self.image.code_words(BASE)
        self.assertNotIn(BASE + 0x60, words)
        self.assertIn(BASE + 0x5C, words)
        self.assertIn(BASE + 0x80, words)
        bad = WORK / 'thumb.nso'
        raw = fixture(bad)
        string_at = raw.find(b'$a\0')
        raw[string_at + 1] = ord('t'); bad.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, 'Thumb'):
            Image32(bad).code_words(BASE)

    def test_end_to_end_rel_and_rela_native_execution(self):
        for rela in (False, True):
            with self.subTest(rela=rela):
                p = WORK / ('rela.nso' if rela else 'rel.nso')
                fixture(p, rela)
                result = subprocess.run([str(WORK / 'runner'), '--guest-arch', 'arm32', '--game-profile', 'generic',
                                         str(p), hex(BASE), '0x20'], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('ARM32 PASS r0=22', result.stdout)
                status, c, imports = self.load_c(p)
                try:
                    self.assertEqual(status, 0, c.error)
                    self.assertEqual(C.string_at(c.memory, c.image_size), Image32(p).relocated(BASE, {'synthetic_add5': 0xF0000000}))
                    self.assertNotEqual(c.memory, c.base)
                    self.assertEqual(struct.unpack_from('<I', C.string_at(c.memory, c.image_size), 0x808)[0], BASE + 0x80)
                finally:
                    self.libc.free(c.memory)

    def test_generator_cli_and_profile_isolation(self):
        command = [sys.executable, str(ROOT / 'tools/gen_arm32.py'), str(self.path), str(WORK / 'cli.c'),
                   '--base', hex(BASE), '--game-profile', 'generic']
        missing = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn('NSO0 is not an ISA', missing.stderr)
        npdm(WORK / 'correct.npdm', 'arm32')
        result = subprocess.run(command + ['--npdm', str(WORK / 'correct.npdm')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((WORK / 'cli.c').read_text(), generate(self.image, BASE))
        for extra in (['--guest-arch', 'aarch64', '--npdm', str(WORK / 'correct.npdm')],
                      ['--guest-arch', 'arm32', '--game-profile', 'las']):
            result = subprocess.run(command + extra, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('patches', sys.modules)

    def test_unknown_instructions_and_exception_returns_rejected(self):
        for word in (0xEF000001, 0xEE1D0F70, 0xEE300A00, 0xE0000291, 0xFA000000, 0xE1B0F00E, 0xFFFFFFFF):
            with self.subTest(word=hex(word)), self.assertRaisesRegex(Unsupported, '0x00012340'):
                Lifter32().lift(word, 0x12340)

    def test_memory_translation_and_thumb_failures(self):
        buf = C.create_string_buffer(32)
        c = Cpu(); c.memory = C.addressof(buf); c.base = 0xFFFFFFE0; c.size = 32
        value = C.c_uint32()
        self.assertEqual(self.lib.a32_store(C.byref(c), 0xFFFFFFFC, 4, 0xA1B2C3D4), 0)
        self.assertEqual(self.lib.a32_load(C.byref(c), 0xFFFFFFFC, 4, C.byref(value)), 0)
        self.assertEqual(value.value, 0xA1B2C3D4)
        for address, width in ((0xFFFFFFFF, 4), (0, 4), (0xFFFFFFE0, 3)):
            self.assertEqual(self.lib.a32_load(C.byref(c), address, width, C.byref(value)), -1)
        self.assertEqual(self.lib.a32_branch(C.byref(c), BASE | 1), -1)
        self.assertIn(b'Thumb', c.error)
        self.assertEqual(self.lib.a32_branch(C.byref(c), BASE | 2), -1)
        c.r[15] = BASE; c.cpsr = 0x30
        self.assertEqual(self.lib.a32_run(C.byref(c), self.lib.a32_step, 10), -1)
        self.assertIn(b'Thumb', c.error)

    def test_explicit_carry_overflow_and_shift_edges(self):
        vectors = [
            (dp(4), 0xFFFFFFFF, 1, 0, 0, 0, 0x60000000),
            (dp(4), 0x7FFFFFFF, 1, 0, 0, 0x80000000, 0x90000000),
            (dp(2), 0, 1, 0, 0, 0xFFFFFFFF, 0x80000000),
            (dp(2), 0x80000000, 1, 0, 0, 0x7FFFFFFF, 0x30000000),
            (dp(5), 0xFFFFFFFF, 0, 0, 0x20000000, 0, 0x60000000),
            (dp(6), 0, 0, 0, 0, 0xFFFFFFFF, 0x80000000),
            (dp(0), 0, 0, 0, 0x10000000, 0, 0x50000000),
            (dp(13, rn=0, operand=2 | 1 << 5), 0, 0xFFFFFFFF, 0, 0x10000000, 0, 0x70000000),
            (dp(13, rn=0, operand=2 | 3 << 5), 0, 1, 0, 0x20000000, 0x80000000, 0xA0000000),
            (dp(13, rn=0, operand=2 | 16 | 3 << 8), 0, 0x80000000, 256, 0x30000000, 0x80000000, 0xB0000000),
        ]
        for word, a, b, amount, flags, value, nzcv in vectors:
            with self.subTest(word=hex(word), a=hex(a), b=hex(b)):
                c = Cpu(); c.r[1] = a; c.r[2] = b; c.r[3] = amount
                c.r[15] = BASE + self.words.index(word) * 4; c.cpsr = flags | 0x10
                self.assertEqual(self.lib.test_step(C.byref(c)), 0, c.error)
                self.assertEqual(c.r[0], value)
                self.assertEqual(c.cpsr & 0xF0000000, nzcv)
    def test_unknown_dispatch_and_budget_are_errors(self):
        status, c, imports = self.load_c(self.path)
        try:
            self.assertEqual(status, 0, c.error)
            c.r[15] = BASE + 0x60
            self.assertEqual(self.lib.a32_run(C.byref(c), self.lib.a32_step, 1), -1)
            self.assertIn(b'no compiled', c.error)
            c.r[15] = BASE + 0x20; c.r[13] = BASE + c.size - 16
            self.assertEqual(self.lib.a32_run(C.byref(c), self.lib.a32_step, 0), -1)
            self.assertIn(b'budget', c.error)
            c.r[15] = 0xF0000010
            self.assertEqual(self.lib.a32_run(C.byref(c), self.lib.a32_step, 10), -1)
            self.assertIn(b'unknown synthetic HLE', c.error)
            self.assertEqual(self.lib.a32_store(C.byref(c), BASE + 0x20, 4, 0), -1)
            self.assertIn(b'read-only', c.error)
            # Deliberate host-side corruption must not execute stale translated instructions.
            C.memset(c.memory + 0x20, 0, 4)
            c.r[15] = BASE + 0x20
            self.assertEqual(self.lib.a32_step(C.byref(c)), -1)
            self.assertIn(b'code differs', c.error)
        finally:
            self.libc.free(c.memory)

    def test_hle_checks_stack_alignment_and_preserved_registers(self):
        callback_type = C.CFUNCTYPE(C.c_int, C.POINTER(Cpu))

        @callback_type
        def bad_hle(pointer):
            pointer.contents.r[4] ^= 1
            return 0

        for aligned in (True, False):
            status, c, old_imports = self.load_c(self.path)
            imports = (Import * 1)(Import(b'synthetic_add5', C.cast(bad_hle, C.c_void_p)))
            try:
                self.assertEqual(status, 0, c.error)
                c.imports = C.addressof(imports)
                c.r[15] = 0xF0000000; c.r[14] = 0xFFFFFFF0
                c.r[13] = BASE + c.size - (16 if aligned else 15)
                self.assertEqual(self.lib.a32_run(C.byref(c), self.lib.a32_step, 10), -1)
                self.assertIn(b'preserved' if aligned else b'aligned', c.error)
            finally:
                self.libc.free(c.memory)

    def test_malformed_files_relocations_and_missing_imports(self):
        mutations = [('short', lambda b: b[:20]),
                     ('magic', lambda b: b'BAD!' + b[4:]),
                     ('compressed', lambda b: changed(b, 0xC, 1)),
                     ('segment', lambda b: changed(b, 0x18, 0xFFFFFFFF)),
                     ('dynamic', lambda b: changed(b, 0x204, 0x7FFFFFFF)),
                     ('syment64', lambda b: changed(b, 0x524, 24)),
                     ('reloc64', lambda b: changed(b, 0x844, 1027)),
                     ('target', lambda b: changed(b, 0x840, 0xFFFFFFFF)),
                     ('name', lambda b: changed(b, 0x610, 0xFFFFFFFF))]
        for name, mutate in mutations:
            with self.subTest(name=name):
                p = WORK / f'bad-{name}.nso'; raw = fixture(p); p.write_bytes(mutate(raw))
                with self.assertRaises(ValueError):
                    Image32(p)
                status, c, imports = self.load_c(p)
                try:
                    self.assertNotEqual(status, 0)
                    self.assertTrue(c.error)
                finally:
                    self.libc.free(c.memory)
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            self.image.relocated(BASE, {})
        status, c, imports = self.load_c(self.path, import_name=b'not_implemented')
        self.assertNotEqual(status, 0)
        self.assertIn(b'unresolved', c.error)
        self.libc.free(c.memory)

    @unittest.skipIf(U is None, 'Unicorn is not installed; no reference comparisons executed')
    def test_differential_against_unicorn_fixed_seed(self):
        rng = random.Random(0xA32026)
        memory = bytearray(SIZE)
        for i, word in enumerate(self.words):
            struct.pack_into('<I', memory, 4 * i, word)
        memory[0x8000:0x9000] = rng.randbytes(4096)
        # LDR PC target must stay A32 and inside mapped memory.
        struct.pack_into('<I', memory, 0x8004, BASE + 0x7000)
        native = C.create_string_buffer(bytes(memory), SIZE)
        mu = U.Uc(U.UC_ARCH_ARM, U.UC_MODE_ARM)
        mu.mem_map(BASE, SIZE)
        regs = [getattr(A, f'UC_ARM_REG_R{i}') for i in range(13)] + [A.UC_ARM_REG_SP, A.UC_ARM_REG_LR, A.UC_ARM_REG_PC]
        edge = [0, 1, 31, 32, 33, 64, 255, 256, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF]
        count = 0
        for i, word in enumerate(self.words):
            for case in range(16):
                with self.subTest(word=f'{word:08x}', case=case):
                    C.memmove(native, bytes(memory), SIZE)
                    mu.mem_write(BASE, bytes(memory))
                    c = Cpu(); c.memory = C.addressof(native); c.base = BASE; c.size = SIZE
                    state = [rng.choice(edge) if case < 11 else rng.getrandbits(32) for _ in range(16)]
                    state[1] = BASE + 0x7000 if word in (0xE12FFF11, 0xE12FFF31, 0xE1A0F001) else state[1]
                    state[3] = edge[case % len(edge)]
                    state[5] = 4; state[6] = BASE + 0x8000
                    state[13] = BASE + 0xF000; state[14] = BASE + 0x7000; state[15] = BASE + 4 * i
                    for j, value in enumerate(state): c.r[j] = value
                    c.cpsr = case << 28 | 0x10
                    mu.reg_write(A.UC_ARM_REG_CPSR, c.cpsr)
                    for reg, value in zip(regs, state): mu.reg_write(reg, value)
                    # Each instruction is in the supported, defined subset. Reference errors fail the test.
                    mu.emu_start(state[15], BASE + SIZE, count=1)
                    self.assertEqual(self.lib.test_step(C.byref(c)), 0, c.error)
                    self.assertEqual(list(c.r), [mu.reg_read(reg) for reg in regs])
                    self.assertEqual(c.cpsr & 0xF0000000, mu.reg_read(A.UC_ARM_REG_CPSR) & 0xF0000000)
                    self.assertEqual(native.raw, bytes(mu.mem_read(BASE, SIZE)))
                    count += 1
        self.assertGreater(count, 5000)
        print(f'A32 differential: {len(self.words)} encodings, {count} cases, seed=0xA32026, registers/NZCV/memory PASS', flush=True)


def changed(raw, offset, value):
    out = bytearray(raw)
    struct.pack_into('<I', out, offset, value)
    return out


if __name__ == '__main__':
    unittest.main()
