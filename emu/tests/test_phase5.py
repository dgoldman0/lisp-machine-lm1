"""Phase 5 tests — messaging, atomics, multi-thread, multi-tile.

Build-plan milestone: producer on tile 0 sends fixnums, consumer on tile 1
sums them.
"""

from lm1.testing.harness import test
from lm1.execute import Emulator, Cluster
from lm1.decode import (
    Op, encode_r, encode_i, encode_s, encode_b, encode_x,
    FUNC_ADD, FUNC_SUB,
    FUNC_ADD_FIX,
    FUNC_CMP,
    BR_T, BR_NIL, BR_FIX_LT, BR_FIX_EQ,
)
from lm1.word import (
    NIL, T, tag_fixnum, untag_fixnum,
    is_fixnum, is_any_ref, ref_address, make_ref,
    make_header, HDR_INSTANCE,
    WORD_MASK,
)
from lm1.traps import (
    TRAP_QUEUE_FULL, TRAP_QUEUE_EMPTY,
    TRAP_NOT_FIXNUM,
)

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

NURSERY_BASE = 0x1_0000
NURSERY_SIZE = 0x1_0000
OLDGEN_BASE  = 0x2_0000
OLDGEN_SIZE  = 0x2_0000
STACK_TOP    = 0x1_0000
CODE_BASE    = 0x0


def _emu(**kw):
    defaults = dict(
        mem_size=512 * 1024,
        nursery_base=NURSERY_BASE,
        nursery_size=NURSERY_SIZE,
        oldgen_base=OLDGEN_BASE,
        oldgen_size=OLDGEN_SIZE,
    )
    defaults.update(kw)
    return Emulator(**defaults)


def _load_and_run(emu, program, *, base=CODE_BASE, max_instructions=10_000):
    for i, insn in enumerate(program):
        emu.mem.store_u32(base + i * 4, insn)
    emu.thread.pc = base
    emu.thread.sp = STACK_TOP
    emu.run(max_instructions=max_instructions)


# ===================================================================
# Batch: phase5_queue — SEND / RECV / TRY.RECV
# ===================================================================

@test("send_recv_local", batch="phase5_queue")
def test_send_recv_local():
    """SEND to a local queue, RECV retrieves it."""
    emu = _emu()
    t = emu.thread

    # Queue descriptor: (tile_id=0 << 2) | queue_idx=1 = fixnum(1)
    queue_desc = tag_fixnum(1)  # local queue 1

    program = [
        # r1 = queue descriptor (fixnum 1 → queue 1)
        encode_i(Op.LI, 1, 0, queue_desc & 0xFFFF),   # 0x00
        # r2 = value to send (fixnum 42)
        encode_i(Op.LI, 2, 0, tag_fixnum(42) & 0xFFFF),  # 0x04
        # SEND r1(queue), r2(value)
        encode_s(Op.SEND, 1, 2, 0),                     # 0x08
        # RECV r3 from r1(queue)
        encode_i(Op.RECV, 3, 1, 0),                     # 0x0C
        encode_x(Op.HALT_NOP, 0),                       # 0x10
    ]
    _load_and_run(emu, program)

    assert t.regs[3] == tag_fixnum(42), \
        f"RECV should get fixnum(42), got {t.regs[3]:#x}"


@test("send_recv_multiple", batch="phase5_queue")
def test_send_recv_multiple():
    """SEND multiple values, RECV them in FIFO order."""
    emu = _emu()
    t = emu.thread

    queue_desc = tag_fixnum(0)  # queue 0

    program = [
        encode_i(Op.LI, 1, 0, queue_desc & 0xFFFF),       # r1 = queue desc
        # Send 3 values
        encode_i(Op.LI, 2, 0, tag_fixnum(10) & 0xFFFF),
        encode_s(Op.SEND, 1, 2, 0),
        encode_i(Op.LI, 2, 0, tag_fixnum(20) & 0xFFFF),
        encode_s(Op.SEND, 1, 2, 0),
        encode_i(Op.LI, 2, 0, tag_fixnum(30) & 0xFFFF),
        encode_s(Op.SEND, 1, 2, 0),
        # Recv 3 values
        encode_i(Op.RECV, 3, 1, 0),
        encode_i(Op.RECV, 4, 1, 0),
        encode_i(Op.RECV, 5, 1, 0),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)

    assert t.regs[3] == tag_fixnum(10), f"First RECV should be 10"
    assert t.regs[4] == tag_fixnum(20), f"Second RECV should be 20"
    assert t.regs[5] == tag_fixnum(30), f"Third RECV should be 30"


@test("try_recv_empty", batch="phase5_queue")
def test_try_recv_empty():
    """TRY.RECV on empty queue returns nil, doesn't block."""
    emu = _emu()
    t = emu.thread

    queue_desc = tag_fixnum(2)  # queue 2

    # TRY.RECV: RECV opcode with func != 0.  func = Rd2 register index.
    # Format R: opcode=RECV, rd=3 (value), rs1=1 (queue), rs2=unused, func=4 (Rd2=r4)
    program = [
        encode_i(Op.LI, 1, 0, queue_desc & 0xFFFF),       # r1 = queue desc
        # TRY.RECV r3, r4, r1 — encoded as RECV with func=4
        encode_r(Op.RECV, 3, 1, 0, 4),                     # func=4 → Rd2=r4
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)

    assert t.regs[3] == NIL, f"TRY.RECV on empty: value should be nil, got {t.regs[3]:#x}"
    assert t.regs[4] == NIL, f"TRY.RECV on empty: status should be nil, got {t.regs[4]:#x}"


@test("try_recv_with_data", batch="phase5_queue")
def test_try_recv_with_data():
    """TRY.RECV on non-empty queue returns value and T status."""
    emu = _emu()
    t = emu.thread

    queue_desc = tag_fixnum(3)  # queue 3

    # Pre-populate queue
    emu.queues[3].append(tag_fixnum(99))

    program = [
        encode_i(Op.LI, 1, 0, queue_desc & 0xFFFF),
        # TRY.RECV r3(value), r4(status) from r1(queue)
        encode_r(Op.RECV, 3, 1, 0, 4),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)

    assert t.regs[3] == tag_fixnum(99), f"TRY.RECV: value should be 99"
    assert t.regs[4] == T, f"TRY.RECV: status should be T"


@test("send_queue_full_traps", batch="phase5_queue")
def test_send_queue_full_traps():
    """SEND to a full queue raises TRAP_QUEUE_FULL."""
    emu = _emu()
    t = emu.thread

    # Fill queue 0 to capacity
    for i in range(Emulator.QUEUE_DEPTH):
        emu.queues[0].append(tag_fixnum(i))

    program = [
        encode_i(Op.LI, 1, 0, tag_fixnum(0) & 0xFFFF),  # queue 0
        encode_i(Op.LI, 2, 0, tag_fixnum(1) & 0xFFFF),   # value
        encode_s(Op.SEND, 1, 2, 0),
        encode_x(Op.HALT_NOP, 0),
    ]
    caught = False
    try:
        _load_and_run(emu, program)
    except Exception as e:
        caught = True
        assert "full" in str(e).lower() or TRAP_QUEUE_FULL == getattr(e, 'code', None)

    assert caught, "SEND to full queue should trap"


# ===================================================================
# Batch: phase5_atomic — CAS.TAGGED, FAA, FENCE.GC
# ===================================================================

@test("cas_tagged_success", batch="phase5_atomic")
def test_cas_tagged_success():
    """CAS.TAGGED succeeds when memory matches expected value."""
    emu = _emu()
    t = emu.thread

    # Store fixnum(10) at address 0x400
    target_addr = 0x400
    emu.mem.store_word(target_addr, tag_fixnum(10))

    # CAS.TAGGED: Rd=5 (result), Rs_addr=1, Rs_expected=2, Rt_new=3 (func field)
    program = [
        encode_i(Op.LI, 1, 0, target_addr),                     # r1 = addr
        encode_i(Op.LI, 2, 0, tag_fixnum(10) & 0xFFFF),         # r2 = expected
        encode_i(Op.LI, 3, 0, tag_fixnum(20) & 0xFFFF),         # r3 = new value
        # CAS.TAGGED: rd=5, rs1=1(addr), rs2=2(expected), func=3(new reg)
        encode_r(Op.CAS_TAGGED, 5, 1, 2, 3),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)

    assert t.regs[5] == T, f"CAS should succeed: got {t.regs[5]:#x}"
    stored = emu.mem.load_word(target_addr)
    assert stored == tag_fixnum(20), f"Memory should be updated to 20, got {untag_fixnum(stored)}"


@test("cas_tagged_failure", batch="phase5_atomic")
def test_cas_tagged_failure():
    """CAS.TAGGED fails when memory doesn't match expected value."""
    emu = _emu()
    t = emu.thread

    target_addr = 0x400
    emu.mem.store_word(target_addr, tag_fixnum(10))

    program = [
        encode_i(Op.LI, 1, 0, target_addr),
        encode_i(Op.LI, 2, 0, tag_fixnum(99) & 0xFFFF),         # wrong expected
        encode_i(Op.LI, 3, 0, tag_fixnum(20) & 0xFFFF),
        encode_r(Op.CAS_TAGGED, 5, 1, 2, 3),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)

    assert t.regs[5] == NIL, f"CAS should fail: got {t.regs[5]:#x}"
    stored = emu.mem.load_word(target_addr)
    assert stored == tag_fixnum(10), f"Memory should be unchanged"


@test("faa_basic", batch="phase5_atomic")
def test_faa_basic():
    """FAA atomically adds to a fixnum in memory, returns old value."""
    emu = _emu()
    t = emu.thread

    target_addr = 0x400
    emu.mem.store_word(target_addr, tag_fixnum(100))

    # FAA: Rd=5(old), Rs1=1(addr), Rs2=2(delta), func != 31
    program = [
        encode_i(Op.LI, 1, 0, target_addr),
        encode_i(Op.LI, 2, 0, tag_fixnum(7) & 0xFFFF),
        encode_r(Op.FAA_FENCE, 5, 1, 2, 0),             # func=0 → FAA
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)

    assert t.regs[5] == tag_fixnum(100), \
        f"FAA should return old value fixnum(100), got {t.regs[5]:#x}"
    stored = emu.mem.load_word(target_addr)
    assert stored == tag_fixnum(107), \
        f"Memory should be fixnum(107) after FAA, got {untag_fixnum(stored)}"


@test("faa_not_fixnum_traps", batch="phase5_atomic")
def test_faa_not_fixnum_traps():
    """FAA traps when memory contents are not a fixnum."""
    emu = _emu()
    t = emu.thread

    target_addr = 0x400
    emu.mem.store_word(target_addr, NIL)  # Not a fixnum

    program = [
        encode_i(Op.LI, 1, 0, target_addr),
        encode_i(Op.LI, 2, 0, tag_fixnum(1) & 0xFFFF),
        encode_r(Op.FAA_FENCE, 5, 1, 2, 0),
        encode_x(Op.HALT_NOP, 0),
    ]
    caught = False
    try:
        _load_and_run(emu, program)
    except Exception as e:
        caught = True
        assert TRAP_NOT_FIXNUM == getattr(e, 'code', None)

    assert caught, "FAA on non-fixnum should trap"


@test("fence_gc_nop", batch="phase5_atomic")
def test_fence_gc_nop():
    """FENCE.GC executes as a no-op without crashing."""
    emu = _emu()
    t = emu.thread

    # FENCE.GC: opcode=FAA_FENCE, func=0x1F (31)
    program = [
        encode_i(Op.LI, 5, 0, 0xAA),
        encode_r(Op.FAA_FENCE, 0, 0, 0, 0x1F),         # FENCE.GC
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)

    assert t.regs[5] == 0xAA, f"r5 should be 0xAA after FENCE.GC"


# ===================================================================
# Batch: phase5_multithread — multi-thread round-robin
# ===================================================================

@test("two_threads_round_robin", batch="phase5_multithread")
def test_two_threads_round_robin():
    """Two threads on the same tile run in round-robin."""
    emu = _emu(num_threads=2)

    # Thread 0: set r5 = 0xAA, halt
    prog0 = [
        encode_i(Op.LI, 5, 0, 0xAA),
        encode_x(Op.HALT_NOP, 0),
    ]
    # Thread 1: set r6 = 0xBB, halt (at different code address)
    prog1_base = 0x100
    prog1 = [
        encode_i(Op.LI, 6, 0, 0xBB),
        encode_x(Op.HALT_NOP, 0),
    ]

    # Load programs
    for i, insn in enumerate(prog0):
        emu.mem.store_u32(i * 4, insn)
    for i, insn in enumerate(prog1):
        emu.mem.store_u32(prog1_base + i * 4, insn)

    # Set up threads
    emu.threads[0].pc = 0
    emu.threads[0].sp = STACK_TOP
    emu.threads[1].pc = prog1_base
    emu.threads[1].sp = STACK_TOP - 0x1000  # separate stack

    emu.run_round_robin(max_instructions=100)

    assert emu.threads[0].regs[5] == 0xAA, \
        f"Thread 0 should set r5=0xAA, got {emu.threads[0].regs[5]:#x}"
    assert emu.threads[1].regs[6] == 0xBB, \
        f"Thread 1 should set r6=0xBB, got {emu.threads[1].regs[6]:#x}"
    assert emu.threads[0].halted
    assert emu.threads[1].halted


@test("recv_stall_unstall", batch="phase5_multithread")
def test_recv_stall_unstall():
    """Thread stalls on RECV, producer unstalls it via SEND."""
    emu = _emu(num_threads=2)

    queue_desc_val = tag_fixnum(0)  # queue 0

    # Thread 0 (producer): send fixnum(77) to queue 0, then halt
    prog0 = [
        encode_i(Op.LI, 1, 0, queue_desc_val & 0xFFFF),
        encode_i(Op.LI, 2, 0, tag_fixnum(77) & 0xFFFF),
        encode_s(Op.SEND, 1, 2, 0),
        encode_x(Op.HALT_NOP, 0),
    ]

    # Thread 1 (consumer): recv from queue 0 into r5, then halt
    prog1_base = 0x100
    prog1 = [
        encode_i(Op.LI, 1, 0, queue_desc_val & 0xFFFF),
        encode_i(Op.RECV, 5, 1, 0),  # blocks if queue empty
        encode_x(Op.HALT_NOP, 0),
    ]

    for i, insn in enumerate(prog0):
        emu.mem.store_u32(i * 4, insn)
    for i, insn in enumerate(prog1):
        emu.mem.store_u32(prog1_base + i * 4, insn)

    emu.threads[0].pc = 0
    emu.threads[0].sp = STACK_TOP
    emu.threads[1].pc = prog1_base
    emu.threads[1].sp = STACK_TOP - 0x1000

    emu.run_round_robin(max_instructions=1000)

    assert emu.threads[1].regs[5] == tag_fixnum(77), \
        f"Consumer should receive fixnum(77), got {emu.threads[1].regs[5]:#x}"
    assert emu.threads[0].halted
    assert emu.threads[1].halted


# ===================================================================
# Batch: phase5_multitile — cluster with cross-tile messaging
# ===================================================================

@test("cross_tile_send_recv", batch="phase5_multitile")
def test_cross_tile_send_recv():
    """Tile 0 sends a value, tile 1 receives it via cluster routing."""
    cluster = Cluster(
        n_tiles=2,
        mem_size=512 * 1024,
        nursery_base=NURSERY_BASE,
        nursery_size=NURSERY_SIZE,
        oldgen_base=OLDGEN_BASE,
        oldgen_size=OLDGEN_SIZE,
    )

    tile0 = cluster.tiles[0]
    tile1 = cluster.tiles[1]

    # Queue descriptor for tile 1, queue 0: (1 << 2) | 0 = 4
    remote_q = tag_fixnum(4)
    # Queue descriptor for tile 1 local: (1 << 2) | 0 for its own perspective
    # But tile 1 RECVs from its own queue 0 → descriptor = (1 << 2) | 0 = 4
    local_q = tag_fixnum(4)

    # Tile 0 program: SEND fixnum(55) to tile1/queue0, then HALT
    prog0 = [
        encode_i(Op.LI, 1, 0, remote_q & 0xFFFF),
        encode_i(Op.LI, 2, 0, tag_fixnum(55) & 0xFFFF),
        encode_s(Op.SEND, 1, 2, 0),
        encode_x(Op.HALT_NOP, 0),
    ]
    for i, insn in enumerate(prog0):
        tile0.mem.store_u32(i * 4, insn)
    tile0.thread.pc = 0
    tile0.thread.sp = STACK_TOP

    # Tile 1 program: RECV from its own queue 0, then HALT
    prog1 = [
        encode_i(Op.LI, 1, 0, local_q & 0xFFFF),
        encode_i(Op.RECV, 5, 1, 0),
        encode_x(Op.HALT_NOP, 0),
    ]
    for i, insn in enumerate(prog1):
        tile1.mem.store_u32(i * 4, insn)
    tile1.thread.pc = 0
    tile1.thread.sp = STACK_TOP

    cluster.run(max_instructions=1000)

    assert tile1.thread.regs[5] == tag_fixnum(55), \
        f"Tile 1 should receive fixnum(55), got {tile1.thread.regs[5]:#x}"


@test("producer_consumer_sum", batch="phase5_multitile")
def test_producer_consumer_sum():
    """BUILD-PLAN MILESTONE: producer on tile 0 sends fixnums 1..10,
    consumer on tile 1 sums them → fixnum(55).

    Producer:
        for i = 1 to 10: SEND fixnum(i)
        SEND fixnum(0)  # sentinel
        HALT

    Consumer:
        sum = 0
        loop: RECV val
              if val == 0: break
              sum += val
              goto loop
        r5 = sum
        HALT
    """
    cluster = Cluster(
        n_tiles=2,
        mem_size=512 * 1024,
        nursery_base=NURSERY_BASE,
        nursery_size=NURSERY_SIZE,
        oldgen_base=OLDGEN_BASE,
        oldgen_size=OLDGEN_SIZE,
    )

    tile0 = cluster.tiles[0]
    tile1 = cluster.tiles[1]

    # Queue descriptor: tile 1, queue 0 → (1<<2)|0 = 4
    remote_q = tag_fixnum(4)
    local_q = tag_fixnum(4)  # tile 1's own perspective

    # ---- Producer (tile 0) ----
    # r1 = queue desc, r2 = counter (starts at fixnum(1)),
    # r3 = limit (fixnum(11)), r4 = step (fixnum(1))
    # Loop: SEND r2, r2 += 1, CMP r2 vs r3, if <0 goto loop
    # Then SEND fixnum(0) as sentinel, HALT
    prod = [
        encode_i(Op.LI, 1, 0, remote_q & 0xFFFF),       # 0x00: r1 = queue
        encode_i(Op.LI, 2, 0, tag_fixnum(1) & 0xFFFF),   # 0x04: r2 = fixnum(1)
        encode_i(Op.LI, 3, 0, tag_fixnum(11) & 0xFFFF),  # 0x08: r3 = fixnum(11)
        encode_i(Op.LI, 4, 0, tag_fixnum(1) & 0xFFFF),   # 0x0C: r4 = fixnum(1)
        # loop at 0x10
        encode_s(Op.SEND, 1, 2, 0),                       # 0x10: SEND r1, r2
        # r2 += r4 (ADD.FIX)
        encode_r(Op.ARITH_FIX, 2, 2, 4, FUNC_ADD_FIX),   # 0x14: r2 = r2 + r4
        # CMP.TAGGED r7, r2, r3 → r7 < 0 if r2 < r3
        encode_r(Op.CMP_TAGGED, 7, 2, 3, FUNC_CMP),      # 0x18: r7 = cmp(r2,r3)
        # BR_COND r7, FIX_LT, -4 (back to loop at 0x10)
        encode_b(Op.BR_COND, 7, BR_FIX_LT, -4),          # 0x1C: if r7<0 → 0x10
        # Send sentinel (fixnum 0)
        encode_i(Op.LI, 2, 0, tag_fixnum(0) & 0xFFFF),   # 0x20: r2 = fixnum(0)
        encode_s(Op.SEND, 1, 2, 0),                       # 0x24: SEND sentinel
        encode_x(Op.HALT_NOP, 0),                          # 0x28: HALT
    ]
    for i, insn in enumerate(prod):
        tile0.mem.store_u32(i * 4, insn)
    tile0.thread.pc = 0
    tile0.thread.sp = STACK_TOP

    # ---- Consumer (tile 1) ----
    # r1 = queue desc, r5 = sum (starts at fixnum(0)), r6 = fixnum(0) (sentinel)
    # Loop: RECV r2, CMP r2 vs r6, if ==0 done, sum += r2, goto loop
    # After loop, r5 = sum. HALT.
    cons = [
        encode_i(Op.LI, 1, 0, local_q & 0xFFFF),         # 0x00: r1 = queue
        encode_i(Op.LI, 5, 0, tag_fixnum(0) & 0xFFFF),   # 0x04: r5 = sum = 0
        encode_i(Op.LI, 6, 0, tag_fixnum(0) & 0xFFFF),   # 0x08: r6 = fixnum(0)
        # loop at 0x0C
        encode_i(Op.RECV, 2, 1, 0),                       # 0x0C: RECV r2, r1
        # CMP.TAGGED r7, r2, r6 → r7==0 if r2==fixnum(0)
        encode_r(Op.CMP_TAGGED, 7, 2, 6, FUNC_CMP),      # 0x10: r7 = cmp(r2,r6)
        # if r7 == 0 → done (+3 words)
        encode_b(Op.BR_COND, 7, BR_FIX_EQ, 3),           # 0x14: if r7==0 → 0x20
        # sum += val
        encode_r(Op.ARITH_FIX, 5, 5, 2, FUNC_ADD_FIX),   # 0x18: r5 = r5 + r2
        encode_b(Op.BR, 0, 0, -4),                        # 0x1C: goto 0x0C
        # done
        encode_x(Op.HALT_NOP, 0),                          # 0x20: HALT
    ]
    for i, insn in enumerate(cons):
        tile1.mem.store_u32(i * 4, insn)
    tile1.thread.pc = 0
    tile1.thread.sp = STACK_TOP

    cluster.run(max_instructions=10_000)

    result = tile1.thread.regs[5]
    # 1+2+...+10 = 55
    assert result == tag_fixnum(55), \
        f"Consumer sum should be fixnum(55), got {untag_fixnum(result)}"
