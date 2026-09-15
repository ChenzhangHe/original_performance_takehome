"""Let scalar parity/address/gather consumers advance lane by lane.

Unlike ordinary fragment issue, selected terminal hash XORs are eight explicit
scalar producers, each unlocking only its matching parity lane. Other barriers
are unchanged. No machine capacities or dependency verification are relaxed.
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

SOURCE_REF = '2d5bc94853f290e21856da7d6705b585078a39f9'


def make_source(depths, groups, full_policies, lane_consumers=True, scalar_shift=False):
    source = subprocess.run(['git', 'show', f'{SOURCE_REF}:perf_takehome.py'], cwd=REPO,
                            check=True, capture_output=True, text=True).stdout
    old = '''                            val_ready = emit(
                                "valu",
                                ("^", chunk_val, chunk_val, chunk_tmp2),
                                val_ready,
                                shifted,
                            )'''
    assert source.count(old) == 1
    source = source.replace(old, f'''                            if depth in {tuple(depths)!r} and chunk_no >= chunk_count-{groups}:
                                val_ready = [emit("alu", ("^", chunk_val+lane, chunk_val+lane, chunk_tmp2+lane),
                                                  val_ready, shifted[lane] if isinstance(shifted, list) else shifted) for lane in range(VLEN)]
                            else:
''' + '\n'.join('    '+line for line in old.splitlines()))
    if scalar_shift:
        old = '''                        shifted = emit(
                            "valu",
                            (">>", chunk_tmp2, chunk_val, hash_constants[16]),
                            val_ready,
                        )'''
        assert source.count(old) == 1
        source = source.replace(old, f'''                        if depth in {tuple(depths)!r} and chunk_no >= chunk_count-{groups}:
                            shifted = [emit("alu", (">>", chunk_tmp2+lane, chunk_val+lane, hash_constants[16]+lane),
                                            val_ready) for lane in range(VLEN)]
                        else:
''' + '\n'.join('    '+line for line in old.splitlines()))
    if lane_consumers:
        old = '''                parity = emit_scalar_rhs(
                    "&", parity_dest, chunk_val, one, val_ready
                )'''
        assert source.count(old) == 1
        source = source.replace(old, f'''                if depth in {tuple(depths)!r} and chunk_no >= chunk_count-{groups}:
                    parity = [emit("alu", ("&", parity_dest+lane, chunk_val+lane, one),
                                   val_ready[lane]) for lane in range(VLEN)]
                else:
''' + '\n'.join('    '+line for line in old.splitlines()))
    if not full_policies:
        old = '        for policy in dict.fromkeys(policies):'
        assert source.count(old) == 1
        source = source.replace(old, '''        policies = ("fragment_adaptive_tail_hetero_360_220_140_140_920",
                    "fragment_adaptive_tail_hetero_360_220_140_140_900")
        for policy in dict.fromkeys(policies):''')
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--depths', nargs='*', type=int, default=[5, 6, 7, 8, 9])
    parser.add_argument('--groups', type=int, default=32)
    parser.add_argument('--full-policies', action='store_true')
    parser.add_argument('--whole-barrier', action='store_true', help='Control: same scalar issue, old all-lane parity barrier')
    parser.add_argument('--scalar-shift', action='store_true', help='Also release terminal shift results lane by lane')
    args = parser.parse_args()
    assert set(args.depths) <= set(range(5, 10)) and 0 <= args.groups <= 32
    module = types.ModuleType('lane_tail_probe')
    exec(compile(make_source(args.depths, args.groups, args.full_policies, not args.whole_barrier, args.scalar_shift),
                 '<lane_tail_probe>', 'exec'), module.__dict__)
    class Builder(module.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)
    builder = Builder()
    result = dict(source_ref=SOURCE_REF, **vars(args))
    try:
        builder.build_kernel(10, 2047, 256, 16)
    except AssertionError as error:
        if str(error) != 'Out of scratch space':
            raise
        print(json.dumps(dict(**result, rejected='scratch', required=builder.scratch_ptr)))
        return
    verify_emission(builder)
    for seed in (123, 456, 789):
        check(builder, seed)
    verify_words(builder, 'full_word', [(i*0x9e3779b9)&0xffffffff for i in range(2047)],
                 [(i*0xabcdef01+0x80000000)&0xffffffff for i in range(256)])
    slots = Counter()
    for bundle in builder.instrs:
        slots.update({engine: len(items) for engine, items in bundle.items()})
    lookups = [builder.issue_cycles[i] for i, op in enumerate(builder.operations)
               if not op.get('is_setup', False) and op['round'] > 0 and op['engine'] == 'load']
    print(json.dumps(dict(**result, cycles=len(builder.instrs), scratch=builder.scratch_ptr,
                          slots=slots, weighted=slots['valu']+slots['alu']/8,
                          first_lookup=min(lookups), last_lookup=max(lookups),
                          policy=builder.schedule_policy,
                          verification='three frozen seeds, full-word workspace, exact emission')))


if __name__ == '__main__':
    main()
