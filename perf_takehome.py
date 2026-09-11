"""
# Anthropic's Original Performance Engineering Take-home (Release version)

Copyright Anthropic PBC 2026. Permission is granted to modify and use, but not
to publish or redistribute your solutions so it's hard to find spoilers.

# Task

- Optimize the kernel (in KernelBuilder.build_kernel) as much as possible in the
  available time, as measured by test_kernel_cycles on a frozen separate copy
  of the simulator.

Validate your results using `python tests/submission_tests.py` without modifying
anything in the tests/ folder.

We recommend you look through problem.py next.
"""

from collections import defaultdict
import random
import unittest

from problem import (
    Engine,
    DebugInfo,
    SLOT_LIMITS,
    VLEN,
    N_CORES,
    SCRATCH_SIZE,
    Machine,
    Tree,
    Input,
    HASH_STAGES,
    reference_kernel,
    build_mem_image,
    reference_kernel2,
)


LOOKUP_DEPTH = 3
SETUP_CHUNK = 32
HASH_ALU_CHUNKS = 0
BIT_MASK_VALU_CHUNKS = 1
DEPTH3_SHARED_SELECT = True
PAIR_LOOKUP_DEPTH = 3
DEPTH3_COEFF_SELECT = True
DEPTH4_BIT_SELECT = True
NODE_VIRTUAL_VECTORS = 5
DEPTH4_CACHE_CHUNKS = 25
DEPTH4_CACHE_ROUNDS = (4,)
PATH_REUSE_DEPTH = 4
DIRECT_PATH_DEPTH = 4


class KernelBuilder:
    def __init__(self):
        self.instrs = []
        self.scratch = {}
        self.scratch_debug = {}
        self.scratch_ptr = 0
        self.const_map = {}

    def debug_info(self):
        return DebugInfo(scratch_map=self.scratch_debug)

    def build(self, slots: list[tuple[Engine, tuple]], vliw: bool = False):
        # Simple slot packing that just uses one slot per instruction bundle
        instrs = []
        for engine, slot in slots:
            instrs.append({engine: [slot]})
        return instrs

    def add(self, engine, slot):
        self.instrs.append({engine: [slot]})

    def instruction_accesses(self, engine, slot):
        """Return scratch addresses read and written by an instruction slot."""
        if engine == "load":
            if slot[0] == "const":
                return set(), {slot[1]}
            if slot[0] == "load":
                return {slot[2]}, {slot[1]}
            if slot[0] == "vload":
                return {slot[2]}, set(range(slot[1], slot[1] + VLEN))
            if slot[0] == "load_offset":
                return {slot[2] + slot[3]}, {slot[1] + slot[3]}
        if engine == "store":
            if slot[0] == "vstore":
                return {slot[1]} | set(range(slot[2], slot[2] + VLEN)), set()
            if slot[0] == "store":
                return {slot[1], slot[2]}, set()
        if engine == "alu":
            return {slot[2], slot[3]}, {slot[1]}
        if engine == "valu":
            if slot[0] == "vbroadcast":
                return {slot[2]}, set(range(slot[1], slot[1] + VLEN))
            if slot[0] == "multiply_add":
                reads = set()
                for addr in slot[2:5]:
                    reads.update(range(addr, addr + VLEN))
                return reads, set(range(slot[1], slot[1] + VLEN))
            reads = set(range(slot[2], slot[2] + VLEN))
            reads.update(range(slot[3], slot[3] + VLEN))
            return reads, set(range(slot[1], slot[1] + VLEN))
        if engine == "flow" and slot[0] == "add_imm":
            return {slot[2]}, {slot[1]}
        if engine == "flow" and slot[0] == "vselect":
            reads = set()
            for addr in slot[2:5]:
                reads.update(range(addr, addr + VLEN))
            return reads, set(range(slot[1], slot[1] + VLEN))
        raise ValueError((engine, slot))

    def pack_setup(self, extract=False, setup_chunk=-1):
        """Infer setup hazards, then either schedule or return its DAG."""
        flat = [
            {"engine": engine, "slot": slot}
            for instr in self.instrs
            for engine, slots in instr.items()
            for slot in slots
        ]

        last_writer = {}
        readers = defaultdict(set)
        successors = [[] for _ in flat]
        remaining = []
        for op_id, op in enumerate(flat):
            reads, writes = self.instruction_accesses(op["engine"], op["slot"])
            deps = {last_writer[addr] for addr in reads if addr in last_writer}
            for addr in writes:
                if addr in last_writer:
                    deps.add(last_writer[addr])
                deps.update(readers[addr])
            remaining.append(len(deps))
            op.update(
                deps=list(deps),
                chunk=setup_chunk,
                round=-1,
                local_seq=op_id,
            )
            for dep in deps:
                successors[dep].append(op_id)
            for addr in reads:
                readers[addr].add(op_id)
            for addr in writes:
                last_writer[addr] = op_id
                readers[addr].clear()

        if extract:
            self.instrs = []
            return flat, last_writer

        ready = defaultdict(list)
        bottom_level = [1] * len(flat)
        for op_id in range(len(flat) - 1, -1, -1):
            if successors[op_id]:
                bottom_level[op_id] = 1 + max(
                    bottom_level[succ] for succ in successors[op_id]
                )
        for op_id, count in enumerate(remaining):
            if count == 0:
                ready[flat[op_id]["engine"]].append(op_id)
        bundles = []
        scheduled = 0
        while scheduled < len(flat):
            chosen = []
            bundle = {}
            for engine in ("load", "valu", "alu"):
                ready[engine].sort(
                    key=lambda op_id: (bottom_level[op_id], -op_id), reverse=True
                )
                selected = ready[engine][: SLOT_LIMITS[engine]]
                del ready[engine][: len(selected)]
                if selected:
                    bundle[engine] = [flat[op_id]["slot"] for op_id in selected]
                    chosen.extend(selected)
            assert chosen
            bundles.append(bundle)
            scheduled += len(chosen)
            for op_id in chosen:
                for succ in successors[op_id]:
                    remaining[succ] -= 1
                    if remaining[succ] == 0:
                        ready[flat[succ]["engine"]].append(succ)
        self.instrs = bundles

    def schedule(self, ops):
        """Try several list-scheduling priorities and retain the shortest."""
        successors = [[] for _ in ops]
        dep_counts = []
        for op_id, op in enumerate(ops):
            deps = list(dict.fromkeys(dep for dep in op["deps"] if dep is not None))
            op["deps"] = deps
            dep_counts.append(len(deps))
            for dep in deps:
                successors[dep].append(op_id)

        # Bottom level is a useful critical-path priority for unit-latency ops.
        pending = dep_counts.copy()
        topological = [i for i, count in enumerate(pending) if count == 0]
        for op_id in topological:
            for succ in successors[op_id]:
                pending[succ] -= 1
                if pending[succ] == 0:
                    topological.append(succ)
        assert len(topological) == len(ops), "Dependency cycle in scheduler"
        bottom_level = [1] * len(ops)
        for op_id in reversed(topological):
            if successors[op_id]:
                bottom_level[op_id] = 1 + max(
                    bottom_level[succ] for succ in successors[op_id]
                )

        def priority(policy, op_id):
            fanout = len(successors[op_id])
            unlocks_load = any(ops[succ]["engine"] == "load" for succ in successors[op_id])
            round_no = ops[op_id].get("round", -1)
            local_seq = ops[op_id].get("local_seq", -1)
            chunk_no = ops[op_id].get("chunk", -1)
            if policy == "critical_early":
                return (bottom_level[op_id], -op_id)
            if policy == "critical_late":
                return (bottom_level[op_id], op_id)
            if policy == "fanout":
                return (fanout, bottom_level[op_id], -op_id)
            if policy == "load_unlock":
                return (unlocks_load, fanout, bottom_level[op_id], -op_id)
            if policy == "fifo":
                return (-op_id,)
            if policy == "lifo":
                return (op_id,)
            if policy == "wavefront_early":
                return (-round_no, -local_seq, -chunk_no)
            if policy == "wavefront_late":
                return (-round_no, local_seq, -chunk_no)
            if policy == "wavefront_critical":
                return (-round_no, bottom_level[op_id], -local_seq, -chunk_no)
            if policy.startswith("cohort_"):
                penalty = int(policy.split("_")[1])
                return (chunk_no * 100 - round_no * penalty, local_seq)
            if policy.startswith(("tail_hetero_", "tail_multi_")):
                fields = policy.split("_")
                penalty = {
                    "load": int(fields[2]),
                    "valu": int(fields[3]),
                    "alu": int(fields[4]),
                    "flow": int(fields[5]),
                }.get(ops[op_id]["engine"], 245)
                return (chunk_no * 100 - round_no * penalty, local_seq)
            if policy.startswith("tail_"):
                penalty = int(policy.split("_")[2])
                return (chunk_no * 100 - round_no * penalty, local_seq)
            raise ValueError(policy)

        def make_schedule(policy):
            adaptive = policy.startswith("adaptive_")
            if adaptive:
                policy = policy[len("adaptive_"):]
            priorities = [priority(policy, op_id) for op_id in range(len(ops))]
            remaining = dep_counts.copy()
            ready = defaultdict(list)
            for op_id, count in enumerate(remaining):
                if count == 0:
                    ready[ops[op_id]["engine"]].append(op_id)

            scheduled_count = 0
            bundles = []
            engine_order = ("load", "valu", "alu", "store", "flow")
            while scheduled_count < len(ops):
                chosen = []
                bundle = {}
                for engine in engine_order:
                    candidates = ready[engine]
                    tail_engines = (
                        {"valu", "alu", "flow"}
                        if policy.startswith(
                            ("tail_compute_", "tail_hetero_", "tail_multi_")
                        )
                        else set(engine_order)
                    )
                    if policy.startswith("tail_multi_"):
                        fields = policy.split("_")
                        tail_start = {
                            "valu": int(fields[6]),
                            "alu": int(fields[7]),
                            "flow": int(fields[8]),
                        }.get(engine, len(ops) + 1)
                    elif policy.startswith("tail_"):
                        tail_start = int(policy.rsplit("_", 1)[1])
                    else:
                        tail_start = len(ops) + 1
                    if (
                        policy.startswith("tail_")
                        and engine in tail_engines
                        and len(bundles) >= tail_start
                    ):
                        candidates.sort(
                            key=lambda op_id: (
                                -ops[op_id]["chunk"],
                                ops[op_id]["round"],
                                bottom_level[op_id],
                            ),
                            reverse=True,
                        )
                    else:
                        candidates.sort(key=priorities.__getitem__, reverse=True)
                    selected = candidates[: SLOT_LIMITS[engine]]
                    del candidates[: len(selected)]
                    if selected:
                        bundle[engine] = [ops[op_id]["slot"] for op_id in selected]
                        chosen.extend(selected)

                # A binary vector op can issue as eight independent scalar
                # lanes when the complete group fits this same cycle.
                if adaptive and SLOT_LIMITS["alu"] - len(bundle.get("alu", ())) >= VLEN:
                    offload = next((i for i in ready["valu"]
                                    if ops[i]["slot"][0] not in ("vbroadcast", "multiply_add")), None)
                    if offload is not None:
                        ready["valu"].remove(offload)
                        # Keep the logical vector intact until register allocation.
                        bundle["alu_vector"] = [ops[offload]["slot"]]
                        chosen.append(offload)

                assert chosen, "Dependency cycle in scheduler"
                bundles.append(bundle)
                scheduled_count += len(chosen)
                for op_id in chosen:
                    for succ in successors[op_id]:
                        remaining[succ] -= 1
                        if remaining[succ] == 0:
                            ready[ops[succ]["engine"]].append(succ)
            return bundles

        policies = (
            "critical_early",
            "critical_late",
            "fanout",
            "load_unlock",
            "fifo",
            "lifo",
            "wavefront_early",
            "wavefront_late",
            "wavefront_critical",
            "cohort_5",
            "cohort_10",
            "cohort_20",
            "cohort_40",
            "cohort_80",
            "cohort_160",
            "cohort_320",
            "cohort_480",
            "cohort_640",
            "cohort_960",
            "cohort_1280",
        ) + tuple(f"cohort_{penalty}" for penalty in range(200, 461, 10)) + tuple(
            f"cohort_{penalty}" for penalty in range(221, 281)
        ) + (
            "tail_laggard_240_1038",
            "tail_compute_245_960",
            "tail_hetero_360_240_240_220_900",
            "tail_multi_290_190_195_260_975_780_800",
        )
        policies += tuple("adaptive_" + policy for policy in policies)
        policies += (
            "adaptive_tail_hetero_360_220_140_140_900",
            "adaptive_tail_hetero_360_220_220_140_750",
            "adaptive_tail_hetero_480_300_140_220_750",
        )
        self.schedule_stats = {}
        best = None
        for policy in dict.fromkeys(policies):
            bundles = make_schedule(policy)
            self.schedule_stats[policy] = len(bundles)
            if best is None or len(bundles) < len(best):
                best = bundles
                self.schedule_policy = policy
        identities = {id(op["slot"]): i for i, op in enumerate(ops)}
        self.issue_cycles = {identities[id(slot)]: cycle for cycle, bundle in enumerate(best)
                             for slots in bundle.values() for slot in slots}
        self.offloaded_ops = {identities[id(slot)] for bundle in best
                              for slot in bundle.get("alu_vector", ())}
        self.instrs.extend(best)

    def alloc_scratch(self, name=None, length=1):
        addr = self.scratch_ptr
        if name is not None:
            self.scratch[name] = addr
            self.scratch_debug[addr] = (name, length)
        self.scratch_ptr += length
        assert self.scratch_ptr <= SCRATCH_SIZE, "Out of scratch space"
        return addr

    def scratch_const(self, val, name=None):
        if val not in self.const_map:
            addr = self.alloc_scratch(name)
            self.add("load", ("const", addr, val))
            self.const_map[val] = addr
        return self.const_map[val]

    def build_hash(self, val_hash_addr, tmp1, tmp2, round, i):
        slots = []

        for hi, (op1, val1, op2, op3, val3) in enumerate(HASH_STAGES):
            slots.append(("alu", (op1, tmp1, val_hash_addr, self.scratch_const(val1))))
            slots.append(("alu", (op3, tmp2, val_hash_addr, self.scratch_const(val3))))
            slots.append(("alu", (op2, val_hash_addr, tmp1, tmp2)))
            slots.append(("debug", ("compare", val_hash_addr, (round, i, "hash_stage", hi))))

        return slots

    def build_kernel(
        self, forest_height: int, n_nodes: int, batch_size: int, rounds: int
    ):
        """
        Build a SIMD kernel and list-schedule all vector chunks together.
        """
        assert batch_size % VLEN == 0
        assert PATH_REUSE_DEPTH in (0, 2, 3, 4)
        assert DIRECT_PATH_DEPTH in (0, 2, 3, 4) and DIRECT_PATH_DEPTH <= PATH_REUSE_DEPTH
        # Legacy lookup experiments below used positive addresses. Reject
        # those switches until their formulas are ported to S=5-A as well.
        assert (LOOKUP_DEPTH == PAIR_LOOKUP_DEPTH == 3
                and DEPTH3_COEFF_SELECT and DEPTH4_BIT_SELECT), (
            "Negative indices require the pair-coefficient low-bit lookup path"
        )
        chunk_count = batch_size // VLEN
        # Cache coverage is tuned for the scored shape; retain the generic path
        # elsewhere, where different overlap can require more live scratch.
        cache_depth4 = (forest_height, batch_size, rounds) == (10, 256, 16) and DEPTH4_CACHE_CHUNKS > 0 and any(
            r < rounds and r % (forest_height + 1) == 4 for r in DEPTH4_CACHE_ROUNDS
        )
        forest_values_p = 7
        inp_values_p = forest_values_p + n_nodes + batch_size

        def vector_const(value, name):
            if value in self.const_map:
                scalar = self.const_map[value]
                vector = self.alloc_scratch(name, VLEN)
            else:
                # Lane 0 holds the scalar before the broadcast, then remains a
                # valid scalar alias of the vector constant. This saves one
                # scratch word for every new vector constant.
                vector = self.alloc_scratch(name, VLEN)
                scalar = vector
                self.add("load", ("const", scalar, value))
                self.const_map[value] = scalar
            self.add("valu", ("vbroadcast", vector, scalar))
            return vector

        # For depths >= 2, idx holds S=5-A (mod 2**32), not an address.
        # A'=2*A-5-p becomes S'=2*S+p: one multiply_add.
        depth2_left_base = vector_const((-6) & 0xFFFFFFFF, "depth2_left_base")
        depth2_right_base = vector_const((-8) & 0xFFFFFFFF, "depth2_right_base")
        one = vector_const(1, "one")
        two = vector_const(2, "two")
        forest_values_scalar = self.scratch_const(forest_values_p, "forest_values_scalar")
        address_five = self.scratch_const(5)
        final_xor_vec = vector_const(HASH_STAGES[-1][1], "final_xor")
        final_xor_const = self.const_map[HASH_STAGES[-1][1]]
        top_nodes = self.alloc_scratch("top_nodes", VLEN)
        self.add("load", ("vload", top_nodes, self.const_map[forest_values_p]))
        root_value = top_nodes
        root_value_copy = self.alloc_scratch("root_value_copy")
        zero = self.alloc_scratch("zero")  # Scratch starts zeroed.
        self.add("alu", ("+", root_value_copy, root_value, zero))
        for lane in range(7):
            self.add("alu", ("^", top_nodes + lane, top_nodes + lane, final_xor_const))
        root_value_encoded = self.alloc_scratch("root_value_encoded")
        self.add("alu", ("+", root_value_encoded, root_value, zero))

        depth1_left = top_nodes + 1
        depth1_right = top_nodes + 2
        depth1_right_vec = self.alloc_scratch("depth1_right_vec", VLEN)
        depth1_left_vec = self.alloc_scratch("depth1_left_vec", VLEN)
        self.add("valu", ("vbroadcast", depth1_right_vec, depth1_right))
        self.add("valu", ("vbroadcast", depth1_left_vec, depth1_left))

        def prepare_pairs(first, count, address):
            # Direct lookup uses F1+p*(F0-F1), with p the latest path bit.
            # Only the legacy path needs the address-dependent intercept below.
            # For a pair starting at A0, D=F0-F1 and E=F0+(A0-5)*D.
            # Thus F[A] = (5-A)*D+E, including wraparound arithmetic.
            # The setup-only zero word is dead after copying the raw root.
            for offset in range(0, count, 2):
                intercept, slope = first + offset, first + offset + 1
                if address < forest_values_p + (1 << (DIRECT_PATH_DEPTH + 1)) - 1:
                    # Keep D,F1 in place; permute the broadcast references.
                    self.add("alu", ("-", intercept, intercept, slope))
                    continue
                base = self.scratch_const(address + offset)
                self.add("alu", ("-", slope, intercept, slope))
                self.add("alu", ("-", zero, base, address_five))
                self.add("alu", ("*", zero, zero, slope))
                self.add("alu", ("+", intercept, intercept, zero))

        if PAIR_LOOKUP_DEPTH >= 2:
            prepare_pairs(top_nodes + 3, 4, forest_values_p + 3)
        depth2_values = [top_nodes + tree_idx for tree_idx in range(3, 7)]
        depth2_vectors = []
        for name, scalar in enumerate(depth2_values):
            vector = self.alloc_scratch(f"depth2_vec_{name}", VLEN)
            self.add("valu", ("vbroadcast", vector, scalar))
            depth2_vectors.append(vector)

        if LOOKUP_DEPTH >= 3:
            depth3_addr = self.scratch_const(forest_values_p + 7, "depth3_addr")
            # The setup hazard tracker delays this overwrite until all users of
            # the first tree prefix have read it.
            self.add("load", ("vload", top_nodes, depth3_addr))
            for lane in range(VLEN):
                self.add("alu", ("^", top_nodes + lane, top_nodes + lane, final_xor_const))
            if PAIR_LOOKUP_DEPTH >= 3:
                prepare_pairs(top_nodes, 8, forest_values_p + 7)
            depth3_vectors = []
            for i in range(4):
                vector = self.alloc_scratch(f"depth3_lower_{i}", VLEN)
                self.add("valu", ("vbroadcast", vector, top_nodes + i))
                depth3_vectors.append(vector)
            a, b, c, d = [top_nodes + i for i in range(4, 8)]
            if DEPTH3_SHARED_SELECT or PAIR_LOOKUP_DEPTH >= 3:
                upper_scalars = tuple(enumerate((a, b, c, d)))
            else:
                for dest in (a, b, c):
                    self.add("alu", ("-", dest, dest, d))
                self.add("alu", ("-", a, a, b))
                self.add("alu", ("-", a, a, c))
                upper_scalars = (("base", d), ("delta4", b), ("delta5", c), ("cross", a))
            for name, scalar in upper_scalars:
                vector = self.alloc_scratch(f"depth3_upper_{name}", VLEN)
                self.add("valu", ("vbroadcast", vector, scalar))
                depth3_vectors.append(vector)
            depth3_shared = [top_nodes]
            depth3_thresholds = {n: self.scratch_const(n) for n in (16, 18, 20)}
            depth3_bit4 = self.scratch_const(4)

        if cache_depth4:
            depth4_bit8 = self.scratch_const(8)
            depth4_vectors = []
            depth4_thresholds = {n: self.scratch_const(n) for n in range(22, 38, 2)}
            for address in (22, 30):
                self.add("load", ("vload", top_nodes, depth4_thresholds[address]))
                for lane in range(VLEN):
                    self.add("alu", ("^", top_nodes + lane, top_nodes + lane, final_xor_const))
                prepare_pairs(top_nodes, VLEN, address)
                for lane in range(VLEN):
                    vector = self.alloc_scratch(f"depth4_coef_{address}_{lane}", VLEN)
                    self.add("valu", ("vbroadcast", vector, top_nodes + lane))
                    depth4_vectors.append(vector)

        if DIRECT_PATH_DEPTH >= 2:
            depth2_vectors = [depth2_vectors[i ^ 1] for i in range(4)]
        if DIRECT_PATH_DEPTH >= 3:
            depth3_vectors = [depth3_vectors[i ^ 1] for i in range(8)]
        if DIRECT_PATH_DEPTH >= 4 and cache_depth4:
            depth4_vectors = [depth4_vectors[i ^ 1] for i in range(16)]

        # Stages 2/3 become two independent affine arms followed by XOR.
        # All coefficients are reduced modulo the machine's 32-bit word size.
        hash23_multiplier = 1 + (1 << HASH_STAGES[2][4])
        hash23_shift = HASH_STAGES[3][4]
        hash23_left_bias = (HASH_STAGES[2][1] + HASH_STAGES[3][1]) & 0xFFFFFFFF
        hash23_right_multiplier = hash23_multiplier << hash23_shift
        hash23_right_bias = (HASH_STAGES[2][1] << hash23_shift) & 0xFFFFFFFF
        hash_constants = {}
        for stage, (op1, val1, op2, op3, val3) in enumerate(HASH_STAGES):
            if stage in (2, 3):
                continue
            if val3 != 16 and val1 not in hash_constants:
                hash_constants[val1] = vector_const(val1, f"const_{val1:x}")
            fused = (op1, op2, op3) == ("+", "+", "<<")
            if not fused and val3 not in hash_constants:
                hash_constants[val3] = vector_const(val3, f"const_{val3:x}")
        for constant in (hash23_left_bias, hash23_right_multiplier, hash23_right_bias):
            if constant not in hash_constants:
                hash_constants[constant] = vector_const(constant, f"const_{constant:x}")
        depth2_threshold = self.scratch_const(12, "depth2_threshold")
        for op1, _val1, op2, op3, shift in HASH_STAGES:
            if (op1, op2, op3) == ("+", "+", "<<"):
                multiplier = 1 + (1 << shift)
                if multiplier not in hash_constants:
                    hash_constants[multiplier] = vector_const(
                        multiplier, f"const_{multiplier:x}"
                    )

        # Fold initialization into the same DAG as the kernel.  Main operations
        # gain dependencies on the setup instructions that produce their inputs.
        setup_ops, setup_writers = self.pack_setup(
            extract=True, setup_chunk=SETUP_CHUNK
        )
        idx = self.alloc_scratch("idx", batch_size)
        val = self.alloc_scratch("val", batch_size)
        input_addrs = self.alloc_scratch("input_addrs", chunk_count)
        # Node/hash and retained parity scratch is virtual; physical storage
        # follows scheduled lifetimes, including cross-round parity consumers.
        node_or_addr = self.scratch_ptr
        tmp1 = node_or_addr + chunk_count * NODE_VIRTUAL_VECTORS * VLEN
        tmp2 = tmp1 + batch_size
        path_bits_base = tmp2 + batch_size

        ops = setup_ops
        setup_count = len(setup_ops)
        shared_select_uses = []
        node_pool_uses = []
        emit_context = {"chunk": -1, "round": -1, "local_seq": 0}

        def emit(engine, slot, *deps):
            op_id = len(ops)
            flat_deps = []
            for dep in deps:
                if isinstance(dep, (list, tuple, set)):
                    flat_deps.extend(dep)
                else:
                    flat_deps.append(dep)
            reads, _writes = self.instruction_accesses(engine, slot)
            flat_deps.extend(
                setup_writers[addr] for addr in reads if addr in setup_writers
            )
            ops.append(
                {
                    "engine": engine,
                    "slot": slot,
                    "deps": flat_deps,
                    **emit_context,
                }
            )
            emit_context["local_seq"] += 1
            return op_id

        def emit_scalar_vector(op, dest, left, right, *deps):
            return [
                emit("alu", (op, dest + lane, left + lane, right + lane), *deps)
                for lane in range(VLEN)
            ]

        def emit_scalar_rhs(op, dest, left, right, *deps):
            return [
                emit("alu", (op, dest + lane, left + lane, right), *deps)
                for lane in range(VLEN)
            ]

        def select(dest, cond, left, right, *deps):
            return emit("flow", ("vselect", dest, cond, left, right), *deps)

        # Keep these scalar addresses live through output stores, avoiding
        # a separate flow add_imm for each chunk at the end of execution.
        input_addr_ready = [
            emit("load", ("const", input_addrs + chunk_no,
                          inp_values_p + chunk_no * VLEN))
            for chunk_no in range(chunk_count)
        ]

        for chunk_no in range(chunk_count):
            emit_context.update(chunk=chunk_no, round=-1, local_seq=0)
            offset = chunk_no * VLEN
            chunk_idx = idx + offset
            chunk_val = val + offset
            chunk_node = node_or_addr + chunk_no * NODE_VIRTUAL_VECTORS * VLEN
            chunk_tmp1 = tmp1 + offset
            chunk_tmp2 = tmp2 + offset

            val_ready = emit(
                "load",
                ("vload", chunk_val, input_addrs + chunk_no),
                input_addr_ready[chunk_no],
            )
            idx_ready = None  # At the root, the index is implicit, not read.
            round_starts = []
            path_bits = {}
            path_bit_uses = []

            def lookup_mask(bit, destination, *deps):
                # Bit j of S at depth d is parity from depth d-1-j.
                # Retain explicit overwrite dependencies. Direct interpolation
                # can omit the index barrier, but must wait for its own parity.
                if depth <= PATH_REUSE_DEPTH and depth - 1 - bit in path_bits:
                    address, producers = path_bits[depth - 1 - bit]
                    ready = list(producers)
                    for dep in deps:
                        ready.extend(dep if isinstance(dep, (list, tuple)) else [dep])
                    return address, ready
                return destination, emit_scalar_rhs(
                    "&", destination, chunk_idx, self.const_map[1 << bit], *deps
                )

            for round_no in range(rounds):
                emit_context["round"] = round_no
                round_starts.append(len(ops))
                depth = round_no % (forest_height + 1)
                if depth == 0:
                    path_bits = {}
                    reuse_depth = min(PATH_REUSE_DEPTH, forest_height, rounds - 1 - round_no)
                    if reuse_depth >= 4 and not (
                        cache_depth4 and chunk_no < DEPTH4_CACHE_CHUNKS
                        and round_no + 4 in DEPTH4_CACHE_ROUNDS
                    ):
                        reuse_depth = 3
                    retained_depth = max(reuse_depth - 1, min(DIRECT_PATH_DEPTH, reuse_depth))
                cached_lookup = (0 < depth <= LOOKUP_DEPTH) or (
                    depth == 4 and cache_depth4 and chunk_no < DEPTH4_CACHE_CHUNKS
                    and round_no in DEPTH4_CACHE_ROUNDS
                )
                direct_lookup = cached_lookup and 2 <= depth <= DIRECT_PATH_DEPTH
                interpolation, interpolation_ready = (
                    path_bits[depth - 1] if direct_lookup else (chunk_idx, idx_ready)
                )
                lookup_barrier = None if direct_lookup else idx_ready
                # A gathered node dies at the input XOR, before hash tmp2 use.
                chunk_node = (node_or_addr + chunk_no * NODE_VIRTUAL_VECTORS * VLEN
                              if cached_lookup else chunk_tmp2)
                lookup_start = len(ops)
                if depth == 0:
                    root_round_value = (
                        root_value_copy if round_no == 0 else root_value_encoded
                    )
                    val_ready = emit_scalar_rhs(
                        "^", chunk_val, chunk_val, root_round_value, val_ready
                    )
                elif depth == 1 and LOOKUP_DEPTH >= 1:
                    # Read root parity, either retained or held in idx.
                    root_parity = path_bits[0][0] if 0 in path_bits else chunk_idx
                    selected = select(
                        chunk_node, root_parity, depth1_left_vec, depth1_right_vec,
                        idx_ready,
                    )
                    val_ready = emit(
                        "valu",
                        ("^", chunk_val, chunk_val, chunk_node),
                        val_ready,
                        selected,
                    )
                    if depth < forest_height and round_no < rounds - 1:
                        # Next S is (p0 ? -6 : -8) + p1. Prepare its
                        # base while this round hashes, after the last p0 read.
                        idx_ready = select(
                            chunk_idx, root_parity, depth2_left_base,
                            depth2_right_base, selected,
                        )
                elif depth == 2 and LOOKUP_DEPTH >= 2 and PAIR_LOOKUP_DEPTH >= 2:
                    half_mask, half = lookup_mask(1, chunk_tmp1, lookup_barrier)
                    slope = select(
                        chunk_node, half_mask, depth2_vectors[1], depth2_vectors[3], half
                    )
                    intercept = select(
                        chunk_tmp2, half_mask, depth2_vectors[0], depth2_vectors[2], half
                    )
                    selected = emit(
                        "valu", ("multiply_add", chunk_node, interpolation, chunk_node, chunk_tmp2),
                        slope, intercept, interpolation_ready,
                    )
                    val_ready = emit(
                        "valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected
                    )
                elif depth == 2 and LOOKUP_DEPTH >= 2:
                    if chunk_no < BIT_MASK_VALU_CHUNKS:
                        low_bit = emit(
                            "valu", ("&", chunk_tmp1, chunk_idx, one), idx_ready
                        )
                    else:
                        low_bit = emit_scalar_vector(
                            "&", chunk_tmp1, chunk_idx, one, idx_ready
                        )
                    left = select(
                        chunk_node, chunk_tmp1, depth2_vectors[1], depth2_vectors[0], low_bit
                    )
                    right = select(
                        chunk_tmp2, chunk_tmp1, depth2_vectors[3], depth2_vectors[2], low_bit
                    )
                    low_half = [
                        emit(
                            "alu", ("<", chunk_tmp1 + lane, chunk_idx + lane, depth2_threshold),
                            left, right,
                        )
                        for lane in range(VLEN)
                    ]
                    selected = select(
                        chunk_node, chunk_tmp1, chunk_node, chunk_tmp2, low_half, left, right
                    )
                    val_ready = emit(
                        "valu",
                        ("^", chunk_val, chunk_val, chunk_node),
                        val_ready,
                        selected,
                    )
                elif depth == 3 and LOOKUP_DEPTH >= 3 and PAIR_LOOKUP_DEPTH >= 3 and DEPTH3_COEFF_SELECT:
                    upper_d = chunk_node + VLEN
                    final_mask = chunk_node + 2 * VLEN
                    lower_mask, lower_half = lookup_mask(1, chunk_tmp1, lookup_barrier)
                    # Pair starts A=14,16,18,20 map to S=-9,-11,-13,-15;
                    # (S >> 1)&3 gives 3,2,1,0 for both members of each pair.
                    lower_slope = select(chunk_node, lower_mask, depth3_vectors[5], depth3_vectors[7], lower_half)
                    lower_intercept = select(chunk_tmp2, lower_mask, depth3_vectors[4], depth3_vectors[6], lower_half)
                    upper_slope = select(upper_d, lower_mask, depth3_vectors[1], depth3_vectors[3], lower_slope, lower_intercept)
                    upper_intercept = select(chunk_tmp1, lower_mask, depth3_vectors[0], depth3_vectors[2], upper_slope)
                    final_mask, half = lookup_mask(2, final_mask, upper_intercept)
                    slope = select(chunk_node, final_mask, upper_d, chunk_node, half, lower_slope, upper_slope)
                    intercept = select(chunk_tmp2, final_mask, chunk_tmp1, chunk_tmp2, half, lower_intercept, upper_intercept)
                    selected = emit(
                        "valu", ("multiply_add", chunk_node, interpolation, chunk_node, chunk_tmp2), slope, intercept, interpolation_ready
                    )
                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected)
                elif depth == 3 and LOOKUP_DEPTH >= 3 and PAIR_LOOKUP_DEPTH >= 3:
                    lower_half = emit_scalar_rhs(
                        "<", chunk_tmp1, chunk_idx, depth3_thresholds[16], idx_ready
                    )
                    lower_slope = select(
                        chunk_node, chunk_tmp1, depth3_vectors[1], depth3_vectors[3], lower_half
                    )
                    lower_intercept = select(
                        chunk_tmp2, chunk_tmp1, depth3_vectors[0], depth3_vectors[2], lower_half
                    )
                    lower = emit(
                        "valu", ("multiply_add", chunk_node, chunk_idx, chunk_node, chunk_tmp2),
                        lower_slope, lower_intercept,
                    )
                    upper_half = emit_scalar_rhs(
                        "<", chunk_tmp1, chunk_idx, depth3_thresholds[20], lower
                    )
                    upper_slope = select(
                        chunk_tmp2, chunk_tmp1, depth3_vectors[5], depth3_vectors[7], upper_half
                    )
                    # Read the mask before overwriting it with the intercept.
                    upper_intercept = select(
                        chunk_tmp1, chunk_tmp1, depth3_vectors[4], depth3_vectors[6],
                        upper_half, upper_slope,
                    )
                    upper = emit(
                        "valu", ("multiply_add", chunk_tmp2, chunk_idx, chunk_tmp2, chunk_tmp1),
                        upper_slope, upper_intercept,
                    )
                    half = emit_scalar_rhs(
                        "<", chunk_tmp1, chunk_idx, depth3_thresholds[18], upper
                    )
                    selected = select(chunk_node, chunk_tmp1, chunk_node, chunk_tmp2, half, lower, upper)
                    val_ready = emit(
                        "valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected
                    )
                elif depth == 3 and LOOKUP_DEPTH >= 3:
                    def mask(op, scalar, *deps):
                        if op == "&" and chunk_no < BIT_MASK_VALU_CHUNKS:
                            return emit(
                                "valu", (op, chunk_tmp1, chunk_idx, one), *deps
                            )
                        return [
                            emit("alu", (op, chunk_tmp1 + lane, chunk_idx + lane, scalar), *deps)
                            for lane in range(VLEN)
                        ]

                    odd = mask("&", self.const_map[1], idx_ready)
                    lower_left = select(
                        chunk_node, chunk_tmp1, depth3_vectors[1], depth3_vectors[0], odd
                    )
                    lower_right = select(
                        chunk_tmp2, chunk_tmp1, depth3_vectors[3], depth3_vectors[2], odd
                    )
                    lower_half = mask("<", depth3_thresholds[16], lower_left, lower_right)
                    lower = select(
                        chunk_node, chunk_tmp1, chunk_node, chunk_tmp2,
                        lower_half, lower_left, lower_right,
                    )
                    odd = mask("&", self.const_map[1], lower)
                    if DEPTH3_SHARED_SELECT:
                        shared_bank = chunk_no % len(depth3_shared)
                        shared_buffer = depth3_shared[shared_bank]
                        upper_left = select(
                            chunk_tmp2, chunk_tmp1, depth3_vectors[5], depth3_vectors[4],
                            odd, lower,
                        )
                        upper_right = select(
                            shared_buffer, chunk_tmp1,
                            depth3_vectors[7], depth3_vectors[6],
                            odd,
                            list(range(setup_count)) if shared_buffer == top_nodes else (),
                        )
                        upper_half = mask("<", depth3_thresholds[20], upper_left, upper_right)
                        upper = select(
                            chunk_tmp2, chunk_tmp1, chunk_tmp2, shared_buffer,
                            upper_half, upper_left, upper_right,
                        )
                        shared_select_uses.append(
                            (shared_bank, round_no, chunk_no, upper_right, upper)
                        )
                    else:
                        upper = emit("valu", ("multiply_add", chunk_tmp2, chunk_tmp1, depth3_vectors[7], depth3_vectors[5]), odd, lower)
                        upper_half = mask("<", depth3_thresholds[13], upper)
                        upper = emit("valu", ("multiply_add", chunk_tmp2, chunk_tmp1, chunk_tmp2, depth3_vectors[4]), upper_half, upper)
                        odd = mask("&", self.const_map[1], upper)
                        upper = emit("valu", ("multiply_add", chunk_tmp2, chunk_tmp1, depth3_vectors[6], chunk_tmp2), odd, upper)
                    half = mask("<", depth3_thresholds[18], upper)
                    selected = select(
                        chunk_node, chunk_tmp1, chunk_node, chunk_tmp2, half, upper, lower
                    )
                    val_ready = emit(
                        "valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected
                    )
                elif (depth == 4 and cache_depth4 and chunk_no < DEPTH4_CACHE_CHUNKS
                      and round_no in DEPTH4_CACHE_ROUNDS and DEPTH4_BIT_SELECT):
                    upper_result = chunk_node + VLEN
                    # Four interpolations trade three extra MACs for three
                    # fewer selects. Share the three low-bit masks.
                    masks = [chunk_node + n * VLEN for n in (2, 3, 4)]
                    auxiliary = masks[2]
                    masks[0], mask0 = lookup_mask(1, masks[0], lookup_barrier)
                    mask1 = None
                    order = sorted(range(8), key=lambda p: ((5 - 22 - 2*p) >> 1) & 7)
                    def quartet(pair, dest, *deps):
                        p, q = order[pair:pair+2]
                        slope = select(chunk_tmp1, masks[0], depth4_vectors[2*q+1], depth4_vectors[2*p+1], mask0, *deps)
                        intercept = select(chunk_tmp2, masks[0], depth4_vectors[2*q], depth4_vectors[2*p], mask0, *deps)
                        return emit("valu", ("multiply_add", dest, interpolation, chunk_tmp1, chunk_tmp2), slope, intercept, interpolation_ready)
                    def octet(pair, dest, *deps):
                        nonlocal mask1
                        lower = quartet(pair, dest, *deps)
                        upper = quartet(pair+2, auxiliary, lower)
                        if mask1 is None:
                            masks[1], mask1 = lookup_mask(2, masks[1], upper)
                        return select(dest, masks[1], auxiliary, dest, mask1, lower, upper)
                    lower = octet(0, chunk_node, idx_ready)
                    upper = octet(4, upper_result, lower)
                    masks[2], mask2 = lookup_mask(3, masks[2], upper)
                    selected = select(chunk_node, masks[2], upper_result, chunk_node, lower, upper, mask2)
                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected)
                elif (depth == 4 and cache_depth4 and chunk_no < DEPTH4_CACHE_CHUNKS
                      and round_no in DEPTH4_CACHE_ROUNDS):
                    auxiliary = chunk_node + VLEN
                    upper_result = chunk_node + 2 * VLEN

                    def quartet(first, destination, *deps):
                        half = emit_scalar_rhs(
                            "<", chunk_tmp1, chunk_idx,
                            depth4_thresholds[22 + first + 2], *deps,
                        )
                        slope = select(
                            chunk_tmp2, chunk_tmp1, depth4_vectors[first + 1],
                            depth4_vectors[first + 3], half,
                        )
                        intercept = select(
                            chunk_tmp1, chunk_tmp1, depth4_vectors[first],
                            depth4_vectors[first + 2], half, slope,
                        )
                        return emit(
                            "valu", ("multiply_add", destination, chunk_idx, chunk_tmp2, chunk_tmp1),
                            slope, intercept,
                        )

                    def octet(first, destination, *deps):
                        lower = quartet(first, destination, *deps)
                        upper = quartet(first + 4, auxiliary, lower)
                        half = emit_scalar_rhs(
                            "<", chunk_tmp1, chunk_idx,
                            depth4_thresholds[22 + first + 4], upper,
                        )
                        return select(destination, chunk_tmp1, destination, auxiliary, half, lower, upper)

                    lower = octet(0, chunk_node, idx_ready)
                    upper = octet(8, upper_result, lower)
                    half = emit_scalar_rhs("<", chunk_tmp1, chunk_idx, depth4_thresholds[30], upper)
                    selected = select(chunk_node, chunk_tmp1, chunk_node, upper_result, half, lower, upper)
                    val_ready = emit(
                        "valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected
                    )
                else:
                    # Decode only gathered addresses; cached lookup uses S.
                    address_ready = [
                        emit("alu", ("-", chunk_node + lane, address_five, chunk_idx + lane), idx_ready)
                        for lane in range(VLEN)
                    ]
                    node_loads = [
                        emit(
                            "load",
                            ("load_offset", chunk_node, chunk_node, lane),
                            address_ready[lane],
                        )
                        for lane in range(VLEN)
                    ]
                    node_loads = [
                        emit(
                            "alu",
                            ("^", chunk_node + lane, chunk_node + lane, final_xor_const),
                            node_loads[lane],
                        )
                        for lane in range(VLEN)
                    ]

                    val_ready = emit(
                        "valu",
                        ("^", chunk_val, chunk_val, chunk_node),
                        val_ready,
                        node_loads,
                    )
                if cached_lookup:
                    node_pool_uses.append((chunk_node, lookup_start, len(ops), NODE_VIRTUAL_VECTORS))
                for stage, (op1, val1, op2, op3, val3) in enumerate(HASH_STAGES):
                    if stage == 3:
                        continue  # Already evaluated together with stage 2.
                    if stage == 2:
                        left = emit(
                            "valu",
                            ("multiply_add", chunk_tmp1, chunk_val,
                             hash_constants[hash23_multiplier],
                             hash_constants[hash23_left_bias]),
                            val_ready,
                        )
                        right = emit(
                            "valu",
                            ("multiply_add", chunk_tmp2, chunk_val,
                             hash_constants[hash23_right_multiplier],
                             hash_constants[hash23_right_bias]),
                            val_ready,
                        )
                        val_ready = emit(
                            "valu", ("^", chunk_val, chunk_tmp1, chunk_tmp2),
                            left, right,
                        )
                    elif val3 == 16:
                        shifted = emit(
                            "valu",
                            (">>", chunk_tmp2, chunk_val, hash_constants[16]),
                            val_ready,
                        )
                        if round_no == rounds - 1:
                            decoded = emit(
                                "valu",
                                ("^", chunk_tmp1, chunk_val, final_xor_vec),
                                val_ready,
                            )
                            val_ready = emit(
                                "valu",
                                ("^", chunk_val, chunk_tmp1, chunk_tmp2),
                                decoded,
                                shifted,
                            )
                        else:
                            val_ready = emit(
                                "valu",
                                ("^", chunk_val, chunk_val, chunk_tmp2),
                                val_ready,
                                shifted,
                            )
                    elif (op1, op2, op3) == ("+", "+", "<<"):
                        multiplier = 1 + (1 << val3)
                        val_ready = emit(
                            "valu",
                            (
                                "multiply_add",
                                chunk_val,
                                chunk_val,
                                hash_constants[multiplier],
                                hash_constants[val1],
                            ),
                            val_ready,
                        )
                    else:
                        scalar_hash_arm = (
                            val3 == 19 and chunk_no < HASH_ALU_CHUNKS
                        )
                        if scalar_hash_arm:
                            left = emit_scalar_vector(
                                op1, chunk_tmp1, chunk_val, hash_constants[val1], val_ready
                            )
                        else:
                            left = emit(
                                "valu",
                                (op1, chunk_tmp1, chunk_val, hash_constants[val1]),
                                val_ready,
                            )
                        right = emit(
                            "valu",
                            (op3, chunk_tmp2, chunk_val, hash_constants[val3]),
                            val_ready,
                        )
                        val_ready = emit(
                            "valu",
                            (op2, chunk_val, chunk_tmp1, chunk_tmp2),
                            left,
                            right,
                        )

                # The final index is not part of the required output.  At the
                # leaf, the next round is known to restart at the root.
                if round_no == rounds - 1:
                    continue
                if round_no % (forest_height + 1) == forest_height:
                    # The root does not read an index, and its child index is
                    # rebuilt from parity. No physical zero/reset is needed.
                    idx_ready = None
                    continue

                # Encoded hash parity p is the inverse of raw hash parity.
                # Keep p at the root, then use S'=2*S+p at deeper levels.
                parity_dest = chunk_idx if depth == 0 else chunk_tmp1
                if depth < retained_depth:
                    # Unique logical producer per group/round; its storage is
                    # colored over all consumers without inserting copies.
                    parity_dest = path_bits_base + (round_no * chunk_count + chunk_no) * VLEN
                    path_bit_uses.append((parity_dest, len(ops)))
                parity = emit_scalar_vector(
                    "&", parity_dest, chunk_val, one, val_ready
                )
                if depth < retained_depth:
                    path_bits[depth] = (parity_dest, parity)
                if depth == 0:
                    idx_ready = parity
                    continue
                if depth == 1:
                    idx_ready = emit_scalar_vector("+", chunk_idx, chunk_idx, parity_dest, parity, idx_ready)
                else:
                    idx_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, two, parity_dest), parity, idx_ready)

            node_pool_uses.extend((address, start, len(ops), 1) for address, start in path_bit_uses)
            for start, end in zip(round_starts, round_starts[1:] + [len(ops)]):
                node_pool_uses.append((chunk_tmp1, start, end, 1))
                node_pool_uses.append((chunk_tmp2, start, end, 1))
            store_addr = input_addr_ready[chunk_no]
            emit(
                "store",
                ("vstore", input_addrs + chunk_no, chunk_val),
                val_ready,
                store_addr,
            )

        # Only the upper-right pair uses this one shared vector. Release it as
        # soon as the upper quartet is selected. Descending chunk order matches
        # the cohort scheduler, avoiding a backwards chain through the wavefront.
        # All setup consumers finish before the first overwrite (explicit deps).
        for bank in range(len(depth3_shared)):
            previous_use = None
            uses = (item for item in shared_select_uses if item[0] == bank)
            for _, _, _, writer, reader in sorted(
                uses, key=lambda item: (item[1], -item[2])
            ):
                if previous_use is not None:
                    ops[writer]["deps"].append(previous_use)
                previous_use = reader
        self.schedule(ops)
        self.allocate_node_lifetimes(ops, node_pool_uses, depth4_vectors if cache_depth4 else ())
        for bundle in self.instrs:
            for op, dest, left, right in bundle.pop("alu_vector", ()):
                bundle.setdefault("alu", []).extend(
                    (op, dest + lane, left + lane, right + lane) for lane in range(VLEN)
                )

    def allocate_node_lifetimes(self, ops, uses, reusable=()):
        """Color node vectors after scheduling, preserving every issue cycle."""
        times = {id(slot): cycle for cycle, bundle in enumerate(self.instrs)
                 for slots in bundle.values() for slot in slots}
        intervals = []
        for base, start, end, width in uses:
            for address in (base + n * VLEN for n in range(width)):
                addresses = set(range(address, address + VLEN))
                touched = []
                for op in ops[start:end]:
                    reads, writes = self.instruction_accesses(op["engine"], op["slot"])
                    if addresses & (reads | writes):
                        touched.append(op)
                if touched:
                    intervals.append((min(times[id(o["slot"])] for o in touched),
                                      max(times[id(o["slot"])] for o in touched),
                                      address, start, end))
        # A cache coefficient's physical vector can host nodes after its last
        # scheduled use. All original reads finish before any reused write.
        last_use = {address: -1 for address in reusable}
        for op in ops:
            reads, writes = self.instruction_accesses(op["engine"], op["slot"])
            for address in reusable:
                if any(address <= a < address + VLEN for a in reads | writes):
                    last_use[address] = max(last_use[address], times[id(op["slot"])])
        physical_slots = list(reusable)
        slots_end = [last_use[address] for address in reusable]
        replacements = {}
        for first, last, address, start, end in sorted(intervals):
            # Reads observe start-of-cycle state, writes commit at cycle end.
            color = next((i for i, stop in enumerate(slots_end) if stop <= first),
                         len(slots_end))
            if color == len(slots_end):
                slots_end.append(last)
                physical_slots.append(self.scratch_ptr + (color - len(reusable)) * VLEN)
            else:
                slots_end[color] = last
            physical = physical_slots[color]
            for op in ops[start:end]:
                original = op["slot"]
                slot = list(replacements.get(id(original), original))
                engine = op["engine"]
                positions = (range(1, len(slot)) if engine in ("alu", "valu")
                             or (engine == "flow" and slot[0] == "vselect")
                             else (1, 2) if engine in ("load", "store")
                             and slot[0] != "const" else (1,))
                for pos in positions:
                    # Compare original addresses, never already-renamed ones.
                    if address <= original[pos] < address + VLEN:
                        slot[pos] = physical + original[pos] - address
                replacements[id(original)] = tuple(slot)
        self.alloc_scratch("node_pool", (len(slots_end) - len(reusable)) * VLEN)
        for bundle in self.instrs:
            for engine, slots in bundle.items():
                bundle[engine] = [replacements.get(id(slot), slot) for slot in slots]
        for op in ops:
            op["slot"] = replacements.get(id(op["slot"]), op["slot"])

BASELINE = 147734

def do_kernel_test(
    forest_height: int,
    rounds: int,
    batch_size: int,
    seed: int = 123,
    trace: bool = False,
    prints: bool = False,
):
    print(f"{forest_height=}, {rounds=}, {batch_size=}")
    random.seed(seed)
    forest = Tree.generate(forest_height)
    inp = Input.generate(forest, batch_size, rounds)
    mem = build_mem_image(forest, inp)

    kb = KernelBuilder()
    kb.build_kernel(forest.height, len(forest.values), len(inp.indices), rounds)
    # print(kb.instrs)

    value_trace = {}
    machine = Machine(
        mem,
        kb.instrs,
        kb.debug_info(),
        n_cores=N_CORES,
        value_trace=value_trace,
        trace=trace,
    )
    machine.prints = prints
    machine.enable_pause = False
    machine.run()
    for ref_mem in reference_kernel2(mem, value_trace):
        pass
    inp_values_p = ref_mem[6]
    if prints:
        print(machine.mem[inp_values_p : inp_values_p + len(inp.values)])
        print(ref_mem[inp_values_p : inp_values_p + len(inp.values)])
    assert (
        machine.mem[inp_values_p : inp_values_p + len(inp.values)]
        == ref_mem[inp_values_p : inp_values_p + len(inp.values)]
    ), "Incorrect final values"

    print("CYCLES: ", machine.cycle)
    print("Speedup over baseline: ", BASELINE / machine.cycle)
    return machine.cycle


class Tests(unittest.TestCase):
    def test_ref_kernels(self):
        """
        Test the reference kernels against each other
        """
        random.seed(123)
        for i in range(10):
            f = Tree.generate(4)
            inp = Input.generate(f, 10, 6)
            mem = build_mem_image(f, inp)
            reference_kernel(f, inp)
            for _ in reference_kernel2(mem, {}):
                pass
            assert inp.indices == mem[mem[5] : mem[5] + len(inp.indices)]
            assert inp.values == mem[mem[6] : mem[6] + len(inp.values)]

    def test_kernel_trace(self):
        # Full-scale example for performance testing
        do_kernel_test(10, 16, 256, trace=True, prints=False)

    # Passing this test is not required for submission, see submission_tests.py for the actual correctness test
    # You can uncomment this if you think it might help you debug
    # def test_kernel_correctness(self):
    #     for batch in range(1, 3):
    #         for forest_height in range(3):
    #             do_kernel_test(
    #                 forest_height + 2, forest_height + 4, batch * 16 * VLEN * N_CORES
    #             )

    def test_kernel_cycles(self):
        do_kernel_test(10, 16, 256)


# To run all the tests:
#    python perf_takehome.py
# To run a specific test:
#    python perf_takehome.py Tests.test_kernel_cycles
# To view a hot-reloading trace of all the instructions:  **Recommended debug loop**
# NOTE: The trace hot-reloading only works in Chrome. In the worst case if things aren't working, drag trace.json onto https://ui.perfetto.dev/
#    python perf_takehome.py Tests.test_kernel_trace
# Then run `python watch_trace.py` in another tab, it'll open a browser tab, then click "Open Perfetto"
# You can then keep that open and re-run the test to see a new trace.

# To run the proper checks to see which thresholds you pass:
#    python tests/submission_tests.py

if __name__ == "__main__":
    unittest.main()
