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
DEPTH4_CACHE_CHUNKS = 26
DEPTH4_CACHE_ROUNDS = (4, 15)
DEPTH4_FINAL_CACHE_CHUNKS = 1
PATH_REUSE_DEPTH = 4
DIRECT_PATH_DEPTH = 4
ALU_VECTOR_BACKLOG = 12
ALU_VECTOR_RESERVE_START = 60
INPUT_ADDRESS_CHAIN_LENGTH = 2
NODE_PREENCODE_DEPTH = 5  # Zero disables runtime preprocessing.
INPUT_ADDRESS_CONSUMER_PRIORITY = True
ALU_FRAGMENT_ISSUE = True
DIRECT_GATHER_ADDRESSES = True
BLOCKED_LOOKUP = True
BLOCKED_READ_BANKS = 4
BLOCKED_FINAL_CACHE_CHUNKS = 7
BLOCKED_FUSE_PARENT_XOR = True
BLOCKED_FUSE_SETUP_XOR = True
BLOCKED_SETUP_DEADLINES = True
BLOCKED_EARLY_TAIL_SELECT = True
BLOCKED_DROP_UNUSED_WEIGHT = True
BLOCKED_ENCODE_DEPTH6 = True
BLOCKED_REVERSE_INPUT_CHAIN_LENGTH = 4  # Zero retains the original forward chains.
BLOCKED_TAIL_START = 920
BLOCKED_COMPACT_DEEP = True
COMPACT_LANE_TAIL = True
COMPACT_PARENT_INDEX_SELECT = True
COMPACT_DEEP_LANDING = True
COMPACT_SHALLOW_LANDING = True
COMPACT_SETUP_DEADLINE_CAP = 6
COMPACT_FLOW_EXCHANGE = True
COMPACT_DEPTH3_GATHER_CHUNKS = 16
COMPACT_DEEP_SELECT_DELAY = 1
COMPACT_STARTUP_GROUPS = 2  # Zero restores the pre-bootstrap schedule.


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
                is_setup=True,
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

        # Bootstrap a small cohort through its first tree select. Prioritizing
        # input loads alone delays the root/constants they need; prioritize the
        # complete ancestor DAG by distance to the target instead. This changes
        # only ready-queue order, never an instruction or a dependency.
        startup_dag = set()
        startup_groups = getattr(self, "compact_startup_groups", 0)
        if startup_groups:
            targets = {}
            for op_id, op in enumerate(ops):
                if (not op.get("is_setup", False) and op["round"] == 1
                        and op["engine"] == "flow" and op["slot"][0] == "vselect"):
                    chunk = op["chunk"]
                    if chunk not in targets or op["local_seq"] < ops[targets[chunk]]["local_seq"]:
                        targets[chunk] = op_id
            assert len(targets) >= startup_groups
            todo = [targets[chunk] for chunk in sorted(targets, reverse=True)[:startup_groups]]
            while todo:
                op_id = todo.pop()
                if op_id not in startup_dag:
                    startup_dag.add(op_id)
                    todo.extend(ops[op_id]["deps"])
        startup_distance = [0] * len(ops)
        for op_id in reversed(topological):
            if op_id in startup_dag:
                for dep in ops[op_id]["deps"]:
                    startup_distance[dep] = max(startup_distance[dep], startup_distance[op_id] + 1)

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
            fragmented = policy.startswith("fragment_")
            if fragmented:
                policy = policy[len("fragment_"):]
            balanced = policy.startswith("balanced_")
            if balanced:
                policy = policy[len("balanced_"):]
            adaptive = policy.startswith("adaptive_")
            if adaptive:
                policy = policy[len("adaptive_"):]
            priorities = [priority(policy, op_id) for op_id in range(len(ops))]
            remaining = dep_counts.copy()
            ready = defaultdict(list)
            for op_id, count in enumerate(remaining):
                if count == 0:
                    ready[ops[op_id]["engine"]].append(op_id)

            partial = None  # (logical operation ID, first unissued lane)
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
                    if startup_dag:
                        # Stable sorting preserves every existing policy's
                        # ordering among operations outside the startup DAG.
                        candidates.sort(key=lambda i: (i in startup_dag, startup_distance[i]), reverse=True)
                    capacity = SLOT_LIMITS[engine]
                    # Reserve a whole eight-lane group before scalar issue
                    # when its ready queue is short. This avoids fragmenting
                    # ALU capacity into gaps too small for adaptive offload.
                    if (balanced and engine == "alu"
                            and len(candidates) <= ALU_VECTOR_BACKLOG
                            and len(bundles) >= ALU_VECTOR_RESERVE_START):
                        offload = next((i for i in ready["valu"]
                                        if ops[i]["slot"][0] not in ("vbroadcast", "multiply_add")), None)
                        if offload is not None:
                            ready["valu"].remove(offload)
                            bundle["alu_vector"] = [ops[offload]["slot"]]
                            chosen.append(offload)
                            capacity -= VLEN
                    selected = candidates[:capacity]
                    del candidates[: len(selected)]
                    if selected:
                        bundle[engine] = [ops[op_id]["slot"] for op_id in selected]
                        chosen.extend(selected)

                # Binary vectors can use scalar ALU lanes. Original policies
                # issue all eight together; fragments may use smaller gaps.
                if fragmented:
                    # Use any leftover scalar slots, including gaps smaller
                    # than a vector. A started vector leaves the ready queue;
                    # none of its consumers unlock until all lanes have issued.
                    capacity = (SLOT_LIMITS["alu"] - len(bundle.get("alu", ()))
                                - VLEN * len(bundle.get("alu_vector", ())))
                    while capacity:
                        if partial is not None:
                            op_id, lane = partial
                            partial = None
                        else:
                            op_id = next((i for i in ready["valu"]
                                          if ops[i]["slot"][0] not in
                                          ("vbroadcast", "multiply_add")), None)
                            if op_id is None:
                                break
                            ready["valu"].remove(op_id)
                            lane = 0
                        end = min(VLEN, lane + capacity)
                        # Keep logical addresses until lifetime allocation.
                        bundle.setdefault(f"alu_fragment_{lane}_{end}", []).append(ops[op_id]["slot"])
                        capacity -= end - lane
                        if end == VLEN:
                            chosen.append(op_id)
                        else:
                            partial = (op_id, end)
                elif (adaptive and "alu_vector" not in bundle
                        and SLOT_LIMITS["alu"] - len(bundle.get("alu", ())) >= VLEN):
                    offload = next((i for i in ready["valu"]
                                    if ops[i]["slot"][0] not in ("vbroadcast", "multiply_add")), None)
                    if offload is not None:
                        ready["valu"].remove(offload)
                        # Keep the logical vector intact until register allocation.
                        bundle["alu_vector"] = [ops[offload]["slot"]]
                        chosen.append(offload)

                assert bundle, "Dependency cycle in scheduler"
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
        if getattr(self, "blocked_lookup", False):
            # Retain all old policies; compare the later drain phase on the
            # graph with less deep-node encoding and fewer address loads.
            policies += (f"adaptive_tail_hetero_360_220_140_140_{BLOCKED_TAIL_START}",)
        # Keep the existing policies as fallbacks; compare a bounded set of
        # reservation-aware tail policies on the changed operation graph.
        policies += tuple("balanced_" + p for p in policies if p.startswith("adaptive_tail_"))
        if getattr(self, "fragment_alu_enabled", False):
            # Compare only the existing tail priorities with this new issue
            # mechanism. All original whole-vector policies remain fallbacks.
            policies += tuple("fragment_" + p for p in policies
                              if p.startswith(("adaptive_tail_", "balanced_adaptive_tail_")))
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
        self.issue_first_cycles = {}
        self.lane_issue_cycles = {}
        for cycle, bundle in enumerate(best):
            for engine, slots in bundle.items():
                for slot in slots:
                    op_id = identities[id(slot)]
                    self.issue_first_cycles.setdefault(op_id, cycle)
                    if engine == "alu_vector" or engine.startswith("alu_fragment_"):
                        first, last = ((0, VLEN) if engine == "alu_vector"
                                       else map(int, engine.split("_")[-2:]))
                        lanes = self.lane_issue_cycles.setdefault(op_id, [None] * VLEN)
                        for lane in range(first, last):
                            assert lanes[lane] is None, "Lane issued twice"
                            lanes[lane] = cycle
        for op_id, lanes in self.lane_issue_cycles.items():
            assert all(cycle is not None for cycle in lanes), "Unissued vector lane"
        for op_id, op in enumerate(ops):
            assert all(self.issue_first_cycles[op_id] > self.issue_cycles[dep]
                       for dep in op["deps"]), "Consumer issued before producer completed"
        self.offloaded_ops = set(self.lane_issue_cycles)
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
        assert INPUT_ADDRESS_CHAIN_LENGTH >= 1
        assert NODE_PREENCODE_DEPTH in (0, 4, 5, 6, 7)
        assert PATH_REUSE_DEPTH in (0, 2, 3, 4)
        assert DIRECT_PATH_DEPTH in (0, 2, 3, 4) and DIRECT_PATH_DEPTH <= PATH_REUSE_DEPTH
        # Both index representations use retained-parity coefficient lookup.
        # Legacy address-threshold lookup experiments remain disabled.
        assert (LOOKUP_DEPTH == PAIR_LOOKUP_DEPTH == 3
                and DEPTH3_COEFF_SELECT and DEPTH4_BIT_SELECT), (
            "Index representations require the pair-coefficient low-bit lookup path"
        )
        chunk_count = batch_size // VLEN
        scored_shape = (forest_height, batch_size, rounds) == (10, 256, 16)
        blocked_lookup = (scored_shape and BLOCKED_LOOKUP
                          and DIRECT_GATHER_ADDRESSES and DIRECT_PATH_DEPTH >= 4)
        self.blocked_lookup = blocked_lookup
        self.blocked_setup_xor_fused = blocked_lookup and BLOCKED_FUSE_SETUP_XOR
        self.blocked_setup_deadlines = blocked_lookup and BLOCKED_SETUP_DEADLINES
        self.blocked_early_tail_select = blocked_lookup and BLOCKED_EARLY_TAIL_SELECT
        self.blocked_unused_weight_pruned = blocked_lookup and BLOCKED_DROP_UNUSED_WEIGHT
        self.blocked_encode_depth6 = blocked_lookup and BLOCKED_ENCODE_DEPTH6
        compact_deep = self.blocked_compact_deep = self.blocked_encode_depth6 and BLOCKED_COMPACT_DEEP
        self.compact_lane_tail = compact_deep and COMPACT_LANE_TAIL
        self.compact_parent_index_select = compact_deep and COMPACT_PARENT_INDEX_SELECT
        self.compact_deep_landing = compact_deep and COMPACT_DEEP_LANDING and BLOCKED_FUSE_PARENT_XOR
        self.compact_shallow_landing = compact_deep and COMPACT_SHALLOW_LANDING and BLOCKED_FUSE_PARENT_XOR
        self.compact_flow_exchange = compact_deep and COMPACT_FLOW_EXCHANGE
        self.compact_setup_deadline_cap = COMPACT_SETUP_DEADLINE_CAP if compact_deep else 4
        self.compact_depth3_gather_chunks = COMPACT_DEPTH3_GATHER_CHUNKS if self.compact_flow_exchange else 0
        self.compact_deep_select_delay = COMPACT_DEEP_SELECT_DELAY if self.compact_flow_exchange else 0
        self.compact_startup_groups = COMPACT_STARTUP_GROUPS if self.compact_flow_exchange else 0
        assert 0 <= COMPACT_DEPTH3_GATHER_CHUNKS <= chunk_count or not compact_deep
        assert 0 <= self.compact_startup_groups <= chunk_count
        assert self.compact_setup_deadline_cap >= 0 and COMPACT_DEEP_SELECT_DELAY >= 0
        assert BLOCKED_REVERSE_INPUT_CHAIN_LENGTH >= 0
        self.blocked_reverse_input_chain_length = BLOCKED_REVERSE_INPUT_CHAIN_LENGTH if blocked_lookup else 0
        assert BLOCKED_READ_BANKS in (1, 2, 4, 8)
        assert not blocked_lookup or 0 <= BLOCKED_FINAL_CACHE_CHUNKS <= chunk_count
        self.fragment_alu_enabled = scored_shape and ALU_FRAGMENT_ISSUE
        # Cache coverage is tuned for the scored shape; retain the generic path
        # elsewhere, where different overlap can require more live scratch.
        cache_depth4 = scored_shape and (DEPTH4_CACHE_CHUNKS > 0 or DEPTH4_FINAL_CACHE_CHUNKS > 0) and any(
            r < rounds and r % (forest_height + 1) == 4 for r in DEPTH4_CACHE_ROUNDS
        )
        if blocked_lookup:
            # Stride-3 records remove a full gather level. Spend that load
            # headroom on the final lookup, releasing flow and cache scratch.
            cache_depth4 = BLOCKED_FINAL_CACHE_CHUNKS > 0 and not compact_deep
        def depth4_cached(round_no, chunk_no):
            if blocked_lookup:
                # These groups advance first in the cohort scheduler. Their
                # final coefficient trees overlap the remaining deep gathers.
                return (not compact_deep and round_no == rounds - 1
                        and chunk_no >= chunk_count - BLOCKED_FINAL_CACHE_CHUNKS)
            if not cache_depth4 or round_no not in DEPTH4_CACHE_ROUNDS:
                return False
            if round_no == rounds - 1:
                # The final coefficient tree uses all retained path parities.
                return DIRECT_PATH_DEPTH >= 4 and chunk_no < DEPTH4_FINAL_CACHE_CHUNKS
            return chunk_no < DEPTH4_CACHE_CHUNKS

        # The scored contract requires final values, not final indices. Use
        # part of the otherwise-unused index array as runtime workspace; the
        # forest and input values are never overwritten by preprocessing.
        preencode = scored_shape and NODE_PREENCODE_DEPTH > 0 and not blocked_lookup
        encode_first = 4
        encode_last = NODE_PREENCODE_DEPTH
        encode_node_first = (1 << encode_first) - 1
        encode_node_end = (1 << (encode_last + 1)) - 1
        self.preencoded_node_count = encode_node_end - encode_node_first if preencode else 0
        self.workspace_node_indices = list(range(encode_node_first, encode_node_end)) if preencode else []
        self.workspace_layout = "contiguous_encoded" if preencode else "none"
        if preencode:
            assert self.preencoded_node_count <= batch_size
        positive_addresses = scored_shape and DIRECT_GATHER_ADDRESSES and DIRECT_PATH_DEPTH >= 4
        self.direct_gather_addresses = positive_addresses

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

        # The fallback holds S=5-A and updates S'=2*S+p. The scored direct
        # path constructs A at its first gather and then keeps actual addresses.
        if not positive_addresses:
            depth2_left_base = vector_const((-6) & 0xFFFFFFFF, "depth2_left_base")
            depth2_right_base = vector_const((-8) & 0xFFFFFFFF, "depth2_right_base")
        # Active lookup modes use scalar parity extraction. Keep the vector
        # alias for non-direct experimental modes, without broadcasting it on
        # the scored direct path.
        one = (vector_const(1, "one") if DIRECT_PATH_DEPTH < 3
               else self.scratch_const(1, "one"))
        two = vector_const(2, "two")
        forest_values_scalar = self.scratch_const(forest_values_p, "forest_values_scalar")
        address_five = self.scratch_const(5)
        address_step = self.scratch_const(VLEN)
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
                    if blocked_lookup and address < 22:
                        # The blocked path spends freed flow slots on pure
                        # selection at depths 2/3; keep encoded node words.
                        continue
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

        if blocked_lookup:
            readonly_zero = self.alloc_scratch("positive_zero")
            neg2 = (None if self.compact_parent_index_select
                    else vector_const(0xFFFFFFFE, "path_neg2"))
            negative_weights = {2: vector_const(0xFFFFFFFC, "block_neg4")}
            address_bias = vector_const(0xFFFFFFFB, "address_bias")
            address_bases = {}
            direct_vectors = ([neg2] if neg2 is not None else []) + [address_bias, negative_weights[2]]
        elif positive_addresses:
            # Keep A itself once gathers begin. Before then, accumulate path
            # bits directly into the first gather address. Derive constants
            # from shared scalars so this representation does not require a
            # separate constant load for every base, weight and transition.
            readonly_zero = self.alloc_scratch("positive_zero")
            negative_weights = {}
            neg2 = self.alloc_scratch("path_weight_1", VLEN)
            self.add("alu", ("-", neg2, readonly_zero, two))
            self.add("valu", ("vbroadcast", neg2, neg2))
            negative_weights[1] = neg2
            for shift in (2, 3):
                vector = self.alloc_scratch(f"path_weight_{shift}", VLEN)
                self.add("valu", ("+", vector, negative_weights[shift - 1], negative_weights[shift - 1]))
                negative_weights[shift] = vector
            address_bias = self.alloc_scratch("address_bias", VLEN)
            self.add("alu", ("-", address_bias, readonly_zero, address_five))
            self.add("valu", ("vbroadcast", address_bias, address_bias))
            delta = n_nodes - encode_node_first if preencode else 0
            right4 = vector_const(37 + delta, "address_right_4")
            left4 = self.alloc_scratch("address_left_4", VLEN)
            self.add("valu", ("+", left4, right4, negative_weights[3]))
            if preencode:
                thirty_two = self.alloc_scratch("positive_32", VLEN)
                self.add("alu", ("<<", thirty_two, address_step, two))
                self.add("valu", ("vbroadcast", thirty_two, thirty_two))
                copied_bias = self.alloc_scratch("copied_bias", VLEN)
                self.add("valu", ("-", copied_bias, thirty_two, right4))
                five_vector = vector_const(5, "positive_five")
                exiting_bias = self.alloc_scratch("exiting_bias", VLEN)
                self.add("valu", ("multiply_add", exiting_bias, copied_bias, two, five_vector))
            else:
                copied_bias = exiting_bias = address_bias
            left5 = self.alloc_scratch("address_left_5", VLEN)
            right5 = self.alloc_scratch("address_right_5", VLEN)
            # If only depth 4 was copied, depth 5 addresses must exit the
            # workspace immediately. Otherwise both bases share its offset.
            first_base_bias = exiting_bias if preencode and encode_last == 4 else copied_bias
            self.add("valu", ("multiply_add", left5, left4, two, first_base_bias))
            self.add("valu", ("multiply_add", right5, right4, two, first_base_bias))
            address_bases = {4: (left4, right4), 5: (left5, right5)}
            direct_vectors = [*negative_weights.values(), address_bias, right4, left4, left5, right5]
            if preencode:
                direct_vectors.extend((thirty_two, copied_bias, five_vector, exiting_bias))

        if self.compact_flow_exchange:
            # The first eight shallow-record padding words hold encoded D3
            # nodes: D3=4*A3+W-53. Selecting the complete p3 bias gives
            # B4=2*D3-W-2-4*p3, without a second address arithmetic step.
            exchange_left = vector_const(forest_values_p+n_nodes+15, "exchange_left")
            exchange_right = vector_const(forest_values_p+n_nodes+31, "exchange_right")
            exchange_exit = vector_const((-forest_values_p-n_nodes-2)&0xffffffff, "exchange_exit")
            exchange_exit_even = vector_const((-forest_values_p-n_nodes-6)&0xffffffff, "exchange_exit_even")
            direct_vectors.append(exchange_exit_even)
            exchange_neg4 = negative_weights[2]
            exchange_even = vector_const(0xfffffffa, "exchange_even")
            address_bases[3] = (exchange_left, exchange_right)
            direct_vectors.extend((exchange_left, exchange_right, exchange_exit, exchange_even))
        extra_encode_buffers = []
        if blocked_lookup:
            # Transpose the runtime depth-4/5 values into 16 four-word records:
            # Parent/children records optionally put the left child first so
            # overlapping vloads can land it directly into its final vector.
            # Padding doubles as the flow-exchange D3 table. A vload may read
            # the next record too, but only its first three words are consumed.
            block_base = forest_values_p + n_nodes
            block_input = [self.alloc_scratch(f"block_input_{i}", VLEN) for i in range(3)]
            block_output = self.alloc_scratch("block_output", VLEN)
            block_store_addr = self.alloc_scratch("block_store_addr")
            self.add("load", ("const", block_store_addr, block_base))
            for parent_start in (0, 8):
                addresses = (22 + parent_start, 38 + 2*parent_start, 46 + 2*parent_start)
                for dest, address in zip(block_input, addresses):
                    addr = self.scratch_const(address)
                    self.add("load", ("vload", dest, addr))
                    if not BLOCKED_FUSE_SETUP_XOR:
                        self.add("valu", ("^", dest, dest, final_xor_vec))
                for pair in range(4):
                    for member in range(2):
                        parent = pair*2 + member
                        children = [block_input[1 + (2*parent+j)//VLEN] + (2*parent+j)%VLEN
                                    for j in (0, 1)]
                        fields = ([children[0], block_input[0]+parent, children[1]]
                                  if self.compact_shallow_landing else [block_input[0]+parent, *children])
                        for j, scalar in enumerate(fields):
                            # Each source field is transposed exactly once.
                            # Encode during that existing scalar copy instead
                            # of first XORing all six input vectors separately.
                            opcode, operand = (("^", final_xor_const) if BLOCKED_FUSE_SETUP_XOR
                                               else ("+", readonly_zero))
                            self.add("alu", (opcode, block_output + 4*member+j, scalar, operand))
                        if self.compact_flow_exchange and parent_start == 0:
                            # Later stores retain the final two encoded words;
                            # only padding in the first eight records is read.
                            self.add("alu", ("+", block_output + 4*member+3,
                                             depth3_vectors[parent ^ 1], readonly_zero))
                    self.add("store", ("vstore", block_store_addr, block_output))
                    if parent_start != 8 or pair != 3:
                        self.add("alu", ("+", block_store_addr, block_store_addr, address_step))
            # Record address B=4*A4+block_base-88. The original depth-6 address is
            # A6=B+(73-block_base)-2*p4-p5, where encoded parity is inverted.
            # An optional contiguous depth-6 copy follows the 64 record words.
            # Its address offset is n_nodes+1; fold entry into this existing bias.
            extra_delta = n_nodes + 1 if self.blocked_encode_depth6 and not compact_deep else 0
            block_exit_bias = vector_const((73-2*block_base if compact_deep else 73-block_base+extra_delta) & 0xFFFFFFFF,
                                          "block_exit_bias")
            block_neg4 = negative_weights[2]
            # Every blocked group first gathers at depth 4 (or has no final
            # gather). Only depth-1/2 accumulation needs weights -16/-8;
            # depth 3 uses block_neg4. The old -32 broadcast has no consumer.
            for shift in ((1, 2) if BLOCKED_DROP_UNUSED_WEIGHT else (1, 2, 3)):
                negative_weights[shift] = vector_const((-4*(1 << shift)) & 0xFFFFFFFF,
                                                       f"block_weight_{shift}")
            if self.compact_flow_exchange:
                exchange_neg8 = negative_weights[1]
            address_bases[4] = (vector_const(block_base+28, "block_left"),
                                vector_const(block_base+60, "block_right"))
            direct_vectors.extend((block_exit_bias, *negative_weights.values(), *address_bases[4]))
            if self.compact_shallow_landing:
                # The last round gathers only the parent, now at field one.
                block_tail_bases = (vector_const(block_base+29, "shallow_tail_left"),
                                    vector_const(block_base+61, "shallow_tail_right"))
                direct_vectors.extend(block_tail_bases)
            self.preencoded_node_count = 64
            self.workspace_node_indices = []
            for parent in range(15, 31):
                padding = ((7+(parent-15)%8 if parent < 23 else 13+(parent-15)%2)
                           if self.compact_flow_exchange else None)
                fields = ((2*parent+1, parent, 2*parent+2, padding)
                          if self.compact_shallow_landing else (parent, 2*parent+1, 2*parent+2, padding))
                self.workspace_node_indices.extend(fields)
            self.workspace_layout = "parent_children_stride4"
            if compact_deep:
                self.preencoded_node_count += 192
                self.workspace_node_indices.extend(index for parent in range(63, 127)
                                                   for index in ((2*parent+1, parent, 2*parent+2) if self.compact_deep_landing
                                                                 else (parent, 2*parent+1, 2*parent+2)))
                self.workspace_layout = "stride4_depth45_stride3_depth67" + ("_left_first" if self.compact_deep_landing else "")
                # D6=3*A6+W-146; after depth4, D6=3*B4+73-2*W-6*p4-3*p5.
                # Since 3 is odd, 4/3 modulo 2**32 is 0xaaaaaaac. Thus
                # A8=(4/3)*D6-(4/3)*(W-146)-15-2*p6-p7, with no division.
                if self.compact_parent_index_select:
                    deep_entry_even = vector_const((73-2*block_base-6)&0xffffffff, "deep_entry_even")
                    direct_vectors.append(deep_entry_even)
                deep_three = vector_const(3, "deep_three")
                deep_neg6 = (None if self.compact_parent_index_select
                             else vector_const(0xfffffffa, "deep_neg6"))
                deep_neg3 = vector_const(0xfffffffd, "deep_neg3")
                deep_scale = vector_const(0xaaaaaaac, "deep_four_over_three")
                extra_exit_bias = vector_const((-0xaaaaaaac*(block_base-146)-15)&0xffffffff, "deep_exit_bias")
                if self.compact_parent_index_select:
                    deep_exit_even = vector_const((-0xaaaaaaac*(block_base-146)-17)&0xffffffff, "deep_exit_even")
                direct_vectors.extend([deep_three] + ([deep_neg6] if deep_neg6 is not None else []) +
                                      [deep_neg3, deep_scale, extra_exit_bias] +
                                      ([deep_exit_even] if self.compact_parent_index_select else []))
                # Reuse the original record transpose buffers after their last
                # reads. Eight parents and sixteen children make three vstores.
                deep_src = [self.alloc_scratch(f"deep_src_{i}") for i in range(3)]
                for pointer, address in zip(deep_src, (70, 134, 142)):
                    self.add("load", ("const", pointer, address))
                double_step = self.scratch_const(16)
                self.add("alu", ("+", block_store_addr, block_store_addr, address_step))
                for parent_start in range(0, 64, 8):
                    for buffer, pointer in zip(block_input, deep_src):
                        self.add("load", ("vload", buffer, pointer))
                    for vector_no in range(3):
                        for lane in range(VLEN):
                            parent, field = divmod(vector_no*VLEN+lane, 3)
                            if self.compact_deep_landing and field < 2:
                                field ^= 1
                            if field == 0:
                                scalar = block_input[0]+parent
                            else:
                                child = 2*parent+field-1
                                scalar = block_input[1+child//VLEN]+child%VLEN
                            self.add("alu", ("^", block_output+lane, scalar, final_xor_const))
                        self.add("store", ("vstore", block_store_addr, block_output))
                        if parent_start != 56 or vector_no != 2:
                            self.add("alu", ("+", block_store_addr, block_store_addr, address_step))
                    if parent_start != 56:
                        for i, pointer in enumerate(deep_src):
                            self.add("alu", ("+", pointer, pointer, address_step if i == 0 else double_step))
            elif self.blocked_encode_depth6:
                self.preencoded_node_count += 64
                self.workspace_node_indices.extend(range(63, 127))
                self.workspace_layout = "parent_children_stride4_plus_depth6"
                # A6 is copied A+delta, but A7 is back in the original tree:
                # A7=2*A6-5-2*delta-p. No per-gather address conversion is added.
                extra_exit_bias = vector_const((-5-2*extra_delta) & 0xFFFFFFFF, "extra_exit_bias")
                direct_vectors.append(extra_exit_bias)
                extra_encode_buffers = [self.alloc_scratch(f"extra_buffer_{i}", VLEN) for i in range(2)]
                extra_src = self.alloc_scratch("extra_src")
                extra_dst = self.alloc_scratch("extra_dst")
                self.add("load", ("const", extra_src, forest_values_p+63))
                self.add("load", ("const", extra_dst, block_base+64))
                for vector_no in range(64 // VLEN):
                    buffer = extra_encode_buffers[vector_no % len(extra_encode_buffers)]
                    self.add("load", ("vload", buffer, extra_src))
                    self.add("valu", ("^", buffer, buffer, final_xor_vec))
                    self.add("store", ("vstore", extra_dst, buffer))
                    if vector_no != 64 // VLEN - 1:
                        self.add("alu", ("+", extra_src, extra_src, address_step))
                        self.add("alu", ("+", extra_dst, extra_dst, address_step))

        if self.compact_shallow_landing:
            self.workspace_layout += "_shallow_left_first"
        if self.compact_flow_exchange:
            self.workspace_layout += "_padding_depth3"

        # Fold initialization into the same DAG as the kernel.  Main operations
        # gain dependencies on the setup instructions that produce their inputs.
        setup_ops, setup_writers = self.pack_setup(
            extract=True, setup_chunk=SETUP_CHUNK
        )
        encode_buffers = [self.alloc_scratch(f"encode_buffer_{i}", VLEN) for i in range(2)] if preencode else []
        encode_src = self.alloc_scratch("encode_src") if preencode else None
        encode_dst = self.alloc_scratch("encode_dst") if preencode else None
        encoded_address_five = self.alloc_scratch("encoded_address_five") if preencode else None
        idx = self.alloc_scratch("idx", batch_size)
        val = self.alloc_scratch("val", batch_size)
        input_addrs = self.alloc_scratch("input_addrs", chunk_count)
        # Node/hash and retained parity scratch is virtual; physical storage
        # follows scheduled lifetimes, including cross-round parity consumers.
        node_or_addr = self.scratch_ptr
        tmp1 = node_or_addr + chunk_count * NODE_VIRTUAL_VECTORS * VLEN
        tmp2 = tmp1 + batch_size
        path_bits_base = tmp2 + batch_size

        block_stores = [i for i, op in enumerate(setup_ops) if op["engine"] == "store"] if blocked_lookup else []
        extra_level_stores = []
        if self.blocked_encode_depth6:
            # Dynamic gathers require every store for THEIR level, not all
            # preprocessing. Depth-4 records must not wait for depth-6 copying.
            extra_level_stores, block_stores = block_stores[8:], block_stores[:8]
            assert len(extra_level_stores) == (24 if compact_deep else 8)
        block_children_base = path_bits_base + rounds * batch_size
        ops = setup_ops
        setup_count = len(setup_ops)
        setup_deadline_end = setup_count
        shared_select_uses = []
        node_pool_uses = []
        emit_context = {"chunk": -1, "round": -1, "local_seq": 0, "is_setup": False}

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

        encoded_levels_ready = {}
        if preencode:
            emit_context.update(chunk=SETUP_CHUNK, round=-1, local_seq=0, is_setup=True)
            source_ready = emit("load", ("const", encode_src, forest_values_p + encode_node_first))
            dest_ready = emit("load", ("const", encode_dst, forest_values_p + n_nodes))
            encoded_address_ready = emit("load", ("const", encoded_address_five, 5 + n_nodes - encode_node_first))
            buffer_ready = [None] * len(encode_buffers)
            vector_no = 0
            for level in range(encode_first, encode_last + 1):
                level_stores = []
                for _ in range((1 << level) // VLEN):
                    bank = vector_no % len(encode_buffers)
                    tmp = encode_buffers[bank]
                    read = emit("load", ("vload", tmp, encode_src), source_ready, buffer_ready[bank])
                    encoded = emit("valu", ("^", tmp, tmp, final_xor_vec), read)
                    written = emit("store", ("vstore", encode_dst, tmp), dest_ready, encoded)
                    level_stores.append(written)
                    buffer_ready[bank] = written
                    vector_no += 1
                    if vector_no * VLEN < self.preencoded_node_count:
                        source_ready = emit("alu", ("+", encode_src, encode_src, address_step), read)
                        dest_ready = emit("alu", ("+", encode_dst, encode_dst, address_step), written)
                encoded_levels_ready[level] = level_stores
            setup_deadline_end = len(ops)
            emit_context.update(chunk=-1, round=-1, local_seq=0, is_setup=False)

        # Keep these scalar addresses live through output stores, avoiding
        # a separate flow add_imm for each chunk at the end of execution.
        # Forward pairs retain the old path. The blocked variant anchors each
        # short chain at its highest group, matching descending cohort priority;
        # four-address chains replace eight more constant loads with ALU work.
        reverse_inputs = self.blocked_reverse_input_chain_length > 0
        chain_length = self.blocked_reverse_input_chain_length or INPUT_ADDRESS_CHAIN_LENGTH
        input_addr_ready = [None] * chunk_count
        input_order = range(chunk_count-1, -1, -1) if reverse_inputs else range(chunk_count)
        for chunk_no in input_order:
            if scored_shape and INPUT_ADDRESS_CONSUMER_PRIORITY:
                # This address feeds the group's root-round input load. Do
                # not strand every anchor behind setup as a dummy cohort -1.
                emit_context.update(chunk=chunk_no, round=0, local_seq=0)
            anchor = ((chunk_no == chunk_count-1 or (chunk_no+1) % chain_length == 0)
                      if reverse_inputs else chunk_no % chain_length == 0)
            if anchor:
                ready = emit("load", ("const", input_addrs + chunk_no,
                                      inp_values_p + chunk_no * VLEN))
            else:
                neighbor = chunk_no + 1 if reverse_inputs else chunk_no - 1
                ready = emit("alu", ("-" if reverse_inputs else "+", input_addrs + chunk_no,
                                      input_addrs + neighbor, address_step),
                             input_addr_ready[neighbor])
            input_addr_ready[chunk_no] = ready

        for chunk_no in range(chunk_count):
            emit_context.update(chunk=chunk_no, round=0 if reverse_inputs else -1, local_seq=0)
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
            block_left = block_children_base + 2*offset
            block_right = block_left + VLEN

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
                # Exchange only the first traversal's highest groups. Their
                # extra D3 gathers free selects for all deep address updates.
                exchange = (self.compact_flow_exchange and round_no <= 3
                            and chunk_no >= chunk_count-self.compact_depth3_gather_chunks)
                if depth == 0:
                    path_bits = {}
                    if positive_addresses:
                        first_gather_depth = 3 if exchange else (5 if depth4_cached(round_no + 4, chunk_no) else 4)
                        if first_gather_depth > min(forest_height, rounds - 1 - round_no):
                            first_gather_depth = None
                    reuse_depth = min(PATH_REUSE_DEPTH, forest_height, rounds - 1 - round_no)
                    if reuse_depth >= 4 and not (
                        depth4_cached(round_no + 4, chunk_no)
                    ):
                        reuse_depth = 3
                    retained_depth = max(reuse_depth - 1, min(DIRECT_PATH_DEPTH, reuse_depth))
                    # A traversal ending entirely in direct cached lookup
                    # needs path bits, but no materialized index or address.
                    needs_index = any(
                        (d >= 2 and d > DIRECT_PATH_DEPTH) or
                        (d > LOOKUP_DEPTH and not (d == 4 and depth4_cached(round_no + d, chunk_no)))
                        for d in range(1, min(forest_height, rounds - 1 - round_no) + 1)
                    )
                cached_lookup = (0 < depth <= LOOKUP_DEPTH) or (
                    depth == 4 and depth4_cached(round_no, chunk_no)
                )
                if exchange and depth == 3:
                    cached_lookup = False
                direct_lookup = cached_lookup and 2 <= depth <= DIRECT_PATH_DEPTH
                interpolation, interpolation_ready = (
                    path_bits[depth - 1] if direct_lookup else (chunk_idx, idx_ready)
                )
                lookup_barrier = None if direct_lookup else idx_ready
                # A gathered node dies at the input XOR, before hash tmp2 use.
                chunk_node = (node_or_addr + chunk_no * NODE_VIRTUAL_VECTORS * VLEN
                              if cached_lookup else chunk_tmp2)
                lookup_start = len(ops)
                if blocked_lookup and depth == 4 and round_no != rounds - 1:
                    chunk_node = chunk_tmp1
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
                    if needs_index and depth < forest_height and round_no < rounds - 1:
                        # Prepare the first gather's base, or the fallback's
                        # S2 base (p0 ? -6 : -8), while this round hashes.
                        left_base, right_base = (address_bases[first_gather_depth] if positive_addresses
                                                 else (depth2_left_base, depth2_right_base))
                        if self.compact_shallow_landing and round_no > forest_height:
                            left_base, right_base = block_tail_bases
                        if exchange:
                            left_base, right_base = exchange_left, exchange_right
                        idx_ready = select(chunk_idx, root_parity, left_base, right_base, selected)
                elif blocked_lookup and depth == 2:
                    # Consume the older path bit first. Only the last select
                    # waits for the newest bit; no interpolation MAC is needed.
                    low, high = path_bits[1], path_bits[0]
                    left = select(chunk_node, high[0], depth2_vectors[1], depth2_vectors[3], high[1])
                    right = select(chunk_tmp2, high[0], depth2_vectors[0], depth2_vectors[2], high[1])
                    selected = select(chunk_node, low[0], chunk_node, chunk_tmp2, left, right, low[1])
                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected)
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
                elif exchange and depth == 3:
                    node_loads = [emit("load", ("load_offset", chunk_node, chunk_idx, lane),
                                       idx_ready, block_stores) for lane in range(VLEN)]
                    node_address_reads = node_loads
                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, node_loads)
                elif blocked_lookup and depth == 3:
                    low, middle, high = path_bits[2], path_bits[1], path_bits[0]
                    quartet = []
                    for pair in range(4):
                        dest = chunk_node + pair*VLEN
                        quartet.append(select(dest, high[0], depth3_vectors[pair^1], depth3_vectors[(pair+4)^1], high[1]))
                    left = select(chunk_node, middle[0], chunk_node, chunk_node+2*VLEN, quartet[::2], middle[1])
                    right = select(chunk_node+VLEN, middle[0], chunk_node+VLEN, chunk_node+3*VLEN, quartet[1::2], middle[1])
                    selected = select(chunk_node, low[0], chunk_node, chunk_node+VLEN, left, right, low[1])
                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected)
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
                elif (depth == 4 and depth4_cached(round_no, chunk_no)
                      and round_no == rounds - 1):
                    # Select coefficients before the newest parity arrives;
                    # unlike quartet interpolation, only one MAC waits for it.
                    pairs = sorted(range(8), key=lambda p: ((5 - 22 - 2*p) >> 1) & 7)
                    if self.blocked_early_tail_select:
                        # The same 14 selects + one MAC, but build quartets
                        # using p0 then p1 before p2 arrives. Reverse the
                        # three-bit table coordinate to preserve the lookup.
                        masks = [path_bits[d][0] for d in (0, 1, 2)]
                        mask_ready = [path_bits[d][1] for d in (0, 1, 2)]
                        order = [pairs[((i & 1) << 2) | (i & 2) | (i >> 2)] for i in range(8)]
                        dlow, dhigh, elow, ehigh = [chunk_node + i * VLEN for i in range(4)]

                        def coefficient_quartet(values, destination, *deps):
                            left = select(chunk_tmp1, masks[0], values[1], values[0], mask_ready[0], *deps)
                            right = select(chunk_tmp2, masks[0], values[3], values[2], mask_ready[0], *deps)
                            return select(destination, masks[1], chunk_tmp2, chunk_tmp1, left, right, mask_ready[1])

                        dl = coefficient_quartet([depth4_vectors[2*p+1] for p in order[:4]], dlow)
                        dh = coefficient_quartet([depth4_vectors[2*p+1] for p in order[4:]], dhigh, dl)
                        el = coefficient_quartet([depth4_vectors[2*p] for p in order[:4]], elow, dh)
                        eh = coefficient_quartet([depth4_vectors[2*p] for p in order[4:]], ehigh, el)
                        slope = select(dlow, masks[2], dhigh, dlow, dl, dh, mask_ready[2])
                        intercept = select(elow, masks[2], ehigh, elow, el, eh, mask_ready[2])
                        selected = emit("valu", ("multiply_add", chunk_node, interpolation, dlow, elow),
                                        slope, intercept, interpolation_ready)
                    else:
                        intercept_result = chunk_node + VLEN
                        masks = [path_bits[2][0], path_bits[1][0], path_bits[0][0]]
                        mask_ready = [path_bits[2][1], path_bits[1][1], path_bits[0][1]]

                        def coefficient_tree(values, destination, *deps):
                            left = select(chunk_tmp1, masks[0], values[1], values[0], mask_ready[0], *deps)
                            right = select(chunk_tmp2, masks[0], values[3], values[2], mask_ready[0], *deps)
                            lower = select(destination, masks[1], chunk_tmp2, chunk_tmp1, left, right, mask_ready[1])
                            left = select(chunk_tmp1, masks[0], values[5], values[4], lower)
                            right = select(chunk_tmp2, masks[0], values[7], values[6], lower)
                            upper = select(chunk_tmp1, masks[1], chunk_tmp2, chunk_tmp1, left, right, mask_ready[1])
                            return select(destination, masks[2], chunk_tmp1, destination, lower, upper, mask_ready[2])

                        slope = coefficient_tree([depth4_vectors[2*p+1] for p in pairs], chunk_node)
                        intercept = coefficient_tree([depth4_vectors[2*p] for p in pairs], intercept_result, slope)
                        selected = emit("valu", ("multiply_add", chunk_node, interpolation, chunk_node, intercept_result),
                                        slope, intercept, interpolation_ready)
                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected)
                elif (depth == 4 and depth4_cached(round_no, chunk_no) and DEPTH4_BIT_SELECT):
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
                elif (depth == 4 and depth4_cached(round_no, chunk_no)):
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
                elif ((self.compact_shallow_landing and depth == 4 and round_no != rounds - 1)
                      or (self.compact_deep_landing and depth == 6)):
                    # Overlapping reads preserve earlier left-child lanes.
                    # Only the parent/right fields are consumed before the
                    # next load overwrites them. Keep strict WAR dependencies.
                    block_use_start = len(ops)
                    record_base = block_children_base
                    record_stores = block_stores
                    if depth == 6:
                        record_base += 2*batch_size + chunk_count*BLOCKED_READ_BANKS*VLEN
                        record_stores = extra_level_stores
                    block_left = record_base + 3*offset
                    block_right = block_left + 2*VLEN
                    pending = None
                    node_loads = []
                    block_children_ready = []
                    node_address_reads = []
                    for lane in range(VLEN):
                        read_buffer = block_left+lane
                        loaded = emit("load", ("vload", read_buffer, chunk_idx+lane),
                                      idx_ready[lane] if isinstance(idx_ready, list) else idx_ready,
                                      record_stores, pending)
                        node_address_reads.append(loaded)
                        parent = emit("alu", ("^", chunk_val+lane, chunk_val+lane, read_buffer+1), loaded, val_ready)
                        child = emit("alu", ("+", block_right+lane, read_buffer+2, readonly_zero), loaded)
                        pending = [parent, child]
                        node_loads.append(parent)
                        block_children_ready.extend((loaded, child))
                    val_ready = [*node_loads, *pending]
                elif blocked_lookup and (depth == 4 or (compact_deep and depth == 6)) and round_no != rounds - 1:
                    block_use_start = len(ops)
                    record_base = block_children_base
                    record_stores = block_stores
                    if depth == 6:
                        record_base += 2*batch_size + chunk_count*BLOCKED_READ_BANKS*VLEN
                        record_stores = extra_level_stores
                    block_left = record_base + 2*offset
                    block_right = block_left + VLEN
                    pending = [None] * BLOCKED_READ_BANKS
                    node_loads = []
                    block_children_ready = []
                    node_address_reads = []
                    for lane in range(VLEN):
                        bank = lane % BLOCKED_READ_BANKS
                        read_buffer = record_base + 2*batch_size + (chunk_no*BLOCKED_READ_BANKS+bank)*VLEN
                        loaded = emit("load", ("vload", read_buffer, chunk_idx+lane),
                                      idx_ready[lane] if isinstance(idx_ready, list) else idx_ready,
                                      record_stores, pending[bank])
                        node_address_reads.append(loaded)
                        if BLOCKED_FUSE_PARENT_XOR:
                            # Consume the parent directly from the read buffer.
                            # This replaces its copy AND the later vector XOR
                            # with one scalar XOR per lane, saving one vector
                            # operation per group without moving child data.
                            copied = [emit("alu", ("^", chunk_val+lane, chunk_val+lane, read_buffer),
                                           loaded, val_ready)]
                            copied.extend(emit("alu", ("+", dest+lane, read_buffer+j, readonly_zero), loaded)
                                          for j, dest in enumerate((block_left, block_right), 1))
                        else:
                            copied = [emit("alu", ("+", dest+lane, read_buffer+j, readonly_zero), loaded)
                                      for j, dest in enumerate((chunk_node, block_left, block_right))]
                        node_loads.append(copied[0])
                        block_children_ready.extend(copied[1:])
                        pending[bank] = copied
                    all_copies = [dep for group in pending if group for dep in group]
                    val_ready = ([*node_loads, *all_copies] if BLOCKED_FUSE_PARENT_XOR else
                                 emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, node_loads, all_copies))
                    node_pool_uses.extend((record_base+2*batch_size+(chunk_no*BLOCKED_READ_BANKS+bank)*VLEN,
                                           block_use_start, len(ops), 1) for bank in range(BLOCKED_READ_BANKS))
                elif blocked_lookup and (depth == 5 or (compact_deep and depth == 7)):
                    selected = select(chunk_node, block_parity, block_left, block_right,
                                      block_parity_ready, block_children_ready)
                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected)
                    if ((self.compact_shallow_landing and depth == 5)
                            or (self.compact_deep_landing and depth == 7)):
                        # Negative width denotes one indivisible contiguous
                        # span, not two independently colored vectors.
                        node_pool_uses.append((block_left, block_use_start, len(ops), -2))
                        node_pool_uses.append((block_right, block_use_start, len(ops), 1))
                    else:
                        node_pool_uses.extend((dest, block_use_start, len(ops), 1) for dest in (block_left, block_right))
                else:
                    encoded_node = ((blocked_lookup and (depth == 4 or (self.blocked_encode_depth6 and depth == 6)))
                                    or (preencode and encode_first <= depth <= encode_last))
                    address_constant = encoded_address_five if preencode and encoded_node else address_five
                    # Rebase addresses without extra per-lane arithmetic.
                    # Dynamic gathers may read any node in their level, so
                    # every copy-store for that level must have committed.
                    extra_ready = ((block_stores if depth == 4 else extra_level_stores) if blocked_lookup and encoded_node else
                                   [encoded_address_ready, *encoded_levels_ready[depth]] if encoded_node else [])
                    if positive_addresses:
                        node_loads = [
                            emit("load", ("load_offset", chunk_node, chunk_idx, lane),
                                 idx_ready[lane] if isinstance(idx_ready, list) else idx_ready,
                                 extra_ready)
                            for lane in range(VLEN)
                        ]
                    else:
                        # Decode S only at gathered levels on the fallback.
                        address_ready = [
                            emit("alu", ("-", chunk_node + lane, address_constant, chunk_idx + lane), idx_ready, extra_ready)
                            for lane in range(VLEN)
                        ]
                        node_loads = [
                            emit("load", ("load_offset", chunk_node, chunk_node, lane), address_ready[lane])
                            for lane in range(VLEN)
                        ]
                    node_address_reads = list(node_loads)
                    if not encoded_node:
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
                if exchange and depth == 3:
                    pass  # Choose the complete address bias after p3 arrives.
                elif blocked_lookup and depth == 4 and round_no != rounds - 1:
                    if not self.compact_parent_index_select:
                        slot = (("multiply_add", chunk_idx, chunk_idx, deep_three, block_exit_bias) if compact_deep
                                else ("+", chunk_idx, chunk_idx, block_exit_bias))
                        index_base_ready = emit("valu", slot, node_address_reads)
                elif (positive_addresses and first_gather_depth is not None
                        and not (blocked_lookup and (depth == 5 or (compact_deep and depth == 7)
                                                      or (self.compact_parent_index_select and depth == 6)))
                        and not (self.compact_flow_exchange and depth in (8, 9))
                        and depth >= first_gather_depth and round_no < rounds - 1
                        and depth < forest_height):
                    # A' = 2*A - 5 - p. Prepare the affine base while hashing,
                    # but do not overwrite A until every gather has read it.
                    # For copied A+d, use -5-d within the copy and -5-2*d
                    # when returning to the original forest address space.
                    bias = ((exiting_bias if depth == encode_last else copied_bias)
                            if preencode and encode_first <= depth <= encode_last else address_bias)
                    if self.blocked_encode_depth6 and depth == 6:
                        bias = extra_exit_bias
                    index_base_ready = emit(
                        "valu", ("multiply_add", chunk_idx, chunk_idx, deep_scale if compact_deep and depth == 6 else two, bias), node_address_reads
                    )
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
                            if self.compact_lane_tail and depth in (5, 6, 7, 8, 9):
                                # A finished lane can expose its path bit and
                                # next gather without waiting for the other 7.
                                val_ready = [emit("alu", ("^", chunk_val+lane, chunk_val+lane, chunk_tmp2+lane),
                                                  val_ready, shifted) for lane in range(VLEN)]
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
                # Keep p at the root; later use S'=2*S+p on the fallback or
                # subtract p from the direct path's precomputed address base.
                parity_dest = chunk_idx if depth == 0 else chunk_tmp1
                if depth < retained_depth or (blocked_lookup and (depth == 4 or (compact_deep and depth == 6))):
                    # Unique logical producer per group/round; its storage is
                    # colored over all consumers without inserting copies.
                    parity_dest = path_bits_base + (round_no * chunk_count + chunk_no) * VLEN
                    path_bit_uses.append((parity_dest, len(ops)))
                if self.compact_lane_tail and depth in (5, 6, 7, 8, 9):
                    parity = [emit("alu", ("&", parity_dest+lane, chunk_val+lane, one),
                                   val_ready[lane]) for lane in range(VLEN)]
                else:
                    parity = emit_scalar_rhs("&", parity_dest, chunk_val, one, val_ready)
                if depth < retained_depth or (blocked_lookup and (depth == 4 or (compact_deep and depth == 6))):
                    path_bits[depth] = (parity_dest, parity)
                if depth == 0:
                    idx_ready = parity
                    continue
                if not needs_index:
                    idx_ready = None
                    continue
                if positive_addresses:
                    if exchange and depth == 3:
                        chosen_bias = select(chunk_tmp1, parity_dest, exchange_exit_even, exchange_exit, parity)
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, two, chunk_tmp1),
                                         idx_ready, chosen_bias, node_address_reads)
                        continue
                    if exchange and depth in (1, 2):
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, parity_dest,
                                         exchange_neg8 if depth == 1 else exchange_neg4, chunk_idx), parity, idx_ready)
                        continue
                    if self.compact_flow_exchange and depth in (8, 9):
                        # The select and following MAC replace early MAC plus
                        # eight parity subtracts. Bias selection is lower
                        # scheduling priority; all true dependencies remain.
                        emit_context["round"] = round_no+self.compact_deep_select_delay
                        chosen_bias = select(chunk_tmp1, parity_dest, exchange_even, address_bias, parity)
                        ops[chosen_bias]["semantic_round"] = round_no
                        emit_context["round"] = round_no
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, two, chunk_tmp1),
                                         idx_ready, chosen_bias, node_address_reads)
                        continue
                    if self.compact_parent_index_select and depth in (4, 6):
                        # The next round already has its child value. Spend a
                        # flow select here, hiding address work behind that hash
                        # instead of adding it immediately before a deep gather.
                        block_parity, block_parity_ready = parity_dest, parity
                        even_bias, odd_bias, scale = ((deep_entry_even, block_exit_bias, deep_three) if depth == 4
                                                     else (deep_exit_even, extra_exit_bias, deep_scale))
                        chosen_bias = select(chunk_tmp1, parity_dest, even_bias, odd_bias, parity)
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, scale, chunk_tmp1), idx_ready, chosen_bias)
                        continue
                    if blocked_lookup and depth == 3:
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, parity_dest, block_neg4, chunk_idx), parity, idx_ready)
                        continue
                    if blocked_lookup and depth == 4:
                        block_parity, block_parity_ready = parity_dest, parity
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, parity_dest, deep_neg6 if compact_deep else neg2, chunk_idx), parity, index_base_ready)
                        continue
                    if blocked_lookup and depth == 5:
                        if compact_deep:
                            idx_ready = emit("valu", ("multiply_add", chunk_idx, parity_dest, deep_neg3, chunk_idx), parity, idx_ready)
                            continue
                        index_base_ready = idx_ready
                    if compact_deep and depth == 6:
                        block_parity, block_parity_ready = parity_dest, parity
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, parity_dest, neg2, chunk_idx), parity, index_base_ready)
                        continue
                    if compact_deep and depth == 7:
                        index_base_ready = idx_ready
                    if first_gather_depth is None:
                        idx_ready = None
                    elif depth < first_gather_depth - 1:
                        idx_ready = emit(
                            "valu", ("multiply_add", chunk_idx, parity_dest,
                                     negative_weights[first_gather_depth - 1 - depth], chunk_idx),
                            parity, idx_ready,
                        )
                    else:
                        base_ready = idx_ready if depth == first_gather_depth - 1 else index_base_ready
                        idx_ready = [
                            emit("alu", ("-", chunk_idx + lane, chunk_idx + lane, parity_dest + lane),
                                 parity[lane], base_ready)
                            for lane in range(VLEN)
                        ]
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
        self.prune_unused_constants(ops, node_pool_uses)
        if preencode or self.blocked_setup_deadlines:
            # DCE removes only constants emitted before the copy sequence.
            setup_deadline_end -= self.pruned_constant_loads
            self.prioritize_setup_by_first_use(ops, setup_deadline_end)
        self.schedule(ops)
        # Reclaim index storage only after its actual last scheduled access.
        # Final-cache groups have no index uses in their second traversal.
        # Direct-address constants can also be reused after their last access.
        self.allocate_node_lifetimes(ops, node_pool_uses,
                                     tuple(depth4_vectors if cache_depth4 else ()) +
                                     tuple(direct_vectors if positive_addresses else ()) +
                                     tuple([*block_input, *extra_encode_buffers, block_output] if blocked_lookup else ()) + tuple(encode_buffers) +
                                     tuple(range(idx, idx + batch_size, VLEN)))
        for bundle in self.instrs:
            for engine in list(bundle):
                if engine != "alu_vector" and not engine.startswith("alu_fragment_"):
                    continue
                first, last = ((0, VLEN) if engine == "alu_vector"
                               else map(int, engine.split("_")[-2:]))
                for op, dest, left, right in bundle.pop(engine):
                    bundle.setdefault("alu", []).extend(
                        (op, dest + lane, left + lane, right + lane)
                        for lane in range(first, last)
                    )
            assert all(len(slots) <= SLOT_LIMITS[engine] for engine, slots in bundle.items())

    def prioritize_setup_by_first_use(self, ops, setup_end):
        """Delay setup priority to its first consuming round, without new deps.

        This only changes scheduling metadata. Memory-copy dependencies stay
        intact. An explicit prefix boundary separates setup from body ops;
        is_setup remains immutable for analysis. The compact records can wait
        until round 6; the legacy path retains its earlier round-4 cap.
        """
        needed = [len(ops)] * len(ops)
        for i, op in enumerate(ops):
            if i >= setup_end and op["round"] >= 0:
                needed[i] = op["round"]
        for i in range(len(ops) - 1, -1, -1):
            for dep in ops[i]["deps"]:
                if dep is not None:
                    assert dep < i, "Setup deadlines require a forward DAG"
                    needed[dep] = min(needed[dep], needed[i])
        for i, op in enumerate(ops):
            if i < setup_end:
                assert op["is_setup"]
                op["round"] = min(self.compact_setup_deadline_cap, needed[i])

    def prune_unused_constants(self, ops, uses):
        """Remove unread, dependency-free constants after the full DAG exists.

        Lookup variants leave obsolete setup thresholds behind. This pass is
        conservative: it does not remove memory reads, stores, or any constant
        whose scratch word is read anywhere, even after a later overwrite.
        """
        reads = set()
        for op in ops:
            reads.update(self.instruction_accesses(op["engine"], op["slot"])[0])
        dead = {
            i for i, op in enumerate(ops)
            if op["engine"] == "load" and op["slot"][0] == "const"
            and op["slot"][1] not in reads
            and not any(d is not None for d in op["deps"])
        }
        self.pruned_constant_loads = len(dead)
        if not dead:
            return
        # Map operation IDs and half-open lifetime boundaries together.
        prefix = [0]
        for i in range(len(ops)):
            prefix.append(prefix[-1] + (i not in dead))
        kept = [op for i, op in enumerate(ops) if i not in dead]
        for op in kept:
            op["deps"] = [prefix[d] for d in op["deps"] if d is not None and d not in dead]
        uses[:] = [(base, prefix[start], prefix[end], width) for base, start, end, width in uses]
        ops[:] = kept

    def allocate_node_lifetimes(self, ops, uses, reusable=()):
        """Color node vectors after scheduling, preserving every issue cycle."""
        times = {id(slot): cycle for cycle, bundle in enumerate(self.instrs)
                 for slots in bundle.values() for slot in slots}
        first_times = {}
        for cycle, bundle in enumerate(self.instrs):
            for slots in bundle.values():
                for slot in slots:
                    first_times.setdefault(id(slot), cycle)
        intervals = []
        for base, start, end, width in uses:
            spans = [(base, -width)] if width < 0 else [(base+n*VLEN, 1) for n in range(width)]
            for address, span in spans:
                addresses = set(range(address, address + span*VLEN))
                touched = []
                for op in ops[start:end]:
                    reads, writes = self.instruction_accesses(op["engine"], op["slot"])
                    if addresses & (reads | writes):
                        touched.append(op)
                if touched:
                    # A fragmented vector may first touch its registers well
                    # before its final lane completes. Protect that full span.
                    last = max(times[id(o["slot"])] for o in touched)
                    ends_with_write = any(times[id(o["slot"])] == last
                                          and addresses & self.instruction_accesses(o["engine"], o["slot"])[1]
                                          for o in touched)
                    intervals.append((min(first_times[id(o["slot"])] for o in touched),
                                      last, address, start, end, ends_with_write, span))
        # A cache coefficient's physical vector can host nodes after its last
        # scheduled use. All original reads finish before any reused write.
        last_use = {address: -1 for address in reusable}
        last_write = {address: -1 for address in reusable}
        for op in ops:
            reads, writes = self.instruction_accesses(op["engine"], op["slot"])
            for address in reusable:
                if any(address <= a < address + VLEN for a in reads | writes):
                    last_use[address] = max(last_use[address], times[id(op["slot"])])
                if any(address <= a < address + VLEN for a in writes):
                    last_write[address] = max(last_write[address], times[id(op["slot"])])
        physical_slots = list(reusable)
        slots_end = [last_use[address] for address in reusable]
        read_only_end = [last_write[address] < last_use[address] for address in reusable]
        replacements = {}
        for first, last, address, start, end, ends_with_write, span in sorted(intervals):
            # An old READ may share a cycle with the next value's first WRITE.
            # Two end-of-cycle writes must never share physical scratch, even
            # if the earlier value is dead: the simulator would resolve their
            # collision by engine/slot order, not by these logical lifetimes.
            physical_index = {base: i for i, base in enumerate(physical_slots)}
            colors = None
            for base in physical_slots:
                indices = [physical_index.get(base+n*VLEN) for n in range(span)]
                if all(i is not None and (slots_end[i] < first or (slots_end[i] == first and read_only_end[i]))
                       for i in indices):
                    colors = indices
                    break
            if colors is None:
                colors = []
                for _ in range(span):
                    colors.append(len(slots_end))
                    physical_slots.append(self.scratch_ptr+(len(slots_end)-len(reusable))*VLEN)
                    slots_end.append(last)
                    read_only_end.append(not ends_with_write)
            for color in colors:
                slots_end[color] = last
                read_only_end[color] = not ends_with_write
            physical = physical_slots[colors[0]]
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
                    if address <= original[pos] < address + span*VLEN:
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
