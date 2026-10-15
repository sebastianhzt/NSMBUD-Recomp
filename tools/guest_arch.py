"""Guest instruction set selection; deliberately independent of the host CPU and game profile."""
from pathlib import Path


def select_arch(manual=None, npdm=None, module_arches=()):
    choices = {'arm32', 'aarch64'}
    evidence = []
    if manual is not None:
        evidence.append(('manual selection', manual))
    if npdm is not None:
        data = Path(npdm).read_bytes()
        if len(data) < 0x80 or data[:4] != b'META':
            raise ValueError('malformed NPDM META header')
        # META flags, offset 0xC bit 0: Is64BitInstruction (not the address-space type).
        evidence.append(('NPDM Is64BitInstruction', 'aarch64' if data[0xC] & 1 else 'arm32'))
    evidence.extend((f'module {i}', arch) for i, arch in enumerate(module_arches))
    if not evidence:
        raise ValueError('guest architecture is unknown: supply --guest-arch or --npdm; NSO0 is not an ISA marker')
    if any(arch not in choices for _, arch in evidence):
        raise ValueError(f'unknown guest architecture: {evidence}')
    if len({arch for _, arch in evidence}) != 1:
        raise ValueError(f'contradictory guest architectures: {evidence}')
    return evidence[0][1]
