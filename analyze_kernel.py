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


def analyze(builder):
    operations = builder.operations
    issued = builder.issue_cycles
    first_issued = builder.issue_first_cycles
    assert len(issued) == len(operations)
    by_round = defaultdict(lambda: defaultdict(list))
    waits = defaultdict(list)
    counts = Counter()
    gathers = []
    lookup_loads = []
    for i, op in enumerate(operations):
        ready = max((issued[d] + 1 for d in op["deps"]), default=0)
        assert first_issued[i] >= ready, (i, ready, first_issued[i])
        engine = "alu" if i in builder.offloaded_ops else op["engine"]
        lane_times = builder.lane_issue_cycles.get(i, [issued[i]])
        counts[engine] += len(lane_times)
        waits[engine].extend(t - ready for t in lane_times)
        by_round[op["round"]][engine].extend(lane_times)
        if engine == "load" and op["slot"][0] == "load_offset":
            gathers.append(issued[i])
        if (engine == "load" and op["slot"][0] in ("load_offset", "vload")
                and op["round"] > 0):
            lookup_loads.append(issued[i])
    engines = {}
    for engine, count in counts.items():
        capacity = SLOT_LIMITS[engine]
        engines[engine] = {
            "slots": count,
            "static_floor": ceil(count / capacity),
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
        "workspace_words": builder.preencoded_node_count,
        "workspace_layout": builder.workspace_layout,
        "blocked_lookup": builder.blocked_lookup,
        "direct_gather_addresses": builder.direct_gather_addresses,
        "engines": engines,
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
