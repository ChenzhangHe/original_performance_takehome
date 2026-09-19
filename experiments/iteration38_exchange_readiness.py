"""Small readiness A/B set on one in-memory snapshot of the exchange graph.

Root may edit iteration38_exchange.py concurrently; make_source runs exactly
once before any variant, and the SHA256 of that source accompanies each row.
No operations or actual dependencies are removed or weakened.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import types

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from experiments.iteration38_exchange import make_source
from experiments.dataflow_check import verify_dataflow
from tune_kernel import check
from verify_kernel import verify_emission, verify_words


def transform(snapshot, variant):
    if variant == 'select_late_cap4':
        return transform(transform(snapshot, 'select_late'), 'cap4')
    source = snapshot
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:100])
        source = source.replace(old, new)
    target = '''                        chosen_bias = select(chunk_tmp1, parity_dest, exchange_even, address_bias, parity)
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, two, chunk_tmp1),
                                         idx_ready, chosen_bias, node_address_reads)
                        continue'''
    if variant in ('select_early', 'select_late', 'address_early', 'address_late', 'both_early', 'both_late'):
        select_delta = {'select_early': -1, 'select_late': 1, 'both_early': -1, 'both_late': 1}.get(variant, 0)
        address_delta = {'address_early': -1, 'address_late': 1, 'both_early': -1, 'both_late': 1}.get(variant, 0)
        replace(target, f'''                        emit_context["round"] = round_no + {select_delta}
                        chosen_bias = select(chunk_tmp1, parity_dest, exchange_even, address_bias, parity)
                        emit_context["round"] = round_no + {address_delta}
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, two, chunk_tmp1),
                                         idx_ready, chosen_bias, node_address_reads)
                        emit_context["round"] = round_no
                        continue''')
    elif variant == 'no_lane_tail':
        old = 'self.compact_lane_tail and depth in (5, 6, 7, 8, 9)'
        assert source.count(old) == 2
        source = source.replace(old, 'self.compact_lane_tail and depth in (5, 6, 7)')
    elif variant == 'cap4':
        replace('op["round"] = min(6, needed[i])', 'op["round"] = min(4, needed[i])')
    else:
        assert variant == 'baseline', variant
    return source


def run(snapshot, variant, full):
    source = transform(snapshot, variant)
    if not full:
        old = '        for policy in dict.fromkeys(policies):'
        assert source.count(old) == 1
        source = source.replace(old, '''        policies = ("fragment_adaptive_tail_hetero_360_220_140_140_920",
                    "balanced_adaptive_tail_hetero_360_220_140_140_920",
                    "fragment_adaptive_tail_hetero_360_220_140_140_900")
        for policy in dict.fromkeys(policies):''')
    module = types.ModuleType('exchange_readiness')
    exec(compile(source, '<exchange_readiness>', 'exec'), module.__dict__)
    class Builder(module.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)
        def allocate_node_lifetimes(self, ops, uses, reusable=()):
            self.logical_slots = [op['slot'] for op in ops]
            super().allocate_node_lifetimes(ops, uses, reusable)
    builder = Builder()
    result = dict(snapshot_sha256=hashlib.sha256(snapshot.encode()).hexdigest(), variant=variant, full=full)
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
                      weighted=counts['valu']+counts['alu']/8, counts=counts,
                      verification='3 frozen seeds, full-word workspace, exact emission and scratch provenance')
    if hasattr(builder, 'schedule_policy'):
        result['policy'] = builder.schedule_policy
    if 'cycles' in result:
        flow_times = [builder.issue_cycles[i] for i, op in enumerate(builder.operations)
                      if op['engine'] == 'flow']
        load_times = [builder.issue_cycles[i] for i, op in enumerate(builder.operations)
                      if op['engine'] == 'load' and op['round'] > 0 and not op.get('is_setup')]
        result['flow_first_last_holes'] = [min(flow_times), max(flow_times),
                                          max(flow_times)-min(flow_times)+1-len(flow_times)]
        result['lookup_first_last'] = [min(load_times), max(load_times)]
    print(json.dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variants', nargs='+', default=['baseline', 'select_early', 'select_late',
                                                        'address_early', 'both_early', 'no_lane_tail', 'cap4'])
    parser.add_argument('--full-policies', action='store_true')
    parser.add_argument('--groups', nargs='+', type=int, default=list(range(11, 22)))
    parser.add_argument('--legacy', action='store_true')
    args = parser.parse_args()
    snapshot = make_source(argparse.Namespace(groups=args.groups, select_depths=[8, 9],
                                              select_groups=list(range(32)), full_policies=True,
                                              parent_select=not args.legacy, reuse_padding=not args.legacy))
    best = 940
    for variant in args.variants:
        result = run(snapshot, variant, args.full_policies or variant == 'baseline')
        if result.get('cycles', 10000) < best and not args.full_policies and variant != 'baseline':
            run(snapshot, variant, True)
        best = min(best, result.get('cycles', 10000))


if __name__ == '__main__':
    main()
