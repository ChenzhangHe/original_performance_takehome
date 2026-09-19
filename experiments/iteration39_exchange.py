"""Place the depth3 load/flow exchange across the two traversals.

Pinned 928-cycle parent. Late traversal parent addresses include field one;
the two required bias constants are included in all resource counts.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import types

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from experiments.dataflow_check import verify_dataflow
from tune_kernel import check
from verify_kernel import verify_emission, verify_words

SOURCE_REF = '161c60200be0aa071532f6784817a5b562e9c2c8'


def make_source(args):
    source = subprocess.run(['git', 'show', f'{SOURCE_REF}:perf_takehome.py'], cwd=REPO,
                            check=True, capture_output=True, text=True).stdout
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:100])
        source = source.replace(old, new)
    early = tuple(range(args.early_start, args.early_start+args.early_count))
    late = tuple(range(args.late_start, args.late_start+args.late_count))
    assert all(0 <= group < 32 for group in early+late)
    replace('''                exchange = (self.compact_flow_exchange and round_no <= 3
                            and chunk_no >= chunk_count-self.compact_depth3_gather_chunks)''',
        f'''                exchange = (self.compact_flow_exchange and
                            ((round_no <= 3 and chunk_no in {early!r}) or
                             (11 <= round_no <= 14 and chunk_no in {late!r})))''')
    if late:
        replace('            direct_vectors.append(exchange_exit_even)',
            '''            exchange_tail_exit = vector_const((-forest_values_p-n_nodes-1)&0xffffffff, "exchange_tail_exit")
            exchange_tail_even = vector_const((-forest_values_p-n_nodes-5)&0xffffffff, "exchange_tail_even")
            direct_vectors.extend((exchange_tail_exit, exchange_tail_even))
            direct_vectors.append(exchange_exit_even)''')
        replace('''                        chosen_bias = select(chunk_tmp1, parity_dest, exchange_exit_even, exchange_exit, parity)''',
            '''                        even_bias, odd_bias = ((exchange_exit_even, exchange_exit) if round_no < 11
                                               else (exchange_tail_even, exchange_tail_exit))
                        chosen_bias = select(chunk_tmp1, parity_dest, even_bias, odd_bias, parity)''')
    if not args.full_policies:
        replace('        for policy in dict.fromkeys(policies):', '''        policies = (
            "fragment_adaptive_tail_hetero_360_240_240_220_900",
            "fragment_balanced_adaptive_tail_compute_245_960",
            "fragment_adaptive_tail_hetero_360_220_140_140_920",
            "balanced_adaptive_tail_hetero_360_220_140_140_920",
        )
        for policy in dict.fromkeys(policies):''')
    return source


def run(args):
    module = types.ModuleType('exchange_placement_probe')
    exec(compile(make_source(args), '<exchange_placement_probe>', 'exec'), module.__dict__)
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
        stage = 'emission'; verify_emission(builder)
        stage = 'scratch_provenance'; verify_dataflow(builder)
        for seed in (123, 456, 789):
            stage = f'frozen_seed_{seed}'; check(builder, seed)
        stage = 'full_word_workspace'
        verify_words(builder, 'full_word', [(i*0x9e3779b9)&0xffffffff for i in range(2047)],
                     [(i*0xabcdef01+0x80000000)&0xffffffff for i in range(256)])
    except AssertionError as error:
        result.update(rejected=stage, detail=str(error), scratch=builder.scratch_ptr)
    else:
        result.update(cycles=len(builder.instrs), scratch=builder.scratch_ptr,
                      verification='3 frozen seeds, full-word workspace, exact emission and scratch provenance')
    counts = Counter(op['engine'] for op in builder.operations)
    flow = [i for i,bundle in enumerate(builder.instrs) for _ in bundle.get('flow',())]
    result.update(logical_slots=counts, weighted=counts['valu']+counts['alu']/8,
                  policy=builder.schedule_policy, schedule_stats=builder.schedule_stats,
                  flow_first=min(flow), flow_last=max(flow), flow_holes=max(flow)-min(flow)+1-len(flow))
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--early-start', type=int, default=16)
    parser.add_argument('--early-count', type=int, default=16)
    parser.add_argument('--late-start', type=int, default=16)
    parser.add_argument('--late-count', type=int, default=0)
    parser.add_argument('--full-policies', action='store_true')
    run(parser.parse_args())


if __name__ == '__main__':
    main()
