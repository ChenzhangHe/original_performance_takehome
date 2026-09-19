"""Exact on/off integration and add_imm provenance checks for iteration41."""
from argparse import Namespace
from collections import Counter
import json
import types

from iteration41_transfer import make_source, SOURCE_REF
import perf_takehome as production
from experiments.dataflow_check import accesses, verify_dataflow
from tune_kernel import check
from verify_kernel import verify_emission, verify_words, verify_workspace_addresses


def check_add_imm_provenance():
    assert accesses('flow', ('add_imm', 100, 101, 0xFFFFFFFF)) == [('w', 1, 0), ('r', 2, 0)]
    logical = [('const', 40, 12), ('add_imm', 41, 40, 90), ('+', 42, 41, 40)]
    physical = [('const', 3, 12), ('add_imm', 4, 3, 90), ('+', 5, 4, 3)]
    engines = ('load', 'flow', 'alu')
    fixture = Namespace(operations=[dict(engine=e, slot=s, round=0) for e,s in zip(engines,physical)],
                        logical_slots=logical, instrs=[{e:[s]} for e,s in zip(engines,physical)],
                        offloaded_ops=set(), issue_cycles={0:0, 1:1, 2:2})
    verify_dataflow(fixture)
    for bad in (('add_imm', 4, 6, 90), ('add_imm', 6, 3, 90)):
        fixture.operations[1]['slot'] = bad
        try:
            verify_dataflow(fixture)
        except AssertionError as error:
            assert error.args[0][0] == 'scratch_read_provenance'
        else:
            raise AssertionError('Incorrect add_imm source/destination was accepted')
    fixture.operations[1]['slot'] = physical[1]
    verify_dataflow(fixture)


def build(module):
    class Builder(module.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)
        def allocate_node_lifetimes(self, ops, uses, reusable=()):
            self.logical_slots = [op['slot'] for op in ops]
            super().allocate_node_lifetimes(ops, uses, reusable)
    builder = Builder()
    builder.build_kernel(10, 2047, 256, 16)
    zero = builder.scratch['positive_zero']
    assert all(zero not in builder.instruction_accesses(op['engine'], op['slot'])[1]
               for op in builder.operations), 'Readonly zero was physically overwritten'
    assert all(zero not in builder.instruction_accesses(op['engine'], slot)[1]
               for op,slot in zip(builder.operations, builder.logical_slots)), 'Readonly zero was logically overwritten'
    verify_emission(builder)
    verify_dataflow(builder)
    verify_workspace_addresses(builder)
    for seed in (123, 456, 789):
        check(builder, seed)
    verify_words(builder, 'full_word', [(i*0x9e3779b9)&0xffffffff for i in range(2047)],
                 [(i*0xabcdef01+0x80000000)&0xffffffff for i in range(256)])
    return builder


def run():
    check_add_imm_provenance()
    results = []
    for enabled in (True, False):
        args = Namespace(full_policies=True, root='none', inputs_flow=False, inputs_immediate=enabled)
        pinned = types.ModuleType('pinned')
        exec(compile(make_source(args), '<pinned>', 'exec'), pinned.__dict__)
        reference = build(pinned)
        production.COMPACT_INPUT_IMMEDIATE = enabled
        integrated = build(production)
        for name in ('instrs', 'logical_slots', 'issue_cycles', 'issue_first_cycles',
                     'lane_issue_cycles', 'schedule_stats', 'scratch_ptr'):
            assert getattr(integrated, name) == getattr(reference, name), name
        assert [op['deps'] for op in integrated.operations] == [op['deps'] for op in reference.operations]
        assert len(integrated.instrs) == (923 if enabled else 924)
        slots = Counter()
        for bundle in integrated.instrs:
            slots.update({engine: len(items) for engine, items in bundle.items()})
        flow = [c for c,b in enumerate(integrated.instrs) if b.get('flow')]
        lookup = [integrated.issue_cycles[i] for i,op in enumerate(integrated.operations)
                  if not op.get('is_setup', False) and op['round']>0 and op['engine']=='load']
        results.append(dict(enabled=enabled, cycles=len(integrated.instrs), scratch=integrated.scratch_ptr,
                            policy=integrated.schedule_policy, policies=len(integrated.schedule_stats),
                            exact_instructions_logical_slots_deps_first_last_lane_issues='pass',
                            frozen_seeds=[123,456,789], full_word_workspace='pass',
                            emission_provenance='pass', readonly_zero_never_written='pass', slots=slots,
                            weighted=slots['valu']+slots['alu']/8,
                            flow_first_last=[min(flow),max(flow)], lookup_first_last=[min(lookup),max(lookup)]))
    production.COMPACT_INPUT_IMMEDIATE = True
    print(json.dumps(dict(source_ref=SOURCE_REF, add_imm_provenance_selftest='pass', results=results), indent=2))


if __name__ == '__main__':
    run()
