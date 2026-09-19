"""Test explicit zero-latency scratch WAR edges on frozen 928-cycle graph.

Only overlapping record vloads may share a cycle with their old parent/right
readers. RAW, WAW and memory dependencies remain ordinary strict edges.
The normal emission verifier is retained, then zero-lag metadata is separately
checked against logical access sets; physical provenance checks remain intact.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import types

REPO = Path(__file__).resolve().parents[1]
SOURCE_REF = '161c602'
sys.path.insert(0, str(REPO))
from tune_kernel import check
from verify_kernel import verify_emission, verify_words
from experiments.dataflow_check import verify_dataflow


def make_source(depths, full_policies=False):
    source = subprocess.run(['git', 'show', f'{SOURCE_REF}:perf_takehome.py'],
                            cwd=REPO, capture_output=True, text=True, check=True).stdout
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:100])
        source = source.replace(old, new, 1)
    if depths:
        replace('                                      record_stores, pending)\n                        node_address_reads.append(loaded)',
                '''                                      record_stores,
                                      (node_address_reads[-1] if node_address_reads else None)
                                      if depth in LANDING_WAR_DEPTHS else pending)
                        if depth in LANDING_WAR_DEPTHS:
                            ops[loaded]["same_cycle_deps"] = pending or []
                            ops[loaded]["prior_landing_load"] = node_address_reads[-1] if node_address_reads else None
                        node_address_reads.append(loaded)''')
        replace('            op["deps"] = [prefix[d] for d in op["deps"] if d is not None and d not in dead]',
                '''            op["deps"] = [prefix[d] for d in op["deps"] if d is not None and d not in dead]
            op["same_cycle_deps"] = [prefix[d] for d in op.get("same_cycle_deps", ())]
            if op.get("prior_landing_load") is not None:
                op["prior_landing_load"] = prefix[op["prior_landing_load"]]''')
        replace('            scheduled_count = 0\n            bundles = []',
                '            scheduled_count = 0\n            finished = set()\n            bundles = []')
        replace('                chosen = []\n                bundle = {}',
                '                chosen = []\n                forced_alu = []\n                bundle = {}')
        replace('                            and len(bundles) >= ALU_VECTOR_RESERVE_START):',
                '''                            and len(bundles) >= ALU_VECTOR_RESERVE_START
                            and len(forced_alu) <= SLOT_LIMITS["alu"] - VLEN):''')
        replace('                    selected = candidates[:capacity]\n                    del candidates[: len(selected)]',
                '''                    if engine == "load":
                        selected = []
                        for candidate in candidates:
                            if len(selected) == capacity:
                                break
                            needed = [d for d in ops[candidate].get("same_cycle_deps", ()) if d not in finished]
                            if not all(d in ready["alu"] for d in needed):
                                continue
                            combined = list(dict.fromkeys([*forced_alu, *needed]))
                            if len(combined) > SLOT_LIMITS["alu"]:
                                continue
                            forced_alu = combined
                            selected.append(candidate)
                        for candidate in selected:
                            candidates.remove(candidate)
                    elif engine == "alu":
                        assert len(forced_alu) <= capacity
                        for candidate in forced_alu:
                            candidates.remove(candidate)
                        selected = forced_alu + candidates[:capacity-len(forced_alu)]
                        del candidates[:capacity-len(forced_alu)]
                    else:
                        selected = candidates[:capacity]
                        del candidates[:len(selected)]''')
        replace('                scheduled_count += len(chosen)\n                for op_id in chosen:',
                '                scheduled_count += len(chosen)\n                finished.update(chosen)\n                for op_id in chosen:')
        replace('        self.offloaded_ops = set(self.lane_issue_cycles)',
                '''        for op_id, op in enumerate(ops):
            assert all(self.issue_first_cycles[op_id] >= self.issue_cycles[d]
                       for d in op.get("same_cycle_deps", ())), "Zero-lag WAR issued before old reader"
        self.offloaded_ops = set(self.lane_issue_cycles)''')
    if not full_policies:
        replace('        for policy in dict.fromkeys(policies):',
                '''        policies = ("fragment_adaptive_tail_hetero_360_240_240_220_900",
                    "fragment_adaptive_tail_hetero_360_220_140_140_920",
                    "fragment_adaptive_tail_hetero_360_220_140_140_900")
        for policy in dict.fromkeys(policies):''')
    return source


def verify_war_edges(builder):
    """Verify strict edges normally, then certify each explicit lag-zero edge."""
    verify_emission(builder)
    edge_count = shared_cycles = 0
    for op_id, op in enumerate(builder.operations):
        wars = op.get('same_cycle_deps', ())
        if not wars:
            continue
        assert op['engine'] == 'load' and op['slot'][0] == 'vload'
        assert len(wars) == 2
        prior = op['prior_landing_load']
        assert prior in op['deps'] and prior < op_id
        assert builder.operations[prior]['engine'] == 'load'
        current_reads, current_writes = builder.instruction_accesses('load', builder.logical_slots[op_id])
        prior_reads, prior_writes = builder.instruction_accesses('load', builder.logical_slots[prior])
        assert current_writes & prior_writes
        for old_id in wars:
            old = builder.operations[old_id]
            assert old['engine'] == 'alu' and prior in old['deps']
            old_reads, old_writes = builder.instruction_accesses('alu', builder.logical_slots[old_id])
            # A legal removed strict dependency is WAR only, never RAW/WAW.
            assert old_reads & current_writes
            assert not old_writes & (current_reads | current_writes)
            assert builder.issue_first_cycles[op_id] >= builder.issue_cycles[old_id]
            edge_count += 1
            shared_cycles += builder.issue_first_cycles[op_id] == builder.issue_cycles[old_id]
    verify_dataflow(builder)
    return dict(war_edges=edge_count, same_cycle_edges=shared_cycles)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--depths', type=int, nargs='*', default=[4, 6], choices=(4, 6))
    parser.add_argument('--full-policies', action='store_true')
    args = parser.parse_args()
    module = types.ModuleType('landing_overlap_probe')
    module.LANDING_WAR_DEPTHS = tuple(args.depths)
    exec(compile(make_source(args.depths, args.full_policies), '<landing_overlap_probe>', 'exec'), module.__dict__)
    class Builder(module.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)
        def allocate_node_lifetimes(self, ops, uses, reusable=()):
            self.logical_slots = [op['slot'] for op in ops]
            super().allocate_node_lifetimes(ops, uses, reusable)
    builder = Builder()
    result = dict(source_ref=SOURCE_REF, **vars(args))
    try:
        builder.build_kernel(10, 2047, 256, 16)
    except AssertionError as error:
        if str(error) != 'Out of scratch space':
            raise
        result.update(rejected='scratch', required=builder.scratch_ptr)
    else:
        result.update(verify_war_edges(builder))
        for seed in (123, 456, 789):
            check(builder, seed)
        verify_words(builder, 'full_word', [(i*0x9e3779b9)&0xffffffff for i in range(2047)],
                     [(i*0xabcdef01+0x80000000)&0xffffffff for i in range(256)])
        result.update(cycles=len(builder.instrs), scratch=builder.scratch_ptr,
                      verification='three frozen seeds, full-word workspace, exact emission, WAR edges, scratch provenance')
    physical = Counter()
    for bundle in builder.instrs:
        physical.update({engine:len(slots) for engine, slots in bundle.items()})
    result.update(slots=physical, weighted=physical['valu']+physical['alu']/8,
                  policy=builder.schedule_policy, schedule_stats=builder.schedule_stats)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
