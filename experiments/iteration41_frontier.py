"""Consumer-frontier refinement pinned to the committed 924-cycle kernel."""
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

SOURCE_REF = "109610180033baa744a0fe80a2501e1ba2f73b00"


def make_source(args):
    source = subprocess.run(["git", "show", f"{SOURCE_REF}:perf_takehome.py"],
                            cwd=REPO, check=True, capture_output=True, text=True).stdout

    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:120])
        source = source.replace(old, new)

    if args.passes:
        replace('''        def priority(policy, op_id):''', '''        frontier_bonus = [0] * len(ops)
        frontier_deadlines = [len(ops)] * len(ops)

        def priority(policy, op_id):''')
        replace('''            priorities = [priority(policy, op_id) for op_id in range(len(ops))]''', '''            priorities = [priority(policy, op_id) for op_id in range(len(ops))]
            priorities = [(p[0] + frontier_bonus[i], *p[1:]) for i, p in enumerate(priorities)]''')
        if args.mode == 'deadline':
            replace('''                    capacity = SLOT_LIMITS[engine]''', '''                    candidates.sort(key=lambda i: max(0, len(bundles)-frontier_deadlines[i]+1), reverse=True)
                    capacity = SLOT_LIMITS[engine]''')
        replace('''        identities = {id(op["slot"]): i for i, op in enumerate(ops)}''', f'''        self.frontier_trace = []
        self.frontier_base_policy_count = len(self.schedule_stats)
        current = best
        base_policy = self.schedule_policy
        slot_ids = {{id(op["slot"]): i for i, op in enumerate(ops)}}
        accesses = [self.instruction_accesses(op["engine"], op["slot"]) for op in ops]
        # Derive RAW edges from the original logical instructions. In particular,
        # an address-overwrite WAR edge is not a loaded-value consumer.
        raw_parents = [{{d for d in op["deps"] if set(accesses[d][1]) & set(accesses[i][0])}}
                       for i, op in enumerate(ops)]
        raw_children = [[] for _ in ops]
        for i, parents in enumerate(raw_parents):
            for dep in parents:
                raw_children[dep].append(i)

        def packet_for(load_id):
            owner = (ops[load_id]["chunk"], ops[load_id]["round"])
            queue, seen = [load_id], {{load_id}}
            join = None
            for i in queue:
                if i != load_id and ops[i]["engine"] == "valu":
                    join = i
                    break
                for child in raw_children[i]:
                    if child not in seen and (ops[child]["chunk"], ops[child]["round"]) == owner:
                        seen.add(child)
                        queue.append(child)
            if join is None:
                return None
            packet, bridge, distance = set(), set(), {{join: 0}}
            todo = [join]
            while todo:
                i = todo.pop()
                if (ops[i]["chunk"], ops[i]["round"]) != owner:
                    continue
                bridge.add(i)
                if ops[i]["engine"] == "load" and ops[i]["slot"][0] == "load_offset":
                    packet.add(i)
                    continue  # The load's address is outside the data frontier.
                for dep in raw_parents[i]:
                    distance[dep] = max(distance.get(dep, 0), distance[i]+1)
                    todo.append(dep)
            if len(packet) != VLEN:
                return None
            return join, packet, bridge, distance

        for pass_no in range({args.passes}):
            finish = {{slot_ids[id(slot)]: t for t, bundle in enumerate(current)
                      for slots in bundle.values() for slot in slots}}
            first = {{}}
            for t, bundle in enumerate(current):
                for slots in bundle.values():
                    for slot in slots:
                        first.setdefault(slot_ids[id(slot)], t)
            cursor = max(finish, key=finish.get)
            frontiers, seen_packets = [], set()
            while True:
                op = ops[cursor]
                if (op["engine"] == "load" and op["slot"][0] == "load_offset"
                        and op["round"] >= {args.min_round}):
                    found = packet_for(cursor)
                    if found is not None:
                        join, packet, bridge, distance = found
                        key = tuple(sorted(packet))
                        if key not in seen_packets:
                            seen_packets.add(key)
                            released = sorted(max((finish[d]+1 for d in ops[i]["deps"]), default=0)
                                              for i in packet)
                            ports = [0] * SLOT_LIMITS["load"]
                            ideal = 0
                            for release in released:
                                port = min(range(len(ports)), key=ports.__getitem__)
                                ideal = max(release, ports[port])
                                ports[port] = ideal+1
                            ideal = max(ports)-1
                            last = max(finish[i] for i in packet)
                            excess = last-ideal
                            if excess > 0:
                                frontiers.append((excess, last, join, packet, bridge, distance, ideal))
                if not op["deps"]:
                    break
                cursor = max(op["deps"], key=finish.get)
            if not frontiers:
                self.frontier_trace.append(dict(pass_no=pass_no+1, no_excess_frontier=True))
                break
            excess, last, join, packet, bridge, distance, ideal = max(frontiers, key=lambda f: f[:2])
            selected = packet if {args.scope!r} == "loads" else bridge - ({{join}} if {args.scope!r} == "bridge" else set())
            entry_macs = [i for i in raw_children[join] if ops[i]["engine"] == "valu"
                          and ops[i]["slot"][0] == "multiply_add"
                          and (ops[i]["chunk"], ops[i]["round"]) == (ops[join]["chunk"], ops[join]["round"])]
            if {args.scope!r} == "entry":
                selected = selected | set(entry_macs)
                distance.update((i, -1) for i in entry_macs)
            if {args.mode!r} == "deadline":
                for i in selected:
                    frontier_deadlines[i] = first[join] - {args.advance} - distance[i]
            else:
                for i in selected:
                    frontier_bonus[i] += {args.gain}
            current = make_schedule(base_policy)
            after_first, after_finish = {{}}, {{}}
            for t, bundle in enumerate(current):
                for slots in bundle.values():
                    for slot in slots:
                        i = slot_ids[id(slot)]
                        after_first.setdefault(i, t)
                        after_finish[i] = t
            name = base_policy + ":frontier_" + str(pass_no+1)
            self.schedule_stats[name] = len(current)
            self.frontier_trace.append(dict(pass_no=pass_no+1, cycles=len(current),
                excess=excess, load_last=last, ideal_packet_last=ideal, consumer=join,
                consumer_first=first[join], consumer_round=ops[join]["round"],
                consumer_before_ready=max((finish[d]+1 for d in ops[join]["deps"]), default=0),
                consumer_before_finish=finish[join],
                consumer_chunk=ops[join]["chunk"], packet=sorted(packet), selected=sorted(selected),
                lane_issue=sorted(first[i] for i in packet), lane_issue_after=sorted(after_first[i] for i in packet),
                consumer_after_first=after_first[join], consumer_after_finish=after_finish[join],
                consumer_after_ready=max((after_finish[d]+1 for d in ops[join]["deps"]), default=0),
                entry_macs=[dict(id=i, before=first[i], after=after_first[i]) for i in entry_macs]))
            if len(current) < len(best) or {not args.keep_baseline}:
                best = current
                self.schedule_policy = name
        identities = {{id(op["slot"]): i for i, op in enumerate(ops)}}''')
    if not args.full_policies:
        replace('        for policy in dict.fromkeys(policies):', '''        policies = ("fragment_adaptive_tail_hetero_360_240_240_220_900",
                    "fragment_adaptive_tail_hetero_360_220_140_140_920",
                    "fragment_adaptive_tail_hetero_360_220_140_140_900")
        for policy in dict.fromkeys(policies):''')
    return source


def run(args):
    module = types.ModuleType('iteration41_frontier_probe')
    exec(compile(make_source(args), '<iteration41_frontier_probe>', 'exec'), module.__dict__)

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
        result['frontier_trace'] = getattr(builder, 'frontier_trace', [])
        result['base_policy_count'] = getattr(builder, 'frontier_base_policy_count', len(builder.schedule_stats))
    if args.inspect and 'cycles' in result:
        ops, finish = builder.operations, builder.issue_cycles
        consumer = min((i for i, op in enumerate(ops)
                        if op['engine'] == 'valu' and op['chunk'] == 0 and op['round'] == 10
                        and op['slot'][0] == '^' and op['slot'][1] == op['slot'][2]),
                       key=lambda i: ops[i]['local_seq'])
        def diagnostic(i):
            op = ops[i]
            return dict(id=i, first=builder.issue_first_cycles[i], finish=finish[i],
                        ready=max((finish[d]+1 for d in op['deps']), default=0),
                        engine=op['engine'], slot=builder.logical_slots[i],
                        offloaded=i in builder.offloaded_ops, lane_times=builder.lane_issue_cycles.get(i),
                        chunk=op['chunk'], round=op['round'])
        result['consumer_diagnostic'] = diagnostic(consumer)
        result['consumer_parents'] = [diagnostic(i) for i in ops[consumer]['deps']
                                      if ops[i]['chunk'] == 0 and ops[i]['round'] == 10]
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full-policies', action='store_true')
    parser.add_argument('--passes', type=int, default=1)
    parser.add_argument('--scope', choices=('loads', 'bridge', 'join', 'entry'), default='loads')
    parser.add_argument('--mode', choices=('bonus', 'deadline'), default='bonus')
    parser.add_argument('--gain', type=int, default=360)
    parser.add_argument('--advance', type=int, default=4)
    parser.add_argument('--min-round', type=int, default=10)
    parser.add_argument('--keep-baseline', action='store_true')
    parser.add_argument('--inspect', action='store_true')
    run(parser.parse_args())
