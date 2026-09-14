"""Spend bounded spare flow capacity to remove deep index subtractions."""
import argparse
from collections import Counter
from pathlib import Path
import json
import subprocess
import sys
import types

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from tune_kernel import check
from verify_kernel import verify_emission, verify_words
p=argparse.ArgumentParser()
p.add_argument('--depth',type=int,choices=(6,7,8,9),default=9)
p.add_argument('--groups',type=int,default=32)
args=p.parse_args()
source=subprocess.run(['git','show','3d24666:perf_takehome.py'],cwd=REPO,
                      check=True,capture_output=True,text=True).stdout
def replace(old,new):
    global source
    assert old in source,old[:100]
    source=source.replace(old,new,1)
replace('            direct_vectors = [neg2, address_bias, negative_weights[2]]',
        '''            index_bias_even = vector_const(0xFFFFFFFA, "index_bias_even")
            direct_vectors = [neg2, address_bias, negative_weights[2], index_bias_even]''')
replace('                        and not (blocked_lookup and depth == 5)',
        f'                        and not (blocked_lookup and (depth == 5 or (depth == {args.depth} and chunk_no < {args.groups})))')
replace('                if positive_addresses:\n                    if blocked_lookup and depth == 3:',
        f'''                if positive_addresses:
                    if blocked_lookup and depth == {args.depth} and chunk_no < {args.groups}:
                        chosen_bias = select(chunk_tmp1, parity_dest, index_bias_even, address_bias, parity)
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, two, chunk_tmp1), idx_ready, chosen_bias)
                        continue
                    if blocked_lookup and depth == 3:''')
m=types.ModuleType('index_select')
exec(compile(source,'<index_select>','exec'),m.__dict__)
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
