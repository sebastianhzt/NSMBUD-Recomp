# Experimental ARM32/A32 milestone

This is an isolated experimental backend, not NSMB U Deluxe compatibility. The checkout
was on branch `work`, commit `6244eb9d4d3497094e21cac7dabe998ded1f2800`, with no tracked
changes before this work. No game files were available or used, so no game's build ID,
version, instruction set or runtime behavior has been verified. The synthetic addresses,
symbols and HLE service below belong only to our original fixtures.

## Reproduce the milestone

From the repository root, with the existing Python environment and native C compiler:

```sh
# Cloud instance: activates already installed tools; does not install anything.
source /workspace/.cloud-setup/env.sh
make arm32-demo
make arm32-test
```

`arm32-demo` creates our own NSO fixture, translates only explicitly mapped A32 code to C,
compiles it with the separate ARM32 runtime and runs it. Expected output:

```text
ARM32 PASS r0=22 sp=0x00020ff0 pc=0xfffffff0
```

The program saves/restores LR and SP, calls a synthetic imported five-argument function
through BLX, calls a guest subroutine with BL, stores its result through a relocated
guest pointer, and returns. Its result is `1+2+3+4+5+7 = 22`. The top-level return address
is a harness sentinel, not a Horizon service. Generated artifacts stay under ignored `build/`.

Equivalent explicit commands (no installations):

```sh
tools/venv/bin/python tools/arm32/fixtures.py build/arm32
tools/venv/bin/python tools/gen_arm32.py \
    build/arm32/synthetic.nso build/arm32/program.c \
    --base 0x10000 --guest-arch arm32 --npdm build/arm32/main.npdm --game-profile generic
clang -std=c11 -O2 -Wall -Wextra -Werror -Iruntime \
    runtime/arm32/core.c runtime/arm32/loader.c tools/arm32/runner.c \
    build/arm32/program.c -o build/arm32/runner
build/arm32/runner --guest-arch arm32 --game-profile generic \
    build/arm32/synthetic.nso 0x10000 0x20
```

## Architecture, profile and loader boundaries

`ARCH` in the existing Makefile still selects the **host** toolchain. `--guest-arch`
selects the **guest** instruction set. NSO0 is a container signature and never establishes
A32 versus AArch64. `tools/guest_arch.py` checks NPDM META `Flags` at offset `0xC`, bit 0
(`Is64BitInstruction`), independently of address-space bits. Without NPDM, pass an explicit
manual architecture. Conflicting manual/NPDM/per-module declarations are errors. A single
NPDM describes a process; NSO containers do not provide independent ISA evidence.
The milestone loads one module; real multi-module linking and verification are pending.

`gen_arm32.py` requires the `generic` profile and imports neither `gen.py` nor
`patches.py`. The legacy generator remains AArch64/LAS, now exposes that selection explicitly,
and its NSO reader checks a sibling `main.npdm` when present. The C LAS loader also refuses
a 32-bit NPDM before interpreting modules as ELF64. Missing NPDM preserves the legacy
AArch64 default there; it does not trigger automatic ISA detection.

The ARM32 Python and C loaders are independent of the legacy Elf64 structures. They support
the same deliberately narrow, synthetic format:

- Uncompressed NSO segments, MOD0 and bounded little-endian ELF32 dynamic tables.
- ELF32 symbols (16 bytes), REL (8 bytes), RELA (12 bytes), and REL/RELA PLT tables.
- `R_ARM_RELATIVE` (23, `B+A`) and `R_ARM_ABS32` (2, `S+A`) only, with aligned writable
  destinations, checked symbol references and overflow rejection. Unresolved imports fail.
- Translation requires bounded function symbols plus ARM `$a`/data `$d` mapping symbols;
  `$t`/Thumb mappings are rejected. Literal data, NSO headers and MOD0 are not translated.

Compressed/hashed NSOs, other relocation types (including GLOB_DAT/JUMP_SLOT), special symbol
sections, stripped binaries without mappings, runtime relocation machinery and commercial
modules are **not supported** by this milestone. They must not be relabeled as working
because the synthetic loader passes. Normal ARM32 NSO support needs checks against the
user's authorized files, their NPDM and the actual dynamic tables.

## Implemented CPU semantics and ABI

`runtime/arm32/Cpu32` has separate `uint32_t r[16]` and CPSR. r13 is SP, r14 is LR,
r15 records the current instruction; supported architectural PC reads use instruction+8.
Arithmetic wraps to 32 bits, with N/Z/C/V updates and conditional execution. There is no
reuse or reinterpretation of the legacy AArch64 `Cpu` layout.

Implemented instruction subsets: AND/EOR/ORR/BIC/MOV/MVN; ADD/ADC/SUB/SBC/RSB/RSC;
TST/TEQ/CMP/CMN; immediate/register LSL/LSR/ASR/ROR and RRX; byte/halfword/word loads and
stores, LDRSB/LDRSH, supported pre/post-index/writeback; B/BL/BX and register BLX;
supported MOV/LDR-to-PC and LR returns. Unsupported/unpredictable PC/writeback combinations
are rejected rather than guessed. PC in register-specified shifts, exception returns,
Thumb interworking and unsupported encodings fail with the instruction address.

Guest addresses always remain 32-bit numbers. Loads/stores translate through an arbitrary
host allocation with bounds checked using wider arithmetic. There are no narrowed host
pointers, low fixed-address mappings or >4 GiB guest bases. Text/rodata are write protected
by the guest accessors. Dispatch detects uncompiled/data targets and changed translated code.
The fixture memory allocation is capped at 16 MiB; this is not a full process address space.

Only the **synthetic** AAPCS32 service ABI is defined: r0-r3 hold the first four uint32
arguments, subsequent words are on an 8-byte aligned guest stack, and r0 carries a uint32
result. The wrapper checks preservation of r4-r11, SP and LR. r12 and NZCV are caller-clobbered.
`synthetic_add5` really reads the fifth stack argument and computes its result; there are
no success-returning unknown-service stubs. Host callbacks, guest callbacks from HLE,
64-bit/FP/aggregate arguments and returns are pending, not silently adapted.

This ABI does **not** implement Horizon's AArch32 SVC register conventions, split 64-bit
values, thread initialization, TLS/CP15 or callbacks. All SVC and coprocessor/TLS instructions
are rejected. VFP/NEON, multiply/divide, block transfers, exclusive accesses, barriers and
Thumb/T32 (variable lengths, IT state, BX/BLX interworking) are also pending.

Capstone 5.x is used only for valid instruction/family identification; bit decoding and
semantics are implemented in this backend. The tested installation is Capstone 5.0.7,
BSD licensed, and Unicorn 2.1.4. No decoder source or license files were copied; preserve
[Capstone's license](https://github.com/capstone-engine/capstone/blob/5.0.7/LICENSE.TXT)
when redistributing it. Tests do not install dependencies. If Unicorn is absent, the
differential test is explicitly reported as skipped.

## Validation performed

| Check | Result |
| --- | --- |
| `make arm32-demo` | Native execution returns r0=22 |
| `make arm32-test` | 12 tests passed, none skipped |
| A32 differential, seed `0xA32026` | 371 encodings, 5,936 executed cases; all r0-r15, defined NZCV and memory match Unicorn |
| Explicit edge vectors | Carry, signed overflow, borrow, RRX, shift-by-32 and register shift count 256 pass |
| REL and RELA fixtures | Both execute; Python/C relocated images match byte for byte |
| Negative cases | Contradictory ISA, malformed files/tables, unsupported instruction/relocation, unresolved HLE, OOB/wraparound, Thumb, code/data dispatch, HLE ABI violations all reject |
| GCC AddressSanitizer + UBSan demo | r0=22; no reported violations |
| Existing AArch64 NSO reader | Original synthetic ELF64 fixture parses and exports correctly |
| Existing AArch64 SIMD suite, seed 1 | 512 encodings, 4,096 executed cases; zero failing cases; validity sample 2,048 encodings agrees with Unicorn |
| Linux application build and launcher | Build passed; real Vulkan launcher rendered 1280x720 |
| Existing `tools/test_lift.py` | Compilation failed on Linux: `CommonCrypto/CommonDigest.h` is macOS-only; native scalar comparisons did not execute |
| Game execution, full NSMB port, graphics | Not tested; no user game files available |

Existing SIMD test maintenance was limited to matching the current three-argument profiler
declaration, linking libm on Linux, and failing on incomplete/empty harness output or runner
failure. No assertions or comparisons were disabled. The original scalar harness was left
unchanged; its platform blocker remains visible in `build/aarch64-lift.log`.

Reproduce the AArch64 checks:

```sh
tools/venv/bin/python tools/test_simd.py --encodings 512 --cases 8 --jobs 5 --no-validity --seed 1
make -j5 DEPS=/workspace/.cloud-setup/sysroot/usr
bash /workspace/.cloud-setup/launcher-smoke.sh
```

Logs from this cloud instance: `build/arm32-tests.log`, `build/arm32-demo.log`,
`build/arm32-sanitize.log`, `build/aarch64-simd.log`, `build/aarch64-build.log`,
`build/aarch64-launcher.log`, and the failed `build/aarch64-lift.log`. Clang's sanitizer
runtime is not installed; the sanitizer check used the existing GCC runtimes without
installing anything:

```sh
gcc -std=c11 -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -Iruntime \
    runtime/arm32/core.c runtime/arm32/loader.c tools/arm32/runner.c \
    build/arm32/program.c -o build/arm32/runner-sanitize
build/arm32/runner-sanitize --guest-arch arm32 --game-profile generic \
    build/arm32/synthetic.nso 0x10000 0x20
```

## Next blockers, in order

1. Inspect authorized local NSMB U Deluxe NPDM/modules and version/build IDs. No compatibility
   inference from the repository name or the SaltyNX classification is sufficient.
2. Establish the actual ARM32 NSO relocation, symbol and code/data layout; extend the bounded
   loaders and verify multi-module resolution before lifting real executable code.
3. Specify and test Horizon AArch32 SVC/TLS/thread/callback ABI independently of the synthetic
   AAPCS32 example; add only the first services actually required.
4. Add needed instruction families and Thumb with differential tests. Keep rendering and
   broader game behavior outside this first milestone.

No installations, commits, pushes, publications or destructive Git operations were performed.
