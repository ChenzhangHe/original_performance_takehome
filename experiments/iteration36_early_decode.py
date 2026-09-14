"""Move the representation XOR off the raw-gather dependency chain."""
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
from verify_kernel import verify_emission, verify_words

p = argparse.ArgumentParser()
p.add_argument('--first', type=int, default=6)
p.add_argument('--last', type=int, default=10)
p.add_argument('--scalar', action='store_true')
args = p.parse_args()
source = subprocess.run(['git', 'show', '3d24666:perf_takehome.py'], cwd=REPO,
                        check=True, capture_output=True, text=True).stdout
start = source.index('                    if not encoded_node:\n                        node_loads = [')
end = source.index('\n                    val_ready = emit(',start)
original = source[start:end]
original = original.replace('                    if not encoded_node:', '                    elif not encoded_node:',1)
source = source[:start]+f'''                    if blocked_lookup and not encoded_node and {args.first} <= depth <= {args.last}:
                        # Previous encoded parity must be consumed before
                        # decoding the value. This is independent of the
                        # current node loads, removing their XOR-C latency.
                        val_ready = {'emit_scalar_vector("^", chunk_val, chunk_val, final_xor_vec, val_ready, idx_ready)' if args.scalar else 'emit("valu", ("^", chunk_val, chunk_val, final_xor_vec), val_ready, idx_ready)'}
'''+original+source[end:]
m = types.ModuleType('early_decode')
exec(compile(source,'<early_decode>','exec'),m.__dict__)
class Builder(m.KernelBuilder):
    def schedule(self,ops):
        self.operations=ops
        super().schedule(ops)
b=Builder()
b.build_kernel(10,2047,256,16)
verify_emission(b)
for seed in (123,456,789):
    check(b,seed)
verify_words(b,'full_word',[(i*0x9e3779b9) & 0xFFFFFFFF for i in range(2047)],
             [(i*0xabcdef01+0x80000000) & 0xFFFFFFFF for i in range(256)])
slots=Counter()
for bundle in b.instrs:
    slots.update({engine:len(items) for engine,items in bundle.items()})
times=[b.issue_cycles[i] for i,op in enumerate(b.operations)
       if not op.get('is_setup',False) and op['round']>0 and op['engine']=='load'
       and op['slot'][0] in ('load_offset','vload')]
print(json.dumps(dict(**vars(args),cycles=len(b.instrs),scratch=b.scratch_ptr,
                      slots=slots,weighted=slots['valu']+slots['alu']/8,
                      first_lookup=min(times),last_lookup=max(times),policy=b.schedule_policy)))
