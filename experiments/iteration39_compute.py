"""Bounded arithmetic work exchange, pinned to the accepted 928-cycle source.

Flow add_imm is part of the original ISA. Move only named setup/input scalar
address chains to that engine; no instruction semantics or limits change.
The provenance helper is locally extended to declare add_imm's one scratch
read and write. Machine correctness still uses the independent frozen model.
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
from verify_kernel import verify_emission, verify_words
from experiments import dataflow_check

SOURCE_REF = '161c60200be0aa071532f6784817a5b562e9c2c8'


def make_source(args):
    source = subprocess.check_output(['git', 'show', f'{SOURCE_REF}:perf_takehome.py'], cwd=REPO, text=True)
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:100])
        source = source.replace(old, new)
    if args.inputs:
        replace('''                ready = emit("alu", ("-" if reverse_inputs else "+", input_addrs + chunk_no,
                                      input_addrs + neighbor, address_step),
                             input_addr_ready[neighbor])''', f'''                if chunk_no >= chunk_count-{args.inputs}:
                    ready = emit("flow", ("add_imm", input_addrs+chunk_no, input_addrs+neighbor,
                                          -VLEN if reverse_inputs else VLEN), input_addr_ready[neighbor])
                else:
                    ready = emit("alu", ("-" if reverse_inputs else "+", input_addrs+chunk_no,
                                         input_addrs+neighbor, address_step), input_addr_ready[neighbor])''')
    if args.setup:
        replace('''        setup_ops, setup_writers = self.pack_setup(''', f'''        moved = 0
        scalar_values = {{address: value for value, address in self.const_map.items()}}
        for instr in self.instrs:
            for engine, slots in list(instr.items()):
                slot = slots[0]
                if (moved < {args.setup} and engine == "alu" and slot[0] == "+"
                        and slot[1] == slot[2] and slot[3] in scalar_values
                        and scalar_values[slot[3]] in (8, 16)):
                    del instr[engine]
                    instr["flow"] = [("add_imm", slot[1], slot[2], scalar_values[slot[3]])]
                    moved += 1
        setup_ops, setup_writers = self.pack_setup(''')
    if args.setup_loads:
        replace('''        setup_ops, setup_writers = self.pack_setup(''', f'''        moved = 0
        known = {{}}
        for instr in self.instrs:
            for engine, slots in list(instr.items()):
                slot = slots[0]
                if engine == "load" and slot[0] == "const":
                    known[slot[1]] = slot[2]
                elif (engine == "alu" and slot[0] == "+" and slot[1] == slot[2]
                      and slot[2] in known and slot[3] in known):
                    value = (known[slot[2]]+known[slot[3]]) & 0xffffffff
                    if moved < {args.setup_loads} and known[slot[3]] in (8, 16):
                        del instr[engine]
                        instr["load"] = [("const", slot[1], value)]
                        moved += 1
                    known[slot[1]] = value
                else:
                    reads, writes = self.instruction_accesses(engine, slot)
                    for address in writes:
                        known.pop(address, None)
        setup_ops, setup_writers = self.pack_setup(''')
    if args.root_copy:
        replace('''        for lane in range(7):
            self.add("alu", ("^", top_nodes + lane, top_nodes + lane, final_xor_const))
        root_value_encoded = self.alloc_scratch("root_value_encoded")
        self.add("alu", ("+", root_value_encoded, root_value, zero))''', '''        for lane in range(1, 7):
            self.add("alu", ("^", top_nodes + lane, top_nodes + lane, final_xor_const))
        root_value_encoded = self.alloc_scratch("root_value_encoded")
        self.add("alu", ("^", root_value_encoded, root_value, final_xor_const))''')
    if not args.full_policies:
        replace('        for policy in dict.fromkeys(policies):', '''        for policy in (
                "fragment_adaptive_tail_hetero_360_240_240_220_900",
                "fragment_adaptive_tail_hetero_360_220_140_140_920",
                "fragment_adaptive_tail_hetero_360_220_140_140_900"):
'''.rstrip())
    return source


def run(args):
    module = types.ModuleType('iteration39_compute_probe')
    exec(compile(make_source(args), '<iteration39_compute_probe>', 'exec'), module.__dict__)
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
    if hasattr(builder, 'operations'):
        slots = Counter()
        for bundle in builder.instrs:
            slots.update({engine:len(items) for engine, items in bundle.items()})
        result.update(slots=slots, weighted=slots['valu']+slots['alu']/8)
    if hasattr(builder, 'schedule_policy'):
        result.update(policy=builder.schedule_policy, schedule_cycles=len(builder.instrs))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full-policies', action='store_true')
    parser.add_argument('--inputs', type=int, default=0, choices=(0, 8, 16, 24, 32))
    parser.add_argument('--setup', type=int, default=0, choices=(0, 8, 16, 24))
    parser.add_argument('--setup-loads', type=int, default=0, choices=(0, 8, 16))
    parser.add_argument('--root-copy', action='store_true')
    run(parser.parse_args())
