"""Report emitted DAG readiness and issue utilization; not part of execution."""

from collections import Counter, defaultdict
import json
from math import ceil

from perf_takehome import KernelBuilder
from problem import SLOT_LIMITS


class AnalyzedKernel(KernelBuilder):
    def schedule(self, ops):
        self.operations = ops
        super().schedule(ops)

    def allocate_node_lifetimes(self, ops, uses, reusable=()):
        self.logical_slots = [op["slot"] for op in ops]
        super().allocate_node_lifetimes(ops, uses, reusable)


def analyze(builder):
    operations = builder.operations
    issued = builder.issue_cycles
    first_issued = builder.issue_first_cycles
    assert len(issued) == len(operations)
    by_round = defaultdict(lambda: defaultdict(list))
    waits = defaultdict(list)
    engine_times = defaultdict(list)
    counts = Counter()
    gathers = []
    lookup_loads = []
    for i, op in enumerate(operations):
        ready = max((issued[d] + 1 for d in op["deps"]), default=0)
        assert first_issued[i] >= ready, (i, ready, first_issued[i])
        engine = "alu" if i in builder.offloaded_ops else op["engine"]
        lane_times = builder.lane_issue_cycles.get(i, [issued[i]])
        counts[engine] += len(lane_times)
        engine_times[engine].extend(lane_times)
        waits[engine].extend(t - ready for t in lane_times)
        # Scheduling deadlines are not execution semantics. In particular,
        # deferred setup vloads must not look like body node lookups.
        semantic_round = -1 if op.get("is_setup", False) else op.get("semantic_round", op["round"])
        by_round[semantic_round][engine].extend(lane_times)
        if engine == "load" and op["slot"][0] == "load_offset":
            gathers.append(issued[i])
        if (engine == "load" and op["slot"][0] in ("load_offset", "vload")
                and semantic_round > 0):
            lookup_loads.append(issued[i])
    engines = {}
    for engine, count in counts.items():
        capacity = SLOT_LIMITS[engine]
        engines[engine] = {
            "slots": count,
            "static_floor": ceil(count / capacity),
            "first_issue": min(engine_times[engine]),
            "last_issue": max(engine_times[engine]),
            # Conditional on this schedule's startup, not a global bound.
            "conditional_finish_bound": min(engine_times[engine]) + ceil(count / capacity),
            "full_issue_cycles": sum(
                len(bundle.get(engine, ())) == capacity for bundle in builder.instrs
            ),
            "ready_wait_total": sum(waits[engine]),
            "ready_wait_max": max(waits[engine]),
        }
    return {
        "cycles": len(builder.instrs),
        "scratch": builder.scratch_ptr,
        "policy": builder.schedule_policy,
        "offloaded_vector_ops": len(builder.offloaded_ops),
        "fragmented_vector_ops": sum(len(set(ts)) > 1 for ts in builder.lane_issue_cycles.values()),
        "max_fragment_span": max((max(ts) - min(ts) + 1 for ts in builder.lane_issue_cycles.values()), default=0),
        "pruned_constant_loads": builder.pruned_constant_loads,
        "preencoded_nodes": sum(index is not None for index in builder.workspace_node_indices),
        "unique_preencoded_nodes": len({index for index in builder.workspace_node_indices if index is not None}),
        "workspace_words": builder.preencoded_node_count,
        "workspace_layout": builder.workspace_layout,
        "blocked_lookup": builder.blocked_lookup,
        "blocked_setup_xor_fused": builder.blocked_setup_xor_fused,
        "blocked_setup_deadlines": builder.blocked_setup_deadlines,
        "blocked_early_tail_select": builder.blocked_early_tail_select,
        "blocked_unused_weight_pruned": builder.blocked_unused_weight_pruned,
        "blocked_encode_depth6": builder.blocked_encode_depth6,
        "blocked_compact_deep": builder.blocked_compact_deep,
        "compact_lane_tail": builder.compact_lane_tail,
        "compact_parent_index_select": builder.compact_parent_index_select,
        "compact_deep_landing": builder.compact_deep_landing,
        "compact_shallow_landing": getattr(builder, "compact_shallow_landing", False),
        "compact_flow_exchange": getattr(builder, "compact_flow_exchange", False),
        "compact_depth3_gather_chunks": getattr(builder, "compact_depth3_gather_chunks", 0),
        "compact_setup_deadline_cap": getattr(builder, "compact_setup_deadline_cap", 4),
        "compact_deep_select_delay": getattr(builder, "compact_deep_select_delay", 0),
        "compact_startup_groups": getattr(builder, "compact_startup_groups", 0),
        "compact_fma_priority": getattr(builder, "compact_fma_priority", False),
        "blocked_reverse_input_chain_length": builder.blocked_reverse_input_chain_length,
        "direct_gather_addresses": builder.direct_gather_addresses,
        "engines": engines,
        "compute": {
            "weighted_equivalents": counts["valu"] + counts["alu"] / 8,
            "optimistic_combined_floor": ceil((counts["valu"] + counts["alu"] / 8) / 7.5),
            "conditional_combined_finish_bound": ceil((counts["valu"] + counts["alu"] / 8
                + 6 * min(engine_times["valu"]) + 1.5 * min(engine_times["alu"])) / 7.5),
            "necessary_900_deficit": max(0, counts["valu"] + counts["alu"] / 8 - 6750),
        },
        "gather": {
            "first": min(gathers), "last": max(gathers), "slots": len(gathers),
            "drain_cycles": len(builder.instrs) - max(gathers) - 1,
            "conditional_finish_bound": min(gathers) + ceil(len(gathers) / 2),
        },
        "lookup_load": {
            "first": min(lookup_loads), "last": max(lookup_loads), "slots": len(lookup_loads),
            "drain_cycles": len(builder.instrs) - max(lookup_loads) - 1,
            "conditional_finish_bound": min(lookup_loads) + ceil(len(lookup_loads) / 2),
        },
        "rounds": {
            r: {e: {"slots": len(ts), "first": min(ts), "last": max(ts)}
                for e, ts in engines.items()}
            for r, engines in sorted(by_round.items())
        },
    }


if __name__ == "__main__":
    kernel = AnalyzedKernel()
    kernel.build_kernel(10, 2047, 256, 16)
    print(json.dumps(analyze(kernel), indent=2))
