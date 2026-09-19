"""Pinned bounded ALU/VALU structural balancing probes, with strict checks."""
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
from verify_kernel import verify_emission, verify_words, verify_workspace_addresses
from experiments.dataflow_check import verify_dataflow

SOURCE_REF = 'ee87c658e6247232801432d7ab5a548d324c096d'


def make_source(args):
    source = subprocess.check_output(['git', 'show', f'{SOURCE_REF}:perf_takehome.py'], cwd=REPO, text=True)
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:100])
        source = source.replace(old, new)
    if args.shallow_stores:
        replace('idx_ready, block_stores) for lane in range(VLEN)]',
                'idx_ready, block_stores[:4]) for lane in range(VLEN)]')
    if args.scalar_broadcast != 'none':
        selection = {'all': 'True', 'constants': 'slot[2] in constant_addresses',
                     'runtime': 'slot[2] not in constant_addresses'}[args.scalar_broadcast]
        replace('        setup_ops, setup_writers = self.pack_setup(', f'''        constant_addresses = set(self.const_map.values())
        for instr in self.instrs:
            selected_broadcasts = [slot for slot in instr.get("valu", ())
                                   if slot[0] == "vbroadcast" and ({selection})]
            if selected_broadcasts:
                instr["valu"] = [slot for slot in instr["valu"] if slot not in selected_broadcasts]
                if not instr["valu"]:
                    del instr["valu"]
                for _, dest, scalar in selected_broadcasts:
                    instr.setdefault("alu", []).extend(("|", dest+lane, scalar, scalar) for lane in range(VLEN))
        setup_ops, setup_writers = self.pack_setup(''')
    if args.age or args.partial_priority:
        replace('            partial = None  # (logical operation ID, first unissued lane)',
                '            partial = None  # (logical operation ID, first unissued lane)\n            partial_since = 0')
        reserve_condition = (f'len(bundles) - partial_since >= {args.age}' if not args.partial_priority else
                             '(not candidates or priorities[partial[0]] >= priorities[candidates[min(len(candidates)-1, max(0, capacity-(VLEN-partial[1])))]] )')
        replace('                    selected = candidates[:capacity]', f'''                    if (fragmented and engine == "alu" and partial is not None
                            and {reserve_condition}):
                        op_id, lane = partial
                        end = min(VLEN, lane + capacity)
                        bundle.setdefault(f"alu_fragment_{{lane}}_{{end}}", []).append(ops[op_id]["slot"])
                        capacity -= end-lane
                        if end == VLEN:
                            chosen.append(op_id)
                            partial = None
                        else:
                            partial = (op_id, end)
                    selected = candidates[:capacity]''')
        replace('                                - VLEN * len(bundle.get("alu_vector", ())))', '''                                - VLEN * len(bundle.get("alu_vector", ()))
                                - sum((int(e.split("_")[-1])-int(e.split("_")[-2]))*len(v)
                                      for e,v in bundle.items() if e.startswith("alu_fragment_")))''')
        replace('                            lane = 0\n                        end =', '                            lane = 0\n                            partial_since = len(bundles)\n                        end =')
    if args.choose != 'priority':
        keys = {
            'slack': '(bottom_level[i], len(successors[i]), i)',
            'critical': '(-bottom_level[i], -len(successors[i]), i)',
            'scalar': '(any(ops[s]["engine"] != "alu" for s in successors[i]), bottom_level[i], i)',
            'no_load': '(any(ops[s]["engine"] in ("load", "flow") for s in successors[i]), bottom_level[i], i)',
            'fanin': '(sum(remaining[s] == 1 for s in successors[i]), bottom_level[i], i)',
        }
        replace('''                            op_id = next((i for i in ready["valu"]
                                          if ops[i]["slot"][0] not in
                                          ("vbroadcast", "multiply_add")), None)''', f'''                            eligible = [i for i in ready["valu"]
                                        if ops[i]["slot"][0] not in ("vbroadcast", "multiply_add")]
                            op_id = min(eligible, key=lambda i: {keys[args.choose]}) if eligible else None''')
    if args.valu_fma:
        fma_key = {'all': 'ops[i]["slot"][0] in ("multiply_add", "vbroadcast")',
                   'only_fma': 'ops[i]["slot"][0] == "multiply_add"',
                   'keep_startup': '(i in startup_dag, ops[i]["slot"][0] in ("multiply_add", "vbroadcast"))'}[args.fma_mode]
        replace('                    capacity = SLOT_LIMITS[engine]', f'''                    if fragmented and engine == "valu":
                        candidates.sort(key=lambda i: {fma_key}, reverse=True)
                    capacity = SLOT_LIMITS[engine]''')
    if args.no_partial_after:
        replace('''                            if op_id is None:
                                break
                            ready["valu"].remove(op_id)''', f'''                            if op_id is None or (len(bundles)>={args.no_partial_after} and capacity<VLEN):
                                break
                            ready["valu"].remove(op_id)''')
    if not args.full_policies:
        replace('        for policy in dict.fromkeys(policies):', '''        for policy in (
                "fragment_adaptive_tail_hetero_360_240_240_220_900",
                "fragment_adaptive_tail_hetero_360_220_140_140_920",
                "fragment_adaptive_tail_hetero_360_220_140_140_900"):
'''.rstrip())
    return source


def run(args):
    module = types.ModuleType('iteration40_balance_probe')
    exec(compile(make_source(args), '<iteration40_balance_probe>', 'exec'), module.__dict__)
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
        verify_workspace_addresses(builder)
        for seed in (123, 456, 789):
            stage = f'frozen_seed_{seed}'
            check(builder, seed)
        stage = 'full_word_workspace'
        verify_words(builder, 'full_word', [(i*0x9e3779b9)&0xffffffff for i in range(2047)],
                     [(i*0xabcdef01+0x80000000)&0xffffffff for i in range(256)])
    except (AssertionError, ValueError) as error:
        result.update(rejected=stage, detail=str(error), scratch=builder.scratch_ptr)
    else:
        result.update(cycles=len(builder.instrs), scratch=builder.scratch_ptr,
                      verification='3 frozen seeds, full-word workspace, exact emission, scratch provenance')
    if hasattr(builder, 'schedule_policy'):
        slots = Counter()
        for bundle in builder.instrs:
            for engine, items in bundle.items():
                if engine == 'alu_vector':
                    slots['alu'] += 8*len(items)
                elif engine.startswith('alu_fragment_'):
                    first, last = map(int, engine.split('_')[-2:])
                    slots['alu'] += (last-first)*len(items)
                else:
                    slots[engine] += len(items)
        delays = sorted((max(lanes)-min(lanes), op_id)
                        for op_id, lanes in builder.lane_issue_cycles.items())
        result.update(policy=builder.schedule_policy, schedule_cycles=len(builder.instrs),
                      slots=slots, weighted=slots['valu']+slots['alu']/8,
                      policies=builder.schedule_stats,
                      offloads=len(builder.lane_issue_cycles), partial_max=max(delays, default=(0,0))[0],
                      partial_sum=sum(delay for delay,_ in delays))
    print(json.dumps(result), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full-policies', action='store_true')
    parser.add_argument('--age', type=int, default=0)
    parser.add_argument('--choose', choices=('priority', 'slack', 'critical', 'scalar', 'no_load', 'fanin'), default='priority')
    parser.add_argument('--valu-fma', action='store_true')
    parser.add_argument('--fma-mode', choices=('all', 'only_fma', 'keep_startup'), default='all')
    parser.add_argument('--partial-priority', action='store_true')
    parser.add_argument('--no-partial-after', type=int, default=0)
    parser.add_argument('--shallow-stores', action='store_true')
    parser.add_argument('--scalar-broadcast', choices=('none', 'all', 'constants', 'runtime'), default='none')
    run(parser.parse_args())
