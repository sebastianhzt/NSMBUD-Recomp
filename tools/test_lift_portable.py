"""Exercise the actual scalar harness MD5 against Python, including real memory changes."""
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent


class PortableScalarHarness(unittest.TestCase):
    def test_memory_digests_and_bad_index(self):
        work = ROOT / 'build/test_lift_portable'
        work.mkdir(parents=True, exist_ok=True)
        source = work / 'functions.c'
        source.write_text('''#include "cpu.h"
static void unchanged(Cpu *c) { (void)c; }
static void changed(Cpu *c) { ST8(0x7200000000ull + 0x3ffff, 0xa5); c->x[0] = 7; }
GuestFn tests[] = {unchanged, changed};
int ntests = 2;
''')
        exe = work / 'harness'
        subprocess.run([os.environ.get('CC', 'clang'), '-O1', '-Wall', '-Wextra', '-Werror',
                        '-Wno-deprecated-declarations', '-I', str(ROOT / 'runtime'),
                        str(ROOT / 'tools/test_harness.c'), str(source), '-o', str(exe)]
                       + ([] if sys.platform == 'darwin' else ['-lcrypto']), check=True)
        original = bytes(range(256)) * 1024
        (work / 'image.bin').write_bytes(bytes(4096))
        (work / 'buffer.bin').write_bytes(original)
        record = lambda index: struct.pack('<36Q', index, 0, *([0] * 34)) + bytes(512)
        cases = work / 'cases.bin'
        cases.write_bytes(record(0) + record(1) + record(0))
        command = [str(exe), str(work / 'image.bin'), str(work / 'buffer.bin'), str(cases), '0x7000000000']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line.split() for line in result.stdout.splitlines()]
        self.assertEqual(len(lines), 3)
        changed = original[:-1] + b'\xa5'
        for line, data, r0 in zip(lines, (original, changed, original), (0, 7, 0)):
            self.assertEqual(len(line), 67)
            self.assertEqual(int(line[0], 16), r0)
            self.assertEqual(line[34], hashlib.md5(data).hexdigest())
        cases.write_bytes(record(2))
        bad = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn('invalid test index', bad.stderr)


if __name__ == '__main__':
    unittest.main()
