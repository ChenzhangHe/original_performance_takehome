"""Fixed-parent startup/tail readiness probes; no simulator changes."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import types

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tune_kernel import check
from verify_kernel import verify_emission, verify_words
from experiments.dataflow_check import verify_dataflow

SOURCE_REF = "161c60200be0aa071532f6784817a5b562e9c2c8"


def make_source(args):
    source = subprocess.run(["git", "show", f"{SOURCE_REF}:perf_takehome.py"],
                            cwd=REPO, check=True, capture_output=True, text=True).stdout

    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:120])
        source = source.replace(old, new)

    if args.release_children:
        replace('val_ready = [*node_loads, *pending]', 'val_ready = node_loads')
    if args.root_input_arm:
        replace('''                    val_ready = emit_scalar_rhs(
                        "^", chunk_val, chunk_val, root_round_value, val_ready
                    )''', '''                    if not (blocked_lookup and round_no == forest_height + 1):
                        val_ready = emit_scalar_rhs(
                            "^", chunk_val, chunk_val, root_round_value, val_ready
                        )''')
        replace('''                        if round_no == rounds - 1:
                            decoded = emit(''', '''                        if blocked_lookup and round_no == forest_height and round_no + 1 < rounds:
                            rooted = emit_scalar_rhs("^", chunk_tmp1, chunk_val, root_value_encoded, val_ready)
                            val_ready = emit("valu", ("^", chunk_val, chunk_tmp1, chunk_tmp2), rooted, shifted)
                        elif round_no == rounds - 1:
                            decoded = emit(''')
    if args.hash_tail_depths is not None:
        old = 'self.compact_lane_tail and depth in (5, 6, 7, 8, 9)'
        assert source.count(old) == 2
        source = source.replace(old, f'self.compact_lane_tail and depth in {tuple(args.hash_tail_depths)!r}')
    if args.tail_bonus:
        replace('''            chunk_no = ops[op_id].get("chunk", -1)''', f'''            chunk_no = ops[op_id].get("chunk", -1)
            if (0 <= chunk_no < {args.tail_groups} and round_no >= {args.tail_round}
                    and ops[op_id]["engine"] in ("valu", "alu")):
                chunk_no += {args.tail_bonus}''')
    if args.input_bootstrap:
        replace('''            chunk_no = ops[op_id].get("chunk", -1)''', f'''            chunk_no = ops[op_id].get("chunk", -1)
            if (chunk_no >= 32 - {args.input_bootstrap} and chunk_no < 32
                    and round_no == 0 and ops[op_id]["engine"] == "load"):
                chunk_no += 4''')
    if args.bootstrap_dag:
        replace('''        def priority(policy, op_id):''', f'''        startup_dag = set()
        for chunk in range(32-{args.bootstrap_dag}, 32):
            target = min((i for i, op in enumerate(ops) if op["engine"] == "flow"
                          and op["chunk"] == chunk and op["round"] == 1), key=lambda i: ops[i]["local_seq"])
            todo = [target]
            while todo:
                i = todo.pop()
                if i not in startup_dag:
                    startup_dag.add(i)
                    todo.extend(ops[i]["deps"])
        startup_distance = [0] * len(ops)
        for i in reversed(topological):
            if i in startup_dag:
                for dep in ops[i]["deps"]:
                    startup_distance[dep] = max(startup_distance[dep], startup_distance[i]+1)

        def priority(policy, op_id):''')
        bootstrap_key = '(i in startup_dag, startup_distance[i])' if args.bootstrap_critical else 'i in startup_dag'
        replace('''                    capacity = SLOT_LIMITS[engine]''', f'''                    candidates.sort(key=lambda i: {bootstrap_key}, reverse=True)
                    capacity = SLOT_LIMITS[engine]''')
    if args.flow_urgency:
        replace('''        def priority(policy, op_id):''', '''        flow_distance = [len(ops)] * len(ops)
        for op_id in reversed(topological):
            if ops[op_id]["engine"] == "flow":
                flow_distance[op_id] = 0
            elif successors[op_id]:
                flow_distance[op_id] = 1 + min(flow_distance[s] for s in successors[op_id])

        def priority(policy, op_id):''')
        replace('''                    capacity = SLOT_LIMITS[engine]''', f'''                    if (engine in ("alu", "valu") and not ready["flow"]
                            and {args.urgency_start} <= len(bundles) < {args.urgency_end}):
                        candidates.sort(key=lambda i: (flow_distance[i] <= {args.flow_urgency},
                                                       priorities[i]), reverse=True)
                    capacity = SLOT_LIMITS[engine]''')
    if args.tail_critical is not None:
        replace('''                    capacity = SLOT_LIMITS[engine]''', f'''                    if len(bundles) >= {args.tail_critical}:
                        candidates.sort(key=lambda i: (bottom_level[i], priorities[i]), reverse=True)
                    capacity = SLOT_LIMITS[engine]''')
    if args.policy:
        replace('        for policy in dict.fromkeys(policies):',
                f'        policies = {tuple(args.policy)!r}\n        for policy in dict.fromkeys(policies):')
    elif not args.full_policies:
        replace('        for policy in dict.fromkeys(policies):', '''        policies = ("fragment_adaptive_tail_hetero_360_240_240_220_900",
                    "fragment_adaptive_tail_hetero_360_220_140_140_920",
                    "fragment_adaptive_tail_hetero_360_220_140_140_900")
        for policy in dict.fromkeys(policies):''')
    return source


def run(args):
    module = types.ModuleType('iteration39_readiness_probe')
    exec(compile(make_source(args), '<iteration39_readiness_probe>', 'exec'), module.__dict__)

    class Builder(module.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)

        def allocate_node_lifetimes(self, ops, uses, reusable=()):
            self.logical_slots = [op['slot'] for op in ops]
            super().allocate_node_lifetimes(ops, uses, reusable)

    builder = Builder()
    result = dict(source_ref=SOURCE_REF, **vars(args))
    stage = 'build'
    try:
        builder.build_kernel(10, 2047, 256, 16)
        stage = 'emission'
        verify_emission(builder)
        stage = 'scratch_provenance'
        verify_dataflow(builder)
        for seed in (123, 456, 789):
            stage = f'frozen_seed_{seed}'
            check(builder, seed)
        stage = 'full_word_workspace'
        verify_words(builder, 'full_word', [(i*0x9e3779b9)&0xffffffff for i in range(2047)],
                     [(i*0xabcdef01+0x80000000)&0xffffffff for i in range(256)])
        if args.compare_production:
            assert args.full_policies, 'Production comparison requires the complete policy set'
            from analyze_kernel import AnalyzedKernel
            import perf_takehome
            stage = 'production_exact_match'
            current = AnalyzedKernel()
            current.build_kernel(10, 2047, 256, 16)
            assert current.instrs == builder.instrs, 'Instructions differ'
            assert current.operations == builder.operations, 'Operations/dependencies differ'
            assert current.logical_slots == builder.logical_slots, 'Logical slots differ'
            assert current.scratch_ptr == builder.scratch_ptr, 'Allocation differs'
            result['production_exact_match'] = True
            stage = 'startup_zero_control'
            saved = perf_takehome.COMPACT_STARTUP_GROUPS
            try:
                perf_takehome.COMPACT_STARTUP_GROUPS = 0
                control = AnalyzedKernel()
                control.build_kernel(10, 2047, 256, 16)
                verify_emission(control)
                verify_dataflow(control)
                check(control, 123)
                assert (len(control.instrs), control.scratch_ptr) == (928, 1440)
                result['startup_zero_control'] = dict(cycles=928, scratch=1440)
            finally:
                perf_takehome.COMPACT_STARTUP_GROUPS = saved
    except AssertionError as error:
        result.update(rejected=stage, detail=str(error), scratch=builder.scratch_ptr)
    else:
        counts = Counter(op['engine'] for op in builder.operations)
        result.update(cycles=len(builder.instrs), scratch=builder.scratch_ptr,
                      weighted=counts['valu']+counts['alu']/8, logical_slots=counts,
                      verification='3 frozen seeds, full-word workspace, exact emission, scratch provenance')
    if hasattr(builder, 'schedule_policy'):
        result['policy'] = builder.schedule_policy
        result['schedule_cycles'] = len(builder.instrs)
    if args.inspect and 'cycles' in result:
        ops, times = builder.operations, builder.issue_cycles
        def entry(i):
            op = ops[i]
            return dict(id=i, t=times[i], first=builder.issue_first_cycles[i],
                        engine=op['engine'], slot=builder.logical_slots[i],
                        round=op['round'], chunk=op['chunk'],
                        ready=max((times[d]+1 for d in op['deps']), default=0))
        flow_times = {times[i] for i, op in enumerate(ops) if op['engine']=='flow'}
        result['flow_holes'] = [i for i in range(min(flow_times), max(flow_times)+1) if i not in flow_times]
        result['last_ops'] = [entry(i) for i in sorted(times, key=times.get) if times[i] >= 895]
        last = max(times, key=times.get)
        chain = []
        while True:
            chain.append(entry(last))
            if not ops[last]['deps']:
                break
            last = max(ops[last]['deps'], key=times.get)
        result['latest_dependency_chain'] = chain
        first_flow = min((i for i, op in enumerate(ops) if op['engine']=='flow'), key=times.get)
        chain = []
        while True:
            chain.append(entry(first_flow))
            if not ops[first_flow]['deps']:
                break
            first_flow = max(ops[first_flow]['deps'], key=times.get)
        result['first_flow_dependency_chain'] = chain
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full-policies', action='store_true')
    parser.add_argument('--policy', nargs='+')
    parser.add_argument('--release-children', action='store_true')
    parser.add_argument('--root-input-arm', action='store_true')
    parser.add_argument('--hash-tail-depths', type=int, nargs='+')
    parser.add_argument('--flow-urgency', type=int, default=0)
    parser.add_argument('--urgency-start', type=int, default=0)
    parser.add_argument('--urgency-end', type=int, default=9999)
    parser.add_argument('--tail-critical', type=int)
    parser.add_argument('--tail-bonus', type=int, default=0)
    parser.add_argument('--tail-groups', type=int, default=4)
    parser.add_argument('--tail-round', type=int, default=11)
    parser.add_argument('--input-bootstrap', type=int, default=0)
    parser.add_argument('--bootstrap-dag', type=int, default=0)
    parser.add_argument('--bootstrap-critical', action='store_true')
    parser.add_argument('--compare-production', action='store_true')
    parser.add_argument('--inspect', action='store_true')
    run(parser.parse_args())
