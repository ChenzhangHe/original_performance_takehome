"""Bounded pipeline-feeding fairness within the pinned FMA-first scheduler.

Reserve the last VALU slot for an eligible binary that directly feeds FMA,
but only if six ready FMAs would otherwise take all slots. Optionally wait
until that binary has aged four cycles. This is a priority experiment, not
an instruction/dependency or engine-capacity change.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import types

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from experiments.iteration41_transfer import make_source as transfer_source, SOURCE_REF
from experiments import dataflow_check
from tune_kernel import check
from verify_kernel import verify_emission, verify_words, verify_workspace_addresses


def make_source(args):
    transfer_args = argparse.Namespace(root='none', inputs_flow=False,
                                       inputs_immediate=args.inputs_immediate,
                                       full_policies=args.full_policies)
    source = transfer_source(transfer_args)
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (old[:100], source.count(old))
        source = source.replace(old, new)
    replace('            remaining = dep_counts.copy()', '''            remaining = dep_counts.copy()
            ready_since = [0] * len(ops)''')
    replace('''                        if remaining[succ] == 0:
                            ready[ops[succ]["engine"]].append(succ)''', '''                        if remaining[succ] == 0:
                            ready_since[succ] = len(bundles)
                            ready[ops[succ]["engine"]].append(succ)''')
    if args.enabled:
        replace('                    capacity = SLOT_LIMITS[engine]', f'''                    if fragmented and engine == "valu" and len(candidates) > SLOT_LIMITS[engine]:
                        if all(ops[i]["slot"][0] == "multiply_add" for i in candidates[:SLOT_LIMITS[engine]]):
                            feeder = next((i for i in candidates[SLOT_LIMITS[engine]:]
                                           if ops[i]["slot"][0] not in ("multiply_add", "vbroadcast")
                                           and len(bundles)-ready_since[i] >= {args.age}
                                           and any(ops[s]["slot"][0] == "multiply_add" for s in successors[i])), None)
                            if feeder is not None:
                                candidates.remove(feeder)
                                candidates.insert(SLOT_LIMITS[engine]-1, feeder)
                    capacity = SLOT_LIMITS[engine]''')
    return source


def run(args):
    module = types.ModuleType('iteration41_fma_fairness')
    exec(compile(make_source(args), '<iteration41_fma_fairness>', 'exec'), module.__dict__)
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
        original = dataflow_check.accesses
        def accesses(engine, slot, lanes=None):
            if engine == 'flow' and slot[0] == 'add_imm':
                return [('w', 1, 0), ('r', 2, 0)]
            return original(engine, slot, lanes)
        stage = 'scratch_provenance'
        dataflow_check.accesses = accesses
        try:
            dataflow_check.verify_dataflow(builder)
        finally:
            dataflow_check.accesses = original
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
    parser.add_argument('--enabled', action='store_true')
    parser.add_argument('--age', type=int, choices=(0, 4), default=0)
    parser.add_argument('--inputs-immediate', action='store_true')
    parser.add_argument('--full-policies', action='store_true')
    run(parser.parse_args())


if __name__ == '__main__':
    main()
