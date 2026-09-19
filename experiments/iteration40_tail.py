"""Pinned late-chain readiness feedback probes; instruction DAG is unchanged."""
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

SOURCE_REF = "ee87c658e6247232801432d7ab5a548d324c096d"


def make_source(args):
    source = subprocess.run(["git", "show", f"{SOURCE_REF}:perf_takehome.py"],
                            cwd=REPO, check=True, capture_output=True, text=True).stdout

    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:120])
        source = source.replace(old, new)

    if args.feedback_passes:
        eligible = (f'op["round"] >= {args.feedback_min_round}' if args.feedback_min_round is not None
                    else f'first[waiter] >= {args.feedback_since}')
        replace('''        def priority(policy, op_id):''', '''        feedback_bonus = [0] * len(ops)
        feedback_deadline = [len(ops)] * len(ops)

        def priority(policy, op_id):''')
        replace('''            priorities = [priority(policy, op_id) for op_id in range(len(ops))]''', '''            priorities = [priority(policy, op_id) for op_id in range(len(ops))]
            priorities = [(p[0] + feedback_bonus[i], *p[1:]) for i, p in enumerate(priorities)]''')
        if args.feedback_deadline:
            replace('''                    capacity = SLOT_LIMITS[engine]''', '''                    candidates.sort(key=lambda i: max(0, len(bundles)-feedback_deadline[i]+1), reverse=True)
                    capacity = SLOT_LIMITS[engine]''')
        replace('''        identities = {id(op["slot"]): i for i, op in enumerate(ops)}''', f'''        self.feedback_trace = []
        current = best
        feedback_base_policy = self.schedule_policy
        slot_ids = {{id(op["slot"]): i for i, op in enumerate(ops)}}
        for pass_no in range({args.feedback_passes}):
            finish = {{slot_ids[id(slot)]: t for t, bundle in enumerate(current)
                      for slots in bundle.values() for slot in slots}}
            first = {{}}
            for t, bundle in enumerate(current):
                for slots in bundle.values():
                    for slot in slots:
                        first.setdefault(slot_ids[id(slot)], t)
            waiter = max(finish, key=finish.get)
            delayed = []
            chain_eligible = []
            while True:
                op = ops[waiter]
                ready_at = max((finish[d]+1 for d in op["deps"]), default=0)
                wait = first[waiter]-ready_at
                if {eligible}:
                    chain_eligible.append(waiter)
                if wait > 0 and {eligible}:
                    delayed.append((wait, waiter, ready_at, first[waiter]))
                if not op["deps"]:
                    break
                waiter = max(op["deps"], key=finish.get)
            selected = sorted(delayed, reverse=True)[:{args.feedback_top}]
            if {args.feedback_deadline}:
                for op_id in chain_eligible:
                    feedback_deadline[op_id] = first[op_id] - {args.feedback_deadline}
            else:
                for wait, op_id, ready_at, first_at in selected:
                    feedback_bonus[op_id] += {args.feedback_gain}
            current = make_schedule(feedback_base_policy)
            name = feedback_base_policy + ":feedback_" + str(pass_no+1)
            self.schedule_stats[name] = len(current)
            self.feedback_trace.append(dict(pass_no=pass_no+1, cycles=len(current),
                deadline_targets=len(chain_eligible) if {args.feedback_deadline} else 0,
                promoted=[dict(id=i, wait=w, ready=r, first=f, round=ops[i]["round"],
                               chunk=ops[i]["chunk"], engine=ops[i]["engine"], slot=ops[i]["slot"],
                               bonus=feedback_bonus[i]) for w, i, r, f in selected]))
            if len(current) < len(best):
                best = current
                self.schedule_policy = name
        identities = {{id(op["slot"]): i for i, op in enumerate(ops)}}''')
    if args.mac_first:
        replace('''                    capacity = SLOT_LIMITS[engine]''', '''                    if fragmented and engine == "valu":
                        candidates.sort(key=lambda i: ops[i]["slot"][0] == "multiply_add", reverse=True)
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
    module = types.ModuleType('iteration40_tail_probe')
    exec(compile(make_source(args), '<iteration40_tail_probe>', 'exec'), module.__dict__)

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
        result['feedback_trace'] = getattr(builder, 'feedback_trace', [])
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
        last = max(times, key=times.get)
        chain = []
        while True:
            chain.append(entry(last))
            if not ops[last]['deps']:
                break
            last = max(ops[last]['deps'], key=times.get)
        result['latest_dependency_chain'] = chain
        result['store_finish'] = {op['chunk']: times[i] for i, op in enumerate(ops)
                                  if op['engine']=='store' and not op.get('is_setup',False)}
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full-policies', action='store_true')
    parser.add_argument('--policy', nargs='+')
    parser.add_argument('--feedback-passes', type=int, default=0)
    parser.add_argument('--feedback-top', type=int, default=1)
    parser.add_argument('--feedback-gain', type=int, default=100)
    parser.add_argument('--feedback-since', type=int, default=650)
    parser.add_argument('--feedback-min-round', type=int)
    parser.add_argument('--feedback-deadline', type=int, default=0)
    parser.add_argument('--mac-first', action='store_true')
    parser.add_argument('--inspect', action='store_true')
    run(parser.parse_args())
