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
    identities = {id(op["slot"]): i for i, op in enumerate(operations)}
    assert len(identities) == len(operations)
    issued = {}
    for cycle, bundle in enumerate(builder.instrs):
        for slots in bundle.values():
            for slot in slots:
                issued[identities[id(slot)]] = cycle
    assert len(issued) == len(operations)
    by_round = defaultdict(lambda: defaultdict(list))
    waits = defaultdict(list)
    counts = Counter()
    gathers = []
    for i, op in enumerate(operations):
        ready = max((issued[d] + 1 for d in op["deps"]), default=0)
        assert issued[i] >= ready, (i, ready, issued[i])
        engine = op["engine"]
        counts[engine] += 1
        waits[engine].append(issued[i] - ready)
        by_round[op["round"]][engine].append(issued[i])
        if engine == "load" and op["slot"][0] == "load_offset":
            gathers.append(issued[i])
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
        "engines": engines,
        "gather": {
            "first": min(gathers), "last": max(gathers), "slots": len(gathers),
            "drain_cycles": len(builder.instrs) - max(gathers) - 1,
            "conditional_finish_bound": min(gathers) + ceil(len(gathers) / 2),
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
