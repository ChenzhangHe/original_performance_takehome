"""Exact integration equivalence for the pinned iteration40 FMA priority probe."""
from argparse import Namespace
from collections import Counter
import json
import types

from iteration40_balance import make_source, SOURCE_REF
import perf_takehome as production
from experiments.dataflow_check import verify_dataflow
from tune_kernel import check
from verify_kernel import verify_emission, verify_words, verify_workspace_addresses


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
    verify_emission(builder)
    verify_dataflow(builder)
    verify_workspace_addresses(builder)
    for seed in (123, 456, 789):
        check(builder, seed)
    verify_words(builder, 'full_word', [(i*0x9e3779b9)&0xffffffff for i in range(2047)],
                 [(i*0xabcdef01+0x80000000)&0xffffffff for i in range(256)])
    return builder


def run():
    # Keep this historical on/off comparison on the pre-iteration41 graph.
    original_input_immediate = production.COMPACT_INPUT_IMMEDIATE
    production.COMPACT_INPUT_IMMEDIATE = False
    results = []
    for enabled in (True, False):
        args = Namespace(full_policies=True, age=0, choose='priority', valu_fma=enabled,
                         fma_mode='only_fma', partial_priority=False, no_partial_after=0,
                         shallow_stores=False, scalar_broadcast='none')
        pinned = types.ModuleType('pinned')
        exec(compile(make_source(args), '<pinned>', 'exec'), pinned.__dict__)
        reference = build(pinned)
        production.COMPACT_FMA_PRIORITY = enabled
        integrated = build(production)
        assert integrated.instrs == reference.instrs
        assert integrated.logical_slots == reference.logical_slots
        assert [op['deps'] for op in integrated.operations] == [op['deps'] for op in reference.operations]
        assert integrated.issue_cycles == reference.issue_cycles
        assert integrated.issue_first_cycles == reference.issue_first_cycles
        assert integrated.lane_issue_cycles == reference.lane_issue_cycles
        assert integrated.schedule_stats == reference.schedule_stats
        assert integrated.scratch_ptr == reference.scratch_ptr
        slots = Counter()
        for bundle in integrated.instrs:
            slots.update({engine: len(items) for engine, items in bundle.items()})
        flow = [c for c,b in enumerate(integrated.instrs) if b.get('flow')]
        lookup = [integrated.issue_cycles[i] for i,op in enumerate(integrated.operations)
                  if not op.get('is_setup', False) and op['round']>0 and op['engine']=='load']
        results.append(dict(enabled=enabled, cycles=len(integrated.instrs), scratch=integrated.scratch_ptr,
                            policy=integrated.schedule_policy, policies=len(integrated.schedule_stats),
                            exact_instructions_logical_slots_deps_lane_issues='pass',
                            frozen_seeds=[123,456,789], full_word_workspace='pass',
                            emission_provenance='pass', slots=slots,
                            weighted=slots['valu']+slots['alu']/8,
                            flow_first_last=[min(flow),max(flow)], lookup_first_last=[min(lookup),max(lookup)],
                            offloads=len(integrated.offloaded_ops),
                            partial_max=max(max(x)-min(x) for x in integrated.lane_issue_cycles.values())))
    production.COMPACT_FMA_PRIORITY = True
    production.COMPACT_INPUT_IMMEDIATE = original_input_immediate
    print(json.dumps(dict(source_ref=SOURCE_REF, results=results), indent=2))


if __name__ == '__main__':
    run()
