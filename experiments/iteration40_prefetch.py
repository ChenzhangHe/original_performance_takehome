"""Narrow depth3's workspace readiness and probe two-child lookahead.

Only the first four shallow stores contain the depth3 padding table. The
remaining four stores belong to unrelated depth4/5 records, not its inputs.
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

SOURCE_REF = 'ee87c658e6247232801432d7ab5a548d324c096d'


def make_source(args):
    source = subprocess.run(['git', 'show', f'{SOURCE_REF}:perf_takehome.py'], cwd=REPO,
                            check=True, capture_output=True, text=True).stdout
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:100])
        source = source.replace(old, new)
    if not args.baseline:
        replace('''                                       idx_ready, block_stores) for lane in range(VLEN)]''',
                '''                                       idx_ready, block_stores[:4]) for lane in range(VLEN)]''')
    if args.pair_prefetch:
        assert not args.baseline
        start = source.index('                elif exchange and depth == 3:')
        end = source.index('                elif blocked_lookup and depth == 3:', start)
        source = source[:start]+f'''                elif exchange and depth == 3:
                    selected = select(chunk_node, path_bits[2][0], pair_left, pair_right,
                                      path_bits[2][1], pair_ready)
                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected)
                    node_address_reads = []
                    node_pool_uses.extend((pair_left+2*VLEN*bank, pair_use_start, len(ops), -2)
                                          for bank in range({args.banks}))
                    node_pool_uses.append((pair_right, pair_use_start, len(ops), 1))
''' + source[end:]
        old = '''                    if exchange and depth in (1, 2):
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, parity_dest,
                                         exchange_neg8 if depth == 1 else exchange_neg4, chunk_idx), parity, idx_ready)
                        continue'''
        replace(old, f'''                    if exchange and depth in (1, 2):
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, parity_dest,
                                         exchange_neg8 if depth == 1 else exchange_neg4, chunk_idx),
                                         parity, idx_ready, pair_address_ready if depth == 2 else None)
                        if depth == 1:
                            pair_use_start = len(ops)
                            pair_left = block_children_base + 32*batch_size + {2*args.banks+2}*offset
                            pair_right = pair_left + {2*args.banks}*VLEN
                            pair_address = pair_right + VLEN
                            # State points at the right member; an 8-word read
                            # four words earlier returns both possible D3 nodes.
                            pair_address_ready = emit("valu", ("+", pair_address, chunk_idx, exchange_neg4), idx_ready)
                            pending = [None] * {args.banks}
                            pair_ready = []
                            merges = []
                            for lane in range(VLEN):
                                bank = lane % {args.banks}
                                buffer = pair_left+2*VLEN*bank+lane
                                loaded = emit("load", ("vload", buffer, pair_address+lane),
                                              pair_address_ready, block_stores[:4], pending[bank])
                                copied = emit("alu", ("+", pair_right+lane, buffer+4, readonly_zero), loaded)
                                pending[bank] = copied
                                pair_ready.extend((loaded, copied))
                                if bank:
                                    merges.append((lane, buffer, loaded))
                            for lane, buffer, loaded in merges:
                                copied = emit("alu", ("+", pair_left+lane, buffer, readonly_zero), loaded, pending[0])
                                pair_ready.append(copied)
                            node_pool_uses.append((pair_address, pair_use_start, len(ops), 1))
                        continue''')
    if not args.full_policies:
        replace('        for policy in dict.fromkeys(policies):', '''        policies = (
            "fragment_adaptive_tail_hetero_360_240_240_220_900",
            "fragment_balanced_adaptive_tail_compute_245_960",
            "fragment_adaptive_tail_hetero_360_220_140_140_920",
        )
        for policy in dict.fromkeys(policies):''')
    return source


def run(args):
    module = types.ModuleType('prefix_readiness_probe')
    exec(compile(make_source(args), '<prefix_readiness_probe>', 'exec'), module.__dict__)
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
    result.update(logical_slots=counts, weighted=counts['valu']+counts['alu']/8,
                  policy=builder.schedule_policy, policies=len(builder.schedule_stats))
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--full-policies', action='store_true')
    parser.add_argument('--pair-prefetch', action='store_true')
    parser.add_argument('--banks', type=int, choices=(1, 2), default=1)
    run(parser.parse_args())


if __name__ == '__main__':
    main()
