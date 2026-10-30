"""Costed input-address graphs on the pinned 923-cycle kernel.

Compare independent flow addresses with short scalar pairs or vector blocks.
All addresses depend only on the memory layout; all tree and input values
are read by the emitted runtime program. No simulator/test changes.
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
from analyze_kernel import analyze
from experiments.dataflow_check import verify_dataflow
from tune_kernel import check
from verify_kernel import verify_emission, verify_words, verify_workspace_addresses

SOURCE_REF = 'd014c591f63298a9b00971decebd6cc004d1ce29'
MODES = ('independent', 'pairs', 'low_pairs', 'high_pairs', 'vector8')


def make_source(args):
    source = subprocess.check_output(['git', 'show', f'{SOURCE_REF}:perf_takehome.py'],
                                     cwd=REPO, text=True)
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (old[:100], source.count(old))
        source = source.replace(old, new)
    if getattr(args, 'direct_root', False):
        replace('''        for lane in range(7):
            self.add("alu", ("^", top_nodes + lane, top_nodes + lane, final_xor_const))
        root_value_encoded = self.alloc_scratch("root_value_encoded")
        self.add("alu", ("+", root_value_encoded, root_value, zero))''', '''        for lane in range(1, 7):
            self.add("alu", ("^", top_nodes + lane, top_nodes + lane, final_xor_const))
        root_value_encoded = self.alloc_scratch("root_value_encoded")
        self.add("alu", ("^", root_value_encoded, root_value, final_xor_const))''')
    if getattr(args, 'narrow_stores', False):
        replace('idx_ready, block_stores) for lane in range(VLEN)]',
                'idx_ready, block_stores[:4]) for lane in range(VLEN)]')
    if args.addresses == 'vector8':
        replace('        # Fold initialization into the same DAG as the kernel.',
                '''        input_addr_stride = vector_const(64, "input_addr_stride")

        # Fold initialization into the same DAG as the kernel.''')
        start = source.index('        for chunk_no in input_order:')
        end = source.index('        for chunk_no in range(chunk_count):', start)
        source = source[:start]+'''        # The top eight layout addresses are independent anchors. Each
        # subtraction generates the next eight scalar pointers at once.
        for chunk_no in range(chunk_count-1, chunk_count-VLEN-1, -1):
            emit_context.update(chunk=chunk_no, round=0, local_seq=0)
            input_addr_ready[chunk_no] = emit("flow", ("add_imm", input_addrs+chunk_no,
                                                     readonly_zero, inp_values_p+chunk_no*VLEN))
        for block in range(chunk_count-2*VLEN, -1, -VLEN):
            emit_context.update(chunk=block+VLEN-1, round=0, local_seq=0)
            ready = emit("valu", ("-", input_addrs+block, input_addrs+block+VLEN, input_addr_stride),
                         input_addr_ready[block+VLEN:block+2*VLEN])
            input_addr_ready[block:block+VLEN] = [ready]*VLEN

'''+source[end:]
    elif args.addresses != 'independent':
        condition = {'pairs': 'chunk_no % 2 == 0',
                     'low_pairs': 'chunk_no < 24 and chunk_no % 2 == 0',
                     'high_pairs': 'chunk_no >= 8 and chunk_no % 2 == 0'}[args.addresses]
        replace('''            if self.compact_input_immediate:
                # Spend initial flow capacity on independent layout addresses.''', f'''            if {condition}:
                ready = emit("alu", ("-", input_addrs+chunk_no, input_addrs+chunk_no+1, address_step),
                             input_addr_ready[chunk_no+1])
            elif self.compact_input_immediate:
                # Spend initial flow capacity on independent layout addresses.''')
    if not args.full_policies:
        replace('        for policy in dict.fromkeys(policies):', '''        for policy in (
                "fragment_adaptive_tail_hetero_360_240_240_220_900",
                "fragment_adaptive_tail_hetero_360_220_140_140_920",
                "fragment_adaptive_tail_hetero_360_220_140_140_900"):
'''.rstrip())
    return source


def build(args):
    module = types.ModuleType('iteration42_addresses')
    exec(compile(make_source(args), '<iteration42_addresses>', 'exec'), module.__dict__)
    module.COMPACT_DEPTH3_GATHER_CHUNKS = args.exchange_groups
    class Builder(module.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)
        def allocate_node_lifetimes(self, ops, uses, reusable=()):
            self.logical_slots = [op['slot'] for op in ops]
            super().allocate_node_lifetimes(ops, uses, reusable)
    result = dict(source_ref=SOURCE_REF, **vars(args))
    builder = Builder()
    stage = 'build'
    try:
        builder.build_kernel(10, 2047, 256, 16)
        stage = 'emission'; verify_emission(builder)
        stage = 'scratch_provenance'; verify_dataflow(builder)
        stage = 'workspace_addresses'; verify_workspace_addresses(builder)
        for seed in (123, 456, 789):
            stage = f'frozen_seed_{seed}'; check(builder, seed)
        stage = 'full_word_workspace'
        verify_words(builder, 'full_word', [(i*0x9e3779b9)&0xffffffff for i in range(2047)],
                     [(i*0xabcdef01+0x80000000)&0xffffffff for i in range(256)])
    except (AssertionError, ValueError) as error:
        result.update(rejected=stage, detail=str(error), scratch=builder.scratch_ptr)
    else:
        report = analyze(builder)
        slots = Counter()
        for bundle in builder.instrs:
            slots.update({engine: len(items) for engine, items in bundle.items()})
        result.update(cycles=len(builder.instrs), scratch=builder.scratch_ptr,
                      slots=slots, weighted=report['compute']['weighted_equivalents'],
                      resource_tail_bounds=report['resource_tail_bounds'],
                      lookup=report['lookup_load'], policy=builder.schedule_policy,
                      policies=len(builder.schedule_stats),
                      verification='emission/provenance/workspace, 3 frozen seeds, full-word memory')
    print(json.dumps(result), flush=True)
    return builder, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--addresses', choices=MODES, default='independent')
    parser.add_argument('--exchange-groups', type=int, choices=tuple(range(16, 22)), default=16)
    parser.add_argument('--full-policies', action='store_true')
    parser.add_argument('--direct-root', action='store_true')
    parser.add_argument('--narrow-stores', action='store_true')
    build(parser.parse_args())


if __name__ == '__main__':
    main()
