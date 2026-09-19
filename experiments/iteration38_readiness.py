"""Bounded readiness experiments pinned to the accepted 941-cycle kernel.

No machine, slot capacities, or production files are modified. Every candidate
retains memory-store barriers and actual scratch hazards, checked independently.
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
from experiments.dataflow_check import verify_dataflow

SOURCE_REF = "34742f6253c7cb1b1b2a3ca664b6b5f2d9a29991"


def make_source(args):
    source = subprocess.run(["git", "show", f"{SOURCE_REF}:perf_takehome.py"],
                            cwd=REPO, check=True, capture_output=True, text=True).stdout
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:100])
        source = source.replace(old, new)
    if args.release_children:
        # The next parent hash reads only its input value, not copied children.
        # Children retain their real loaded-data deps and the buffer WAR chain.
        replace('val_ready = ([*node_loads, *all_copies] if BLOCKED_FUSE_PARENT_XOR else',
                'val_ready = (node_loads if BLOCKED_FUSE_PARENT_XOR else')
        replace('val_ready = [*node_loads, *pending]', 'val_ready = node_loads')
    if args.children_priority is not None:
        replace('''                        child = emit("alu", ("+", block_right+lane, read_buffer+2, readonly_zero), loaded)''',
                f'''                        emit_context["round"] = {args.children_priority}
                        child = emit("alu", ("+", block_right+lane, read_buffer+2, readonly_zero), loaded)
                        emit_context["round"] = round_no''')
    if args.hash_tail_depths is not None:
        old = 'self.compact_lane_tail and depth in (5, 6, 7, 8, 9)'
        assert source.count(old) == 2
        source = source.replace(old, f'self.compact_lane_tail and depth in {tuple(args.hash_tail_depths)!r}')
    if args.early_shallow:
        # A shallow copied child is independent of the later parent hash. Its
        # exact read buffer and WAR dependencies are unchanged; only priority.
        replace('''                            copied.extend(emit("alu", ("+", dest+lane, read_buffer+j, readonly_zero), loaded)
                                          for j, dest in enumerate((block_left, block_right), 1))''',
                '''                            emit_context["round"] = round_no-1
                            copied.extend(emit("alu", ("+", dest+lane, read_buffer+j, readonly_zero), loaded)
                                          for j, dest in enumerate((block_left, block_right), 1))
                            emit_context["round"] = round_no''')
    if args.setup_deadline_cap is not None:
        # Deep-table stores first feed round 6. Their old shared cap of 4
        # was inherited from a different table/cache layout.
        replace('op["round"] = min(4, needed[i])',
                f'op["round"] = min({args.setup_deadline_cap}, needed[i])')
    if args.finish_fragment_age:
        # A started binary vector can otherwise be starved by scalar chains.
        # Reserve slots only for its remaining lanes after a bounded age; its
        # consumers still unlock strictly after all eight lanes complete.
        replace('            partial = None  # (logical operation ID, first unissued lane)',
                '            partial = None  # (logical operation ID, first unissued lane)\n            partial_started = -1')
        replace('                    capacity = SLOT_LIMITS[engine]', f'''                    capacity = SLOT_LIMITS[engine]
                    if (fragmented and engine == "alu" and partial is not None
                            and len(bundles)-partial_started >= {args.finish_fragment_age}):
                        op_id, lane = partial
                        bundle.setdefault(f"alu_fragment_{{lane}}_{{VLEN}}", []).append(ops[op_id]["slot"])
                        chosen.append(op_id)
                        capacity -= VLEN-lane
                        partial = None''')
        replace('''                            ready["valu"].remove(op_id)
                            lane = 0''', '''                            ready["valu"].remove(op_id)
                            lane = 0
                            partial_started = len(bundles)''')
        replace('if (balanced and engine == "alu"',
                'if (balanced and engine == "alu" and capacity >= VLEN')
        replace('''                    capacity = (SLOT_LIMITS["alu"] - len(bundle.get("alu", ()))
                                - VLEN * len(bundle.get("alu_vector", ())))''', '''                    capacity = (SLOT_LIMITS["alu"] - len(bundle.get("alu", ()))
                                - VLEN * len(bundle.get("alu_vector", ()))
                                - sum((int(e.split("_")[-1])-int(e.split("_")[-2]))*len(slots)
                                      for e, slots in bundle.items() if e.startswith("alu_fragment_")))''')
    if not args.full_policies:
        replace('        for policy in dict.fromkeys(policies):', '''        policies = ("fragment_adaptive_tail_hetero_360_220_140_140_920",
                    "fragment_adaptive_tail_hetero_360_220_140_140_900")
        for policy in dict.fromkeys(policies):''')
    return source


def run(args):
    module = types.ModuleType('readiness_probe')
    exec(compile(make_source(args), '<readiness_probe>', 'exec'), module.__dict__)
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
        verify_dataflow(builder)
        for seed in (123, 456, 789):
            stage = f'frozen_seed_{seed}'
            check(builder, seed)
        stage = 'full_word_workspace'
        verify_words(builder, 'full_word', [(i*0x9e3779b9)&0xffffffff for i in range(2047)],
                     [(i*0xabcdef01+0x80000000)&0xffffffff for i in range(256)])
    except AssertionError as error:
        result.update(rejected=stage, detail=str(error), scratch=builder.scratch_ptr)
    else:
        counts = Counter(op['engine'] for op in builder.operations)
        result.update(cycles=len(builder.instrs), scratch=builder.scratch_ptr,
                      weighted=counts['valu']+counts['alu']/8, logical_slots=counts,
                      verification='3 frozen seeds, full-word workspace, exact emission, scratch provenance')
    if hasattr(builder, 'schedule_policy'):
        result['policy'] = builder.schedule_policy
        result['schedule_cycles'] = len(builder.instrs)
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full-policies', action='store_true')
    parser.add_argument('--release-children', action='store_true')
    parser.add_argument('--children-priority', type=int)
    parser.add_argument('--early-shallow', action='store_true')
    parser.add_argument('--hash-tail-depths', type=int, nargs='+')
    parser.add_argument('--setup-deadline-cap', type=int)
    parser.add_argument('--finish-fragment-age', type=int, default=0)
    run(parser.parse_args())


if __name__ == '__main__':
    main()
