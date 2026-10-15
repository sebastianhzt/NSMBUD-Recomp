"""ARM/A32 -> C, with instruction semantics independent of Capstone disassembly.

Capstone (BSD, installed separately) is a validity/encoding-family gate; we decode bits
and implement arithmetic, shifts, flags and addressing explicitly. No Thumb decoder.
"""
import struct
import capstone
from capstone import arm as A


class Unsupported(ValueError):
    pass


class Lifter32:
    def __init__(self):
        if not capstone.__version__.startswith('5.'):
            raise ValueError('A32 backend requires Capstone 5.x (BSD license); no automatic installation')
        self.md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM | capstone.CS_MODE_LITTLE_ENDIAN)

    def lift(self, word, pc):
        ins = next(self.md.disasm(struct.pack('<I', word), pc, count=1), None)
        label = f'{ins.mnemonic} {ins.op_str}' if ins else f'word 0x{word:08x}'

        def reject(reason):
            raise Unsupported(f'ARM32 0x{pc:08x}: {label}: {reason}')

        if not ins or word >> 28 == 15:
            reject('unsupported/reserved encoding or Thumb BLX immediate')
        cond = word >> 28
        reg = lambda r: f'0x{(pc + 8) & 0xFFFFFFFF:08x}u' if r == 15 else f'c->r[{r}]'
        body = []
        branch = False
        if ins.id in (A.ARM_INS_BX, A.ARM_INS_BLX) and word & 0x0ffffff0 in (0x012fff10, 0x012fff30):
            rm = word & 15
            if rm == 15:
                reject('BX/BLX PC is outside the supported subset')
            body.append(f'uint32_t target = {reg(rm)};')
            if ins.id == A.ARM_INS_BLX:
                body.append(f'c->r[14] = 0x{(pc + 4) & 0xFFFFFFFF:08x}u;')
            body.append('return a32_branch(c, target);')
            branch = True
        elif ins.id in (A.ARM_INS_B, A.ARM_INS_BL) and (word >> 25) & 7 == 5:
            imm = word & 0xffffff
            if imm & 0x800000:
                imm -= 0x1000000
            target = (pc + 8 + imm * 4) & 0xFFFFFFFF
            if ins.id == A.ARM_INS_BL:
                body.append(f'c->r[14] = 0x{(pc + 4) & 0xFFFFFFFF:08x}u;')
            body.append(f'return a32_branch(c, 0x{target:08x}u);')
            branch = True
        elif ins.id in (A.ARM_INS_LDR, A.ARM_INS_STR, A.ARM_INS_LDRB, A.ARM_INS_STRB,
                        A.ARM_INS_LDRH, A.ARM_INS_STRH, A.ARM_INS_LDRSB, A.ARM_INS_LDRSH):
            rn, rd = word >> 16 & 15, word >> 12 & 15
            pre, up, wb, load = (word >> i & 1 for i in (24, 23, 21, 20))
            writeback = bool(wb or not pre)
            if (writeback and (rn == 15 or rn == rd)) or (rd == 15 and ins.id != A.ARM_INS_LDR):
                reject('unsupported/unpredictable PC or writeback combination')
            if word >> 26 & 3 == 1:
                if not pre and wb:
                    reject('unprivileged transfer')
                width = 1 if word & (1 << 22) else 4
                signed = False
                if word & (1 << 25):
                    if word & 16:
                        reject('register-specified memory shift')
                    rm = word & 15
                    if rm == 15:
                        reject('PC as offset register')
                    offset = f'a32_shift({reg(rm)}, {(word >> 5) & 3}, {(word >> 7) & 31}, 0, c->cpsr >> 29 & 1).value'
                else:
                    offset = f'{word & 4095}u'
            elif word & 0x0e000090 == 0x00000090:
                width = 1 if ins.id == A.ARM_INS_LDRSB else 2
                signed = ins.id in (A.ARM_INS_LDRSB, A.ARM_INS_LDRSH)
                if word & (1 << 22):
                    offset = f'{((word >> 4) & 0xf0) | (word & 15)}u'
                else:
                    if word & 0xf00 or word & 15 == 15:
                        reject('invalid halfword offset register')
                    offset = reg(word & 15)
            else:
                reject('unsupported memory encoding')
            body += [f'uint32_t base = {reg(rn)}, off = {offset};',
                     f'uint32_t adjusted = base {"+" if up else "-"} off;',
                     f'uint32_t address = {"adjusted" if pre else "base"};']
            if load:
                body += ['uint32_t value;', f'if (a32_load(c, address, {width}, &value)) return -1;']
                if signed:
                    body.append(f'value = (uint32_t)(int32_t)(int{width * 8}_t)value;')
                if rd != 15:
                    body.append(f'c->r[{rd}] = value;')
            else:
                body.append(f'if (a32_store(c, address, {width}, {reg(rd)})) return -1;')
            if writeback:
                body.append(f'c->r[{rn}] = adjusted;')
            if load and rd == 15:
                body.append('return a32_branch(c, value);')
                branch = True
        else:
            allowed = {getattr(A, 'ARM_INS_' + n) for n in (
                'AND', 'EOR', 'SUB', 'RSB', 'ADD', 'ADC', 'SBC', 'RSC', 'TST', 'TEQ', 'CMP', 'CMN',
                'ORR', 'MOV', 'BIC', 'MVN', 'LSL', 'LSR', 'ASR', 'ROR', 'RRX')}
            if ins.id not in allowed or word >> 26 & 3:
                reject('instruction family not implemented (including SVC/TLS/VFP/NEON)')
            op, flags, rn, rd = word >> 21 & 15, word >> 20 & 1, word >> 16 & 15, word >> 12 & 15
            test = op in (8, 9, 10, 11)
            if test and (not flags or rd != 0):
                reject('noncanonical test encoding')
            if rd == 15 and flags and not test:
                reject('exception return/CPSR restore is unsupported')
            if word & (1 << 25):
                rot, imm = (word >> 8 & 15) * 2, word & 255
                value = ((imm >> rot) | (imm << ((32 - rot) & 31))) & 0xffffffff
                body.append(f'A32Shift sh = {{0x{value:08x}u, {str(value >> 31) + "u" if rot else "c->cpsr >> 29 & 1"}}};')
            else:
                by_reg = bool(word & 16)
                if by_reg and (word & 128 or (word >> 8 & 15) == 15 or (word & 15) == 15 or rn == 15):
                    reject('PC/register-specified shift combination is unsupported')
                amount = reg(word >> 8 & 15) if by_reg else str(word >> 7 & 31)
                body.append(f'A32Shift sh = a32_shift({reg(word & 15)}, {(word >> 5) & 3}, {amount}, {int(by_reg)}, c->cpsr >> 29 & 1);')
            if op not in (13, 15):
                body.append(f'uint32_t a = {reg(rn)};')
            body.append('uint32_t b = sh.value, result;')
            arithmetic = {2: ('a', '~b', '1'), 3: ('b', '~a', '1'), 4: ('a', 'b', '0'),
                          5: ('a', 'b', 'c->cpsr >> 29 & 1'), 6: ('a', '~b', 'c->cpsr >> 29 & 1'),
                          7: ('b', '~a', 'c->cpsr >> 29 & 1'), 10: ('a', '~b', '1'), 11: ('a', 'b', '0')}
            if op in arithmetic:
                a, b, cy = arithmetic[op]
                body.append(f'result = a32_add(c, {a}, {b}, {cy}, {int(bool(flags))});')
            else:
                expr = {0: 'a & b', 1: 'a ^ b', 8: 'a & b', 9: 'a ^ b', 12: 'a | b',
                        13: 'b', 14: 'a & ~b', 15: '~b'}[op]
                body.append(f'result = {expr};')
                if flags:
                    body.append('a32_logic_flags(c, result, sh.carry);')
            if test:
                body.append('(void)result; // comparison updates flags only')
            else:
                if rd == 15:
                    body.append('return a32_branch(c, result);')
                    branch = True
                else:
                    body.append(f'c->r[{rd}] = result;')
        if not branch:
            body.append(f'c->r[15] = 0x{(pc + 4) & 0xFFFFFFFF:08x}u; return 0;')
        return (f'if (!a32_condition(c, {cond})) {{ c->r[15] = 0x{(pc + 4) & 0xFFFFFFFF:08x}u; return 0; }}\n'
                + '\n'.join(body))
