# Second milestone: synthetic ARM/Thumb interworking

This work continues the first milestone in the same workspace and on the same base commit
`6244eb9d4d3497094e21cac7dabe998ded1f2800`. No upstream changes were fetched or merged.
The original A32 demo and tests remain available and pass with their original results.
All executable fixtures and translated output used here are original synthetic programs.
No NSMB U Deluxe files were found, downloaded or executed, and no compatibility is claimed.

## Baseline and recovery

Before editing, the complete first-milestone changes were saved outside the checkout:

```text
/workspace/recovery/milestone1-before-thumb-20261015T185229Z/
  changes.patch          # includes tracked changes and new source files
  changed-files.tar.gz   # exact changed/new file contents
  manifest.json          # SHA256 per file and exact base commit
  base.bundle            # recoverable local Git base, no network required
  RESTORE.txt
  baseline/              # original logs and repeated demo/tests
```

The archive contents were verified against the SHA256 manifest, the bundle was verified,
and the first patch was checked in reverse against the unchanged first-milestone workspace.
The final combined recovery archive is `/workspace/recovery/NSMBUD-milestones-1-and-2.tar.gz`;
it includes both snapshots, commands and results. Download that archive to retain a copy
outside the lifetime of this cloud workspace. Local backup files alone do not guarantee
survival of environment deletion. No commits, pushes or publications were made.

The baseline was repeated before the new implementation:

```sh
source /workspace/.cloud-setup/env.sh
make arm32-demo  # r0=22
make arm32-test  # 12 tests; 371 encodings; 5,936 cases, seed 0xA32026
```

### What the initial-screen test actually proves

`build/lasrecomp` is the native Linux **x86-64 host executable** inherited from LAS Recomp.
Under Xvfb, it rendered its game-file configuration interface using software Vulkan
(Mesa llvmpipe), producing a 1280x720 PNG. That test exercised the host launcher/UI,
its libraries and rendering setup. It did not load or execute guest AArch64 game code,
guest ARM32 game code, or any NSMB U Deluxe files. It is not a game-boot test.

## Portable AArch64 scalar test

`tools/test_harness.c` originally computed an MD5 of all 256 KiB of its guest test buffer
with CommonCrypto. Linux lacked that macOS header. The checksum operation and byte range
are preserved:

- macOS (`__APPLE__`) continues to use `CC_MD5` from CommonCrypto, linked as before.
- Linux uses `EVP_Digest(..., EVP_md5(), ...)` and links `libcrypto`.
- EVP errors or an incorrect digest length fail the runner. No constant/fake digest,
  disabled checksum, disabled provider policy or verification bypass is used.
- Linux also links libm, required by the existing portable floating-point helpers.

OpenSSL headers and libcrypto were already installed in this workspace; no dependencies
were installed. Another Linux environment needs the OpenSSL development headers and library
(Debian package `libssl-dev`) before compiling this test. Missing dependencies must be
reported rather than installed automatically. macOS execution was unavailable here;
the CommonCrypto branch was preserved but was not runtime-tested on macOS.

`test_lift.py` now rejects missing/malformed output records, zero-case runs and native-runner
errors, and returns a failure status for semantic differences. Its existing register,
flags, vector and MD5 comparisons remain active. The format remains 67 fields per case.
`test_lift_portable.py` executes the actual C harness against Python MD5 for intact,
modified and restored memory, and checks rejection of an invalid function index.

New scalar evidence: **15 AArch64 encodings, 45 cases actually executed, zero differences**,
using the original synthetic ELF64 fixture. This is separate from the **512 encodings /
4,096 SIMD cases** that already passed in milestone 1 and passed again here. Neither run
uses game executables or establishes game compatibility.

## Thumb scope and execution model

Thumb is opt-in with `gen_arm32.py --thumb`. The default `arm32-demo` remains the original
A32-only fixture. `$a`, `$t` and `$d` mapping symbols distinguish ARM code, Thumb code and
data; function entry tags must agree with the mappings. A second halfword, truncated wide
instruction, misaligned instruction or mapping boundary inside an instruction cannot
be treated as an independent instruction. Code bytes and execution mode are checked at
dispatch, including both halfwords of a 32-bit Thumb instruction.

The CPU layout is unchanged. CPSR.T records ARM/Thumb state and CPSR's architectural IT
fields encode ITSTATE. The runtime advances an IT slot even when its condition is false.
For supported 16-bit instructions, implicit flag updates are suppressed inside IT;
comparisons retain their explicit flag semantics. All 14 supported conditions, all 15
nonempty IT masks and all 16 NZCV combinations were compared with Unicorn. AL/NV IT,
nested IT and control flow before the final IT slot are explicitly rejected.

Supported 16-bit families include immediate shifts, low-register arithmetic/logical
operations (except MUL), comparisons, selected high-register ADD/CMP/MOV, literal/ADR
PC-relative addressing, byte/halfword/word loads and stores, signed register-offset loads,
SP-relative memory/adjustment, conditional/unconditional B, NOP, IT and register BX/BLX.
Supported 32-bit Thumb instructions are MOVW, MOVT, BL and BLX immediate. Other wide
encodings, privileged operations, SVC, VFP/NEON and other unimplemented families fail
with their guest address. This is a tested subset, not general Thumb-2 support.

PC reads use ARM's +8 or Thumb's +4, with word alignment where required for literal/ADR
and BLX-immediate origins. Instruction lengths determine the next PC. BX/BLX use the
target's low bit for the new state; Thumb PCs retain halfword alignment. ARM targets
must be word aligned. Thumb calls tag LR with bit 0, and interworking returns restore
state through BX. The synthetic top-level exit sentinel is accepted only in ARM state.

The demo performs this original sequence:

```text
ARM entry -> BLX to Thumb -> BL to Thumb leaf -> BX back to Thumb
          -> BLX to ARM leaf -> BX back to Thumb -> BX back to ARM -> return
```

It uses ITE/IT, MOVW/MOVT, checked loads/stores and guest subroutine calls. The expected
result is `r0=20`, with `0x56781234` stored in guest memory and SP restored. Register-BLX
and immediate-BLX variants both match Unicorn for all registers, defined CPSR state and
the entire allocated guest memory. The routines are controlled synthetic code, not an
implementation or verification of a commercial SDK ABI.

### Service boundaries

No Horizon SVC, TLS, thread or service implementation was added. The only HLE interface
remains the explicit synthetic five-uint32-argument example from milestone 1; the new
interworking fixtures call guest routines, not kernel services. Unknown services fail.

Before implementing a real SVC, its contract must identify the exact guest encoding,
selector, argument/result/error registers, placement of split 64-bit arguments, preserved
state, memory direction/bounds and callbacks from the actual SDK/binary evidence. Before
implementing TLS, identify the actual CP15/TLS access sequence, per-thread guest address,
layout and lifetime. None is inferred from AArch64 register conventions or the game name.
Those contracts are pending evidence; SVC/TLS instructions currently stop explicitly.

## Commands and final results

Run from the existing checkout, without installing anything:

```sh
source /workspace/.cloud-setup/env.sh
make arm32-demo
make arm32-test
make thumb-demo
make thumb-test
tools/venv/bin/python tools/test_lift_portable.py -v
tools/venv/bin/python tools/test_lift.py build/test_arm32/aarch64/synthetic64.nso 0x7000000000 100 1
tools/venv/bin/python tools/test_simd.py --encodings 512 --cases 8 --jobs 5 --no-validity --seed 1
make -j5 DEPS=/workspace/.cloud-setup/sysroot/usr
bash /workspace/.cloud-setup/launcher-smoke.sh
```

| Check | Final result |
| --- | --- |
| Original A32 demo and suite | r0=22; 12 tests; 5,936 differential cases pass, no skips |
| Thumb demo | r0=20, checked memory and return to ARM |
| Thumb suite | 7 tests pass, no skips |
| Thumb single instructions | 99 encodings, 1,584 cases, seed `0x732026`, all registers/defined flags/memory match |
| IT blocks | 210 condition/mask blocks, 3,360 flag combinations match |
| Last IT slot BX/BLX | 64 conditional interworking cases match |
| Full mixed-state programs | Both register/immediate variants match all registers/state/memory |
| Negative tests | Truncated/misaligned/wrong-mode code, unsupported instructions, nested/invalid IT, early control flow, invalid transitions and predicated OOB memory fail as specified |
| Scalar MD5 portability | One regression test passes, including three actual native buffer digest comparisons |
| AArch64 scalar | 15 encodings, 45 executed cases, zero failing encodings |
| AArch64 SIMD regression | 512 encodings, 4,096 executed cases, zero failures |
| GCC ASan + UBSan | Original A32 and both mixed-state demos pass, no diagnostics |
| LAS host build and launcher | Build passes; host UI renders 1280x720; no guest game execution |
| macOS runtime | Not executed in this Linux environment |
| Real NSMB U Deluxe metadata or execution | Unavailable/unrun: no user game files |

The CommonCrypto Linux failure recorded in milestone 1 is resolved. No required check is
currently failing or skipped in these synthetic Linux suites. Sanitizers use existing GCC
runtimes because Clang's sanitizer runtimes are not installed. For either generated demo:

```sh
gcc -std=c11 -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=all \
    -fno-omit-frame-pointer -Iruntime runtime/arm32/core.c runtime/arm32/loader.c \
    tools/arm32/runner.c build/thumb/program.c -o build/thumb/runner-sanitize
build/thumb/runner-sanitize --guest-arch arm32 --game-profile generic \
    build/thumb/synthetic.nso 0x10000 0x20
```

The immediate-BLX fixture is generated by `test_thumb.py` as `build/test_thumb/immediate.nso`;
translate it with `--thumb` and use the same compiler/runner commands to repeat that
sanitizer variant. Final logs and exact commands are in `build/milestone2/` and the
recovery archive. No executable or translated output from commercial files belongs in Git.

## Next concrete requirement

Provide authorized local `main.npdm`, the `exefs` modules and the game version/update
information in a directory outside the repository, such as `/workspace/game-inputs/NSMBUD/`.
The next action is **read-only metadata inspection**, not execution: NPDM ISA/address-space
flags, module/build IDs, entry-state evidence, compression, dynamic tables and relocations.
Only that evidence can establish whether the next blocker is loading, additional Thumb
instructions, a specific SDK ABI, or something earlier. RomFS is unnecessary for this
initial metadata step. Graphics, audio and full game startup remain future work.
