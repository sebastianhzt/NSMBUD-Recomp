"""A bounded ARMv7 Thumb subset: explicit semantics, halfword lengths and runtime IT state.

Capstone 5.x validates instruction encodings only; it does not implement these semantics.
Unknown 16/32-bit encodings, privileged state and unsupported PC forms are errors.
"""
import capstone
from .lifter import Unsupported


def instruction_size(halfword):
    return 4 if halfword >> 11 in (0b11101, 0b11110, 0b11111) else 2


def signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


class ThumbLifter:
    def __init__(self):
        if not capstone.__version__.startswith('5.'):
            raise ValueError('Thumb backend requires the already installed Capstone 5.x')
        self.md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_LITTLE_ENDIAN)

    def lift(self, data, pc):
        ins = next(self.md.disasm(data, pc, count=1), None)
        size = instruction_size(int.from_bytes(data[:2], 'little'))
        label = f'{ins.mnemonic} {ins.op_str}' if ins else data.hex()

        def reject(reason):
            raise Unsupported(f'Thumb 0x{pc:08x}: {label}: {reason}')

        if len(data) != size or not ins or ins.size != size:
            reject('truncated/invalid Thumb encoding')
        reg = lambda n: f'0x{(pc + 4) & 0xffffffff:08x}u' if n == 15 else f'c->r[{n}]'
        h = int.from_bytes(data[:2], 'little')
        body, control, branch = [], 0, False
        if size == 4:
            lo = int.from_bytes(data[2:], 'little')
            if h & 0xfbf0 in (0xf240, 0xf2c0) and not lo & 0x8000:
                rd = lo >> 8 & 15
                if rd in (13, 15):
                    reject('MOVW/MOVT SP/PC unsupported')
                imm = (h & 15) << 12 | (h >> 10 & 1) << 11 | (lo >> 12 & 7) << 8 | (lo & 255)
                body.append(f'c->r[{rd}] = ' + (f'(c->r[{rd}] & 0xffffu) | 0x{imm << 16:08x}u;' if h & 0x80 else f'{imm}u;'))
            elif h & 0xf800 == 0xf000 and lo & 0xd000 in (0xc000, 0xd000):
                exchange = not bool(lo & 0x1000)
                if exchange and lo & 1:
                    reject('reserved BLX immediate low bit')
                s, j1, j2 = h >> 10 & 1, lo >> 13 & 1, lo >> 11 & 1
                imm = s << 24 | ((~(j1 ^ s)) & 1) << 23 | ((~(j2 ^ s)) & 1) << 22 | (h & 1023) << 12 | (lo & 2047) << 1
                origin = (pc + 4) & ~3 if exchange else pc + 4
                target = (origin + signed(imm, 25)) & 0xffffffff
                body += [f'c->r[14] = 0x{(pc + 4) | 1:08x}u;',
                         f'return {"a32_exchange" if exchange else "t32_branch"}(c, 0x{target:08x}u);']
                control, branch = 1, True
            else:
                reject('32-bit instruction not implemented')
        elif h & 0xff00 == 0xbf00:
            if h & 15:
                condition, mask = h >> 4 & 15, h & 15
                if condition >= 14:
                    reject('IT AL/NV outside supported subset')
                return (f'if (t32_start_it(c, {condition}, {mask})) return -1;\n'
                        f'c->r[15] = 0x{pc + 2:08x}u; return 0;')
            if h != 0xbf00:
                reject('hint not implemented')
            # NOP still consumes an IT slot, including when its condition is false.
        elif h & 0xe000 == 0 and h & 0x1800 != 0x1800:
            kind, amount, rm, rd = h >> 11 & 3, h >> 6 & 31, h >> 3 & 7, h & 7
            body += [f'A32Shift sh = a32_shift(c->r[{rm}], {kind}, {amount}, 0, c->cpsr >> 29 & 1);',
                     f'c->r[{rd}] = sh.value;', 'if (implicit_flags) a32_logic_flags(c, sh.value, sh.carry);']
        elif h & 0xf800 == 0x1800:
            sub, immediate, rn, rd = h >> 9 & 1, h >> 10 & 1, h >> 3 & 7, h & 7
            operand = str(h >> 6 & 7) + 'u' if immediate else reg(h >> 6 & 7)
            body.append(f'c->r[{rd}] = a32_add(c, c->r[{rn}], {"~" if sub else ""}({operand}), {sub}, implicit_flags);')
        elif h & 0xe000 == 0x2000:
            op, rd, imm = h >> 11 & 3, h >> 8 & 7, h & 255
            if op == 0:
                body += [f'c->r[{rd}] = {imm}u;', f'if (implicit_flags) a32_logic_flags(c, c->r[{rd}], c->cpsr >> 29 & 1);']
            else:
                sub = op in (1, 3)
                expr = f'a32_add(c, c->r[{rd}], {"~" if sub else ""}{imm}u, {int(sub)}, {"1" if op == 1 else "implicit_flags"})'
                body.append(f'(void){expr};' if op == 1 else f'c->r[{rd}] = {expr};')
        elif h & 0xfc00 == 0x4000:
            op, rm, rd = h >> 6 & 15, h >> 3 & 7, h & 7
            if op == 13:
                reject('MUL not implemented')
            if op in (2, 3, 4, 7):
                kind = {2: 0, 3: 1, 4: 2, 7: 3}[op]
                body += [f'A32Shift sh = a32_shift(c->r[{rd}], {kind}, c->r[{rm}], 1, c->cpsr >> 29 & 1);',
                         f'c->r[{rd}] = sh.value;', 'if (implicit_flags) a32_logic_flags(c, sh.value, sh.carry);']
            elif op in (5, 6, 9, 10, 11):
                a, b, carry = reg(rd), reg(rm), '0'
                if op in (5, 6): carry = 'c->cpsr >> 29 & 1'
                if op in (6, 10): b = '~' + b
                if op == 9: a, b = '0u', '~' + b
                if op in (9, 10): carry = '1'
                expr = f'a32_add(c, {a}, {b}, {carry}, {"1" if op in (10, 11) else "implicit_flags"})'
                body.append(f'(void){expr};' if op in (10, 11) else f'c->r[{rd}] = {expr};')
            else:
                expr = {0: f'{reg(rd)} & {reg(rm)}', 1: f'{reg(rd)} ^ {reg(rm)}',
                        8: f'{reg(rd)} & {reg(rm)}', 12: f'{reg(rd)} | {reg(rm)}',
                        14: f'{reg(rd)} & ~{reg(rm)}', 15: f'~{reg(rm)}'}[op]
                body.append(f'uint32_t value = {expr};')
                body.append('if (' + ('1' if op == 8 else 'implicit_flags') + ') a32_logic_flags(c, value, c->cpsr >> 29 & 1);')
                if op != 8: body.append(f'c->r[{rd}] = value;')
        elif h & 0xfc00 == 0x4400:
            op, rm, rd = h >> 8 & 3, h >> 3 & 15, (h & 7) | (h >> 4 & 8)
            if op == 3:
                if rm == 15 or h & 7:
                    reject('BX/BLX PC or reserved low bits unsupported')
                body.append(f'uint32_t target = {reg(rm)};')
                if h & 0x80: body.append(f'c->r[14] = 0x{(pc + 2) | 1:08x}u;')
                body.append('return a32_exchange(c, target);')
                control, branch = 1, True
            else:
                if rd == 15 or (op == 1 and rm == 15): reject('high-register PC destination/CMP unsupported')
                if op == 0: body.append(f'c->r[{rd}] += {reg(rm)};')
                elif op == 1: body.append(f'(void)a32_add(c, {reg(rd)}, ~({reg(rm)}), 1, 1);')
                else: body.append(f'c->r[{rd}] = {reg(rm)};')
        elif h & 0xf800 == 0x4800:
            rd, offset = h >> 8 & 7, (h & 255) * 4
            body += ['uint32_t value;', f'if (a32_load(c, 0x{((pc + 4) & ~3) + offset:08x}u, 4, &value)) return -1;', f'c->r[{rd}] = value;']
        elif h & 0xf000 in (0x5000, 0x6000, 0x7000, 0x8000, 0x9000):
            rd, rn = h & 7, h >> 3 & 7
            sign = False
            if h & 0xf000 == 0x5000:
                op = h >> 9 & 7
                load, width, sign = op >= 3, (4, 2, 1, 1, 4, 2, 1, 2)[op], op in (3, 7)
                address = f'{reg(rn)} + {reg(h >> 6 & 7)}'
            elif h & 0xf000 == 0x9000:
                rd, load, width = h >> 8 & 7, bool(h & 0x800), 4
                address = f'c->r[13] + {(h & 255) * 4}u'
            else:
                load = bool(h & 0x800)
                width = 2 if h & 0xf000 == 0x8000 else (1 if h & 0x1000 else 4)
                address = f'{reg(rn)} + {(h >> 6 & 31) * width}u'
            if load:
                body += ['uint32_t value;', f'if (a32_load(c, {address}, {width}, &value)) return -1;']
                body.append(f'c->r[{rd}] = ' + (f'(uint32_t)(int32_t)(int{width * 8}_t)value;' if sign else 'value;'))
            else:
                body.append(f'if (a32_store(c, {address}, {width}, {reg(rd)})) return -1;')
        elif h & 0xf000 == 0xa000:
            base = 'c->r[13]' if h & 0x800 else f'0x{(pc + 4) & ~3:08x}u'
            body.append(f'c->r[{h >> 8 & 7}] = {base} + {(h & 255) * 4}u;')
        elif h & 0xff00 == 0xb000:
            body.append(f'c->r[13] {"-=" if h & 128 else "+="} {(h & 127) * 4}u;')
        elif h & 0xf000 == 0xd000 and h >> 8 & 15 < 14:
            target = (pc + 4 + signed(h & 255, 8) * 2) & 0xffffffff
            body.append(f'if (a32_condition(c, {h >> 8 & 15})) return t32_branch(c, 0x{target:08x}u);')
            control = 2
        elif h & 0xf800 == 0xe000:
            target = (pc + 4 + signed(h & 2047, 11) * 2) & 0xffffffff
            body.append(f'return t32_branch(c, 0x{target:08x}u);')
            control, branch = 1, True
        else:
            reject('16-bit instruction not implemented (including SVC/TLS/privileged state)')
        if not branch: body.append(f'c->r[15] = 0x{pc + size:08x}u; return 0;')
        return (f'int execute, implicit_flags;\nif (t32_predicate(c, {control}, &execute, &implicit_flags)) return -1;\n'
                '(void)implicit_flags;\n'
                f'if (!execute) {{ c->r[15] = 0x{pc + size:08x}u; return 0; }}\n' + '\n'.join(body))
