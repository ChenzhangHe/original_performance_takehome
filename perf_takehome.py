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
ALU_ROOT_CHUNKS = 32
ALU_INDEX_CHUNKS = 32
SETUP_CHUNK = 32
HASH_ALU_CHUNKS = 2
DEPTH3_SHARED_SELECT = True


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
            raise ValueError(policy)

        def make_schedule(policy):
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
                    candidates.sort(
                        key=priorities.__getitem__, reverse=True
                    )
                    selected = candidates[: SLOT_LIMITS[engine]]
                    del candidates[: len(selected)]
                    if selected:
                        bundle[engine] = [ops[op_id]["slot"] for op_id in selected]
                        chosen.extend(selected)

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
        )
        self.schedule_stats = {}
        best = None
        for policy in dict.fromkeys(policies):
            bundles = make_schedule(policy)
            self.schedule_stats[policy] = len(bundles)
            if best is None or len(bundles) < len(best):
                best = bundles
                self.schedule_policy = policy
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
        chunk_count = batch_size // VLEN
        forest_values_p = 7
        inp_values_p = forest_values_p + n_nodes + batch_size

        def vector_const(value, name):
            scalar = self.scratch_const(value, f"{name}_scalar")
            vector = self.alloc_scratch(name, VLEN)
            self.add("valu", ("vbroadcast", vector, scalar))
            return vector

        one = vector_const(1, "one")
        two = vector_const(2, "two")
        forest_values_scalar = self.scratch_const(forest_values_p, "forest_values_scalar")
        top_nodes = self.alloc_scratch("top_nodes", VLEN)
        self.add("load", ("vload", top_nodes, self.const_map[forest_values_p]))
        root_value = top_nodes
        root_value_vec = self.alloc_scratch("root_value_vec", VLEN)
        self.add("valu", ("vbroadcast", root_value_vec, root_value))

        depth1_left = top_nodes + 1
        depth1_right = top_nodes + 2
        depth1_right_vec = self.alloc_scratch("depth1_right_vec", VLEN)
        depth1_left_vec = self.alloc_scratch("depth1_left_vec", VLEN)
        self.add("valu", ("vbroadcast", depth1_right_vec, depth1_right))
        self.add("valu", ("vbroadcast", depth1_left_vec, depth1_left))

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
            depth3_vectors = []
            for i in range(4):
                vector = self.alloc_scratch(f"depth3_lower_{i}", VLEN)
                self.add("valu", ("vbroadcast", vector, top_nodes + i))
                depth3_vectors.append(vector)
            a, b, c, d = [top_nodes + i for i in range(4, 8)]
            if DEPTH3_SHARED_SELECT:
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
            depth3_thresholds = {n: self.scratch_const(n) for n in (9, 11, 13)}

        hash_constants = {}
        for op1, val1, op2, op3, val3 in HASH_STAGES:
            if val1 not in hash_constants:
                hash_constants[val1] = vector_const(val1, f"const_{val1:x}")
            fused = (op1, op2, op3) == ("+", "+", "<<")
            if not fused and val3 not in hash_constants:
                hash_constants[val3] = vector_const(val3, f"const_{val3:x}")
        five_scalar = self.scratch_const(5, "five_scalar")
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
        node_or_addr = self.alloc_scratch("node_or_addr", batch_size)
        tmp1 = self.alloc_scratch("tmp1", batch_size)
        tmp2 = self.alloc_scratch("tmp2", batch_size)
        input_addrs = node_or_addr

        ops = setup_ops
        setup_count = len(setup_ops)
        shared_select_uses = []
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

        def select(dest, cond, left, right, *deps):
            return emit("flow", ("vselect", dest, cond, left, right), *deps)

        # These addresses die at the input vload, so node buffers can hold them.
        # Keep constants independent: deriving them from chunk 0 would require
        # anti-dependencies before that chunk reuses its node buffer.
        input_addr_ready = [
            emit("load", ("const", input_addrs + chunk_no * VLEN,
                          inp_values_p + chunk_no * VLEN))
            for chunk_no in range(chunk_count)
        ]

        for chunk_no in range(chunk_count):
            emit_context.update(chunk=chunk_no, round=-1, local_seq=0)
            offset = chunk_no * VLEN
            chunk_idx = idx + offset
            chunk_val = val + offset
            chunk_node = node_or_addr + offset
            chunk_tmp1 = tmp1 + offset
            chunk_tmp2 = tmp2 + offset

            val_ready = emit(
                "load",
                ("vload", chunk_val, input_addrs + offset),
                input_addr_ready[chunk_no],
            )
            idx_ready = None  # At the root, the index is implicit, not read.

            for round_no in range(rounds):
                emit_context["round"] = round_no
                depth = round_no % (forest_height + 1)
                if depth == 0:
                    if chunk_no < ALU_ROOT_CHUNKS:
                        val_ready = emit_scalar_vector(
                            "^", chunk_val, chunk_val, root_value_vec, val_ready
                        )
                    else:
                        val_ready = emit(
                            "valu",
                            ("^", chunk_val, chunk_val, root_value_vec),
                            val_ready,
                        )
                elif depth == 1 and LOOKUP_DEPTH >= 1:
                    selected = emit(
                        "valu",
                        ("&", chunk_node, chunk_idx, one),
                        idx_ready,
                    )
                    selected = select(
                        chunk_node, chunk_node, depth1_left_vec, depth1_right_vec,
                        selected,
                    )
                    val_ready = emit(
                        "valu",
                        ("^", chunk_val, chunk_val, chunk_node),
                        val_ready,
                        selected,
                    )
                elif depth == 2 and LOOKUP_DEPTH >= 2:
                    low_bit = emit_scalar_vector(
                        "&", chunk_tmp1, chunk_idx, one, idx_ready
                    )
                    left = select(
                        chunk_node, chunk_tmp1, depth2_vectors[0], depth2_vectors[1], low_bit
                    )
                    right = select(
                        chunk_tmp2, chunk_tmp1, depth2_vectors[2], depth2_vectors[3], low_bit
                    )
                    low_half = [
                        emit(
                            "alu", ("<", chunk_tmp1 + lane, chunk_idx + lane, five_scalar),
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
                elif depth == 3 and LOOKUP_DEPTH >= 3:
                    def mask(op, scalar, *deps):
                        return [
                            emit("alu", (op, chunk_tmp1 + lane, chunk_idx + lane, scalar), *deps)
                            for lane in range(VLEN)
                        ]

                    odd = mask("&", self.const_map[1], idx_ready)
                    lower_left = select(
                        chunk_node, chunk_tmp1, depth3_vectors[0], depth3_vectors[1], odd
                    )
                    lower_right = select(
                        chunk_tmp2, chunk_tmp1, depth3_vectors[2], depth3_vectors[3], odd
                    )
                    lower_half = mask("<", depth3_thresholds[9], lower_left, lower_right)
                    lower = select(
                        chunk_node, chunk_tmp1, chunk_node, chunk_tmp2,
                        lower_half, lower_left, lower_right,
                    )
                    odd = mask("&", self.const_map[1], lower)
                    if DEPTH3_SHARED_SELECT:
                        upper_left = select(
                            chunk_tmp2, chunk_tmp1, depth3_vectors[4], depth3_vectors[5],
                            odd, lower,
                        )
                        upper_right = select(
                            top_nodes, chunk_tmp1, depth3_vectors[6], depth3_vectors[7],
                            odd, list(range(setup_count)),
                        )
                        upper_half = mask("<", depth3_thresholds[13], upper_left, upper_right)
                        upper = select(
                            chunk_tmp2, chunk_tmp1, chunk_tmp2, top_nodes,
                            upper_half, upper_left, upper_right,
                        )
                        shared_select_uses.append((round_no, chunk_no, upper_right, upper))
                    else:
                        upper = emit("valu", ("multiply_add", chunk_tmp2, chunk_tmp1, depth3_vectors[7], depth3_vectors[5]), odd, lower)
                        upper_half = mask("<", depth3_thresholds[13], upper)
                        upper = emit("valu", ("multiply_add", chunk_tmp2, chunk_tmp1, chunk_tmp2, depth3_vectors[4]), upper_half, upper)
                        odd = mask("&", self.const_map[1], upper)
                        upper = emit("valu", ("multiply_add", chunk_tmp2, chunk_tmp1, depth3_vectors[6], chunk_tmp2), odd, upper)
                    half = mask("<", depth3_thresholds[11], upper)
                    selected = select(
                        chunk_node, chunk_tmp1, chunk_node, chunk_tmp2, half, upper, lower
                    )
                    val_ready = emit(
                        "valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected
                    )
                else:
                    addr_ready = [
                        emit(
                            "alu",
                            (
                                "+",
                                chunk_node + lane,
                                forest_values_scalar,
                                chunk_idx + lane,
                            ),
                            idx_ready[lane]
                            if isinstance(idx_ready, list)
                            else idx_ready,
                        )
                        for lane in range(VLEN)
                    ]
                    node_loads = [
                        emit(
                            "load",
                            ("load_offset", chunk_node, chunk_node, lane),
                            addr_ready[lane],
                        )
                        for lane in range(VLEN)
                    ]

                    val_ready = emit(
                        "valu",
                        ("^", chunk_val, chunk_val, chunk_node),
                        val_ready,
                        *node_loads,
                    )
                for op1, val1, op2, op3, val3 in HASH_STAGES:
                    if (op1, op2, op3) == ("+", "+", "<<"):
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
                        if chunk_no < HASH_ALU_CHUNKS and val3 == 19:
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

                # 2*idx + (1 if val is even else 2)
                # == 2*idx + 1 + (val & 1), without a flow-engine select.
                parity = emit_scalar_vector(
                    "&", chunk_tmp1, chunk_val, one, val_ready
                )
                if depth == 0:
                    doubled = None
                    index_base = one
                else:
                    doubled = emit(
                        "valu",
                        ("multiply_add", chunk_idx, chunk_idx, two, one),
                        val_ready,
                        idx_ready,
                    )
                    index_base = chunk_idx
                if chunk_no < ALU_INDEX_CHUNKS:
                    idx_ready = [
                        emit(
                            "alu",
                            (
                                "+",
                                chunk_idx + lane,
                                index_base + lane,
                                chunk_tmp1 + lane,
                            ),
                            parity[lane],
                            doubled,
                        )
                        for lane in range(VLEN)
                    ]
                else:
                    idx_ready = emit(
                        "valu",
                        ("+", chunk_idx, index_base, chunk_tmp1),
                        parity,
                        doubled,
                    )

            store_addr = emit(
                "flow",
                ("add_imm", chunk_node, forest_values_scalar, inp_values_p + offset - forest_values_p),
                val_ready,
            )
            emit(
                "store",
                ("vstore", chunk_node, chunk_val),
                val_ready,
                store_addr,
            )

        previous_use = None
        # Only the upper-right pair uses this one shared vector. Release it as
        # soon as the upper quartet is selected. Descending chunk order matches
        # the cohort scheduler, avoiding a backwards chain through the wavefront.
        # All setup consumers finish before the first overwrite (explicit deps).
        for _, _, writer, reader in sorted(shared_select_uses, key=lambda item: (item[0], -item[1])):
            if previous_use is not None:
                ops[writer]["deps"].append(previous_use)
            previous_use = reader
        self.schedule(ops)

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
