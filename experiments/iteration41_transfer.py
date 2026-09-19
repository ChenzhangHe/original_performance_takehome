"""Costed setup-transfer probes pinned to the accepted 924-cycle kernel.

Root aliasing removes two scalar copies, adds six static scratch words and
does not add loads. Input flow transfer removes24 ALU operations and adds24
original-ISA flow.add_imm operations; test only the complete chain family.
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
from tune_kernel import check
from verify_kernel import verify_emission, verify_words, verify_workspace_addresses
from experiments import dataflow_check

SOURCE_REF = '109610180033baa744a0fe80a2501e1ba2f73b00'


def make_source(args):
    source = subprocess.check_output(['git', 'show', f'{SOURCE_REF}:perf_takehome.py'], cwd=REPO, text=True)
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:100])
        source = source.replace(old, new)
    if args.root == 'direct':
        replace('''        for lane in range(7):
            self.add("alu", ("^", top_nodes + lane, top_nodes + lane, final_xor_const))
        root_value_encoded = self.alloc_scratch("root_value_encoded")
        self.add("alu", ("+", root_value_encoded, root_value, zero))''', '''        for lane in range(1, 7):
            self.add("alu", ("^", top_nodes + lane, top_nodes + lane, final_xor_const))
        root_value_encoded = self.alloc_scratch("root_value_encoded")
        self.add("alu", ("^", root_value_encoded, root_value, final_xor_const))''')
    elif args.root == 'alias':
        replace('''        root_value_copy = self.alloc_scratch("root_value_copy")
        zero = self.alloc_scratch("zero")  # Scratch starts zeroed.
        self.add("alu", ("+", root_value_copy, root_value, zero))
        for lane in range(7):
            self.add("alu", ("^", top_nodes + lane, top_nodes + lane, final_xor_const))
        root_value_encoded = self.alloc_scratch("root_value_encoded")
        self.add("alu", ("+", root_value_encoded, root_value, zero))''', '''        root_value_copy = root_value
        zero = self.alloc_scratch("zero")
        for lane in range(1, 7):
            self.add("alu", ("^", top_nodes + lane, top_nodes + lane, final_xor_const))
        root_value_encoded = top_nodes+7
        self.add("alu", ("^", root_value_encoded, root_value, final_xor_const))''')
        replace('''        if LOOKUP_DEPTH >= 3:
            depth3_addr = self.scratch_const''', '''        if LOOKUP_DEPTH >= 3:
            top_nodes = self.alloc_scratch("depth3_setup_nodes", VLEN)
            depth3_addr = self.scratch_const''')
    if args.inputs_flow:
        replace('''                ready = emit("alu", ("-" if reverse_inputs else "+", input_addrs + chunk_no,
                                      input_addrs + neighbor, address_step),
                             input_addr_ready[neighbor])''', '''                ready = emit("flow", ("add_imm", input_addrs+chunk_no, input_addrs+neighbor,
                                      -VLEN if reverse_inputs else VLEN), input_addr_ready[neighbor])''')
    if args.inputs_immediate:
        replace('''            if anchor:
                ready = emit("load", ("const", input_addrs + chunk_no,
                                      inp_values_p + chunk_no * VLEN))
            else:
                neighbor = chunk_no + 1 if reverse_inputs else chunk_no - 1
                ready = emit("alu", ("-" if reverse_inputs else "+", input_addrs + chunk_no,
                                      input_addrs + neighbor, address_step),
                             input_addr_ready[neighbor])''', '''            ready = emit("flow", ("add_imm", input_addrs+chunk_no, readonly_zero,
                                  inp_values_p+chunk_no*VLEN))''')
    if getattr(args, 'anchor_immediate', False):
        replace('''                ready = emit("load", ("const", input_addrs + chunk_no,
                                      inp_values_p + chunk_no * VLEN))''', '''                ready = emit("flow", ("add_imm", input_addrs+chunk_no, readonly_zero,
                                      inp_values_p+chunk_no*VLEN))''')
    if not args.full_policies:
        replace('        for policy in dict.fromkeys(policies):', '''        for policy in (
                "fragment_adaptive_tail_hetero_360_240_240_220_900",
                "fragment_adaptive_tail_hetero_360_220_140_140_920",
                "fragment_adaptive_tail_hetero_360_220_140_140_900"):
'''.rstrip())
    return source


def run(args):
    module = types.ModuleType('iteration41_transfer_probe')
    exec(compile(make_source(args), '<iteration41_transfer_probe>', 'exec'), module.__dict__)
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
        original = dataflow_check.accesses
        def accesses(engine, slot, lanes=None):
            if engine == 'flow' and slot[0] == 'add_imm':
                return [('w', 1, 0), ('r', 2, 0)]
            return original(engine, slot, lanes)
        dataflow_check.accesses = accesses
        try:
            dataflow_check.verify_dataflow(builder)
        finally:
            dataflow_check.accesses = original
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
        result.update(policy=builder.schedule_policy, schedule_cycles=len(builder.instrs),
                      slots=slots, weighted=slots['valu']+slots['alu']/8,
                      policy_count=len(builder.schedule_stats),
                      top_policies=sorted(builder.schedule_stats.items(), key=lambda x:x[1])[:5])
        if 'cycles' in result:
            spans = {}
            for engine in ('load', 'alu', 'valu', 'flow', 'store'):
                times = [cycle for cycle, bundle in enumerate(builder.instrs) if bundle.get(engine)]
                spans[engine] = [min(times), max(times)]
            lookup = [builder.issue_cycles[i] for i,op in enumerate(builder.operations)
                      if not op.get('is_setup', False) and op['round']>0 and op['engine']=='load']
            result.update(engine_spans=spans, lookup_first_last=[min(lookup),max(lookup)])
    print(json.dumps(result), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full-policies', action='store_true')
    parser.add_argument('--root', choices=('none', 'direct', 'alias'), default='none')
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('--inputs-flow', action='store_true')
    inputs.add_argument('--inputs-immediate', action='store_true')
    inputs.add_argument('--anchor-immediate', action='store_true')
    run(parser.parse_args())
