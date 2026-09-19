"""Release gathered input XORs lane by lane on the fixed 924-cycle graph.

This moves vector/scalar issue and, optionally, the raw-node representation
XOR; it does not reduce the number of arithmetic equivalents. All original
load/address dependencies and final hash barriers remain.
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
from verify_kernel import verify_emission, verify_words, verify_workspace_addresses

SOURCE_REF = '109610180033baa744a0fe80a2501e1ba2f73b00'


def make_source(args):
    source = subprocess.check_output(['git', 'show', f'{SOURCE_REF}:perf_takehome.py'],
                                     cwd=REPO, text=True)
    start = source.index('                else:\n                    encoded_node =')
    end = source.index('\n                if cached_lookup:', start)
    section = source[start:end]
    eligible = {'final': 'round_no == rounds - 1',
                'raw': 'not encoded_node',
                'all': 'True',
                'none': 'False'}[args.scope]
    eligible = f'blocked_lookup and ({eligible}) and chunk_no < {args.groups}'
    if args.early_decode:
        old = '                    if not encoded_node:\n                        node_loads = ['
        new = f'''                    if ({eligible}) and not encoded_node:
                        # Retain the previous parity/address read barrier:
                        # decoding the value may run independently of gathers.
                        val_ready = emit("valu", ("^", chunk_val, chunk_val, final_xor_vec),
                                         val_ready, idx_ready)
                    elif not encoded_node:
                        node_loads = ['''
        assert section.count(old) == 1
        section = section.replace(old, new)
    old = '''                    val_ready = emit(
                        "valu",
                        ("^", chunk_val, chunk_val, chunk_node),
                        val_ready,
                        node_loads,
                    )'''
    new = f'''                    if {eligible}:
                        previous_value = val_ready
                        val_ready = [emit("alu", ("^", chunk_val+lane, chunk_val+lane, chunk_node+lane),
                                          previous_value[lane] if isinstance(previous_value, list) else previous_value,
                                          node_loads[lane]) for lane in range(VLEN)]
                    else:
                        val_ready = emit(
                            "valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, node_loads)
'''
    assert section.count(old) == 1
    section = section.replace(old, new)
    source = source[:start]+section+source[end:]
    if not args.full_policies:
        old = '        for policy in dict.fromkeys(policies):'
        new = '''        for policy in (
                "fragment_adaptive_tail_hetero_360_240_240_220_900",
                "fragment_adaptive_tail_hetero_360_220_140_140_920",
                "fragment_adaptive_tail_hetero_360_220_140_140_900"):
'''.rstrip()
        assert source.count(old) == 1
        source = source.replace(old, new)
    return source


def run(args):
    module = types.ModuleType('iteration41_gather_fusion')
    exec(compile(make_source(args), '<iteration41_gather_fusion>', 'exec'), module.__dict__)
    class Builder(module.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)
        def allocate_node_lifetimes(self, ops, uses, reusable=()):
            self.logical_slots = [op['slot'] for op in ops]
            super().allocate_node_lifetimes(ops, uses, reusable)
    builder = Builder()
    stage = 'build'
    result = dict(source_ref=SOURCE_REF, **vars(args))
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
        counts = Counter()
        for bundle in builder.instrs:
            counts.update({engine: len(slots) for engine, slots in bundle.items()})
        result.update(cycles=len(builder.instrs), scratch=builder.scratch_ptr,
                      slots=counts, weighted=counts['valu']+counts['alu']/8,
                      policy=builder.schedule_policy, policies=len(builder.schedule_stats),
                      verification='emission/provenance/workspace, 3 frozen seeds, full-word memory')
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=('none', 'final', 'raw', 'all'), default='final')
    parser.add_argument('--groups', type=int, default=32)
    parser.add_argument('--early-decode', action='store_true')
    parser.add_argument('--full-policies', action='store_true')
    run(parser.parse_args())


if __name__ == '__main__':
    main()
