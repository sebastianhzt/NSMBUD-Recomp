"""Generate portable C for mapped ARM32 code (opt-in Thumb); no game profile is inferred."""
import argparse
from pathlib import Path
import sys

from guest_arch import select_arch
from arm32.image import Image32
from arm32.lifter import Lifter32
from arm32.thumb import ThumbLifter


def generate(image, base, thumb=False):
    instructions = image.code_instructions(base, allow_thumb=thumb)
    lifter = Lifter32()
    thumb_lifter = ThumbLifter() if thumb else None
    cases = []
    for pc, (mode, data) in sorted(instructions.items()):
        word = int.from_bytes(data, 'little')
        body = thumb_lifter.lift(data, pc) if mode == '$t' else lifter.lift(word, pc)
        check = f'if (!!(c->cpsr & 0x20) != {int(mode == "$t")}) return a32_fail(c, "wrong ARM/Thumb execution state");\n'
        cases.append(f'case 0x{pc:08x}u: {{\n'
                     + check + f'if (a32_load(c, c->r[15], {len(data)}, &word)) return -1;\n'
                     f'if (word != 0x{word:08x}u) return a32_fail(c, "code differs from translated image");\n'
                     f'{body}\n}}')
    return ('/* Generated from explicitly mapped synthetic A32 code; no LAS patches. */\n'
            '#include "arm32/cpu.h"\nint a32_step(Cpu32 *c) {\nuint32_t word;\n'
            + ('' if thumb else 'if (c->cpsr & 0x20) return a32_fail(c, "Thumb/T32 code not enabled");\n')
            + 'switch (c->r[15]) {\n'
            + '\n'.join(cases) + '\ndefault: return a32_fail(c, "no compiled A32 instruction (data/unknown target)");\n}\n}\n')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('nso')
    ap.add_argument('output')
    ap.add_argument('--base', required=True, type=lambda s: int(s, 0))
    ap.add_argument('--guest-arch', choices=('arm32', 'aarch64'))
    ap.add_argument('--npdm')
    ap.add_argument('--thumb', action='store_true', help='enable the experimental mapped Thumb subset and interworking')
    ap.add_argument('--module-arch', choices=('arm32', 'aarch64'), action='append', default=[])
    ap.add_argument('--game-profile', required=True, choices=('generic',))
    args = ap.parse_args(argv)
    try:
        if select_arch(args.guest_arch, args.npdm, args.module_arch) != 'arm32':
            raise ValueError('this backend supports ARM/A32 only; use tools/gen.py for AArch64/LAS')
        source = generate(Image32(args.nso), args.base, thumb=args.thumb)
        Path(args.output).write_text(source)
    except (ValueError, OSError) as exc:
        ap.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
