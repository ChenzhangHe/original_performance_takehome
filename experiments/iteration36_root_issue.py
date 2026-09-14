"""Bounded root setup/issue probes on 955, without a scheduling-policy sweep."""
import argparse
from collections import Counter
from pathlib import Path
import json
import subprocess
import sys
import types

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tune_kernel import check
from verify_kernel import verify_emission

p = argparse.ArgumentParser()
p.add_argument('--fuse', action='store_true')
p.add_argument('--vectors', type=int, choices=(0, 1, 2), default=0)
p.add_argument('--tail-start', type=int, default=900)
args = p.parse_args()
source = subprocess.run(['git', 'show', '3d24666:perf_takehome.py'], cwd=REPO,
                        check=True, capture_output=True, text=True).stdout
def replace(old, new):
    global source
    assert old in source, old[:100]
    source = source.replace(old, new, 1)
if args.fuse:
    replace('        for lane in range(7):', '        for lane in range(1, 7):')
    replace('self.add("alu", ("+", root_value_encoded, root_value, zero))',
            'self.add("alu", ("^", root_value_encoded, root_value, final_xor_const))')
if args.vectors:
    replace('self.alloc_scratch("root_value_copy")', 'self.alloc_scratch("root_value_copy", VLEN)')
    replace('self.add("alu", ("+", root_value_copy, root_value, zero))',
            'self.add("valu", ("vbroadcast", root_value_copy, root_value))')
    if args.vectors == 2:
        replace('self.alloc_scratch("root_value_encoded")', 'self.alloc_scratch("root_value_encoded", VLEN)')
        line = ('self.add("alu", ("^", root_value_encoded, root_value, final_xor_const))' if args.fuse else
                'self.add("alu", ("+", root_value_encoded, root_value, zero))')
        replace(line, line+'\n        self.add("valu", ("vbroadcast", root_value_encoded, root_value_encoded))')
    old = '''                    val_ready = emit_scalar_rhs(
                        "^", chunk_val, chunk_val, root_round_value, val_ready
                    )'''
    replace(old, f'''                    if round_no == 0 or {args.vectors} == 2:
                        val_ready = emit("valu", ("^", chunk_val, chunk_val, root_round_value), val_ready)
                    else:
                        val_ready = emit_scalar_rhs("^", chunk_val, chunk_val, root_round_value, val_ready)''')
m = types.ModuleType('root_probe')
source = source.replace('"adaptive_tail_hetero_360_220_140_140_900"',
                        f'"adaptive_tail_hetero_360_220_140_140_{args.tail_start}"', 1)
exec(compile(source, '<root_probe>', 'exec'), m.__dict__)
class Builder(m.KernelBuilder):
    def schedule(self, ops):
        self.operations = ops
        super().schedule(ops)
b = Builder()
b.build_kernel(10,2047,256,16)
verify_emission(b)
for seed in (123,456,789):
    check(b,seed)
slots = Counter()
for bundle in b.instrs:
    slots.update({engine:len(items) for engine,items in bundle.items()})
times = [b.issue_cycles[i] for i,op in enumerate(b.operations)
         if not op.get('is_setup', False) and op['round']>0 and op['engine']=='load'
         and op['slot'][0] in ('load_offset','vload')]
print(json.dumps(dict(**vars(args), cycles=len(b.instrs), scratch=b.scratch_ptr,
                      slots=slots, weighted=slots['valu']+slots['alu']/8,
                      first_lookup=min(times), last_lookup=max(times), policy=b.schedule_policy)))
