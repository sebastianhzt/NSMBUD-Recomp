"""Generate portable C for explicitly selected A32 code; no game profile is inferred."""
import argparse
from pathlib import Path
import sys

from guest_arch import select_arch
from arm32.image import Image32
from arm32.lifter import Lifter32


def generate(image, base):
    words = image.code_words(base)
    lifter = Lifter32()
    cases = []
    for pc, word in sorted(words.items()):
        body = lifter.lift(word, pc)
        cases.append(f'case 0x{pc:08x}u: {{\n'
                     f'if (word != 0x{word:08x}u) return a32_fail(c, "code differs from translated image");\n'
                     f'{body}\n}}')
    return ('/* Generated from explicitly mapped synthetic A32 code; no LAS patches. */\n'
            '#include "arm32/cpu.h"\nint a32_step(Cpu32 *c) {\nuint32_t word;\n'
            'if (a32_load(c, c->r[15], 4, &word)) return -1;\nswitch (c->r[15]) {\n'
            + '\n'.join(cases) + '\ndefault: return a32_fail(c, "no compiled A32 instruction (data/unknown target)");\n}\n}\n')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('nso')
    ap.add_argument('output')
    ap.add_argument('--base', required=True, type=lambda s: int(s, 0))
    ap.add_argument('--guest-arch', choices=('arm32', 'aarch64'))
    ap.add_argument('--npdm')
    ap.add_argument('--module-arch', choices=('arm32', 'aarch64'), action='append', default=[])
    ap.add_argument('--game-profile', required=True, choices=('generic',))
    args = ap.parse_args(argv)
    try:
        if select_arch(args.guest_arch, args.npdm, args.module_arch) != 'arm32':
            raise ValueError('this backend supports ARM/A32 only; use tools/gen.py for AArch64/LAS')
        source = generate(Image32(args.nso), args.base)
        Path(args.output).write_text(source)
    except (ValueError, OSError) as exc:
        ap.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
