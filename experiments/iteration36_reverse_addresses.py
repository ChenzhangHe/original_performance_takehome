"""Align short input-address chains with descending cohort priority."""
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
from verify_kernel import verify_emission
p=argparse.ArgumentParser()
p.add_argument('--chain',type=int,default=2)
p.add_argument('--reverse',action='store_true')
p.add_argument('--input-round',type=int,choices=(-1,0),default=-1)
p.add_argument('--tail-start',type=int,default=900)
args=p.parse_args()
source=subprocess.run(['git','show','3d24666:perf_takehome.py'],cwd=REPO,
                      check=True,capture_output=True,text=True).stdout
if args.reverse:
    start=source.index('        input_addr_ready = []')
    end=source.index('\n        for chunk_no in range(chunk_count):\n            emit_context.update(chunk=chunk_no, round=-1',start)
    source=source[:start]+'''        input_addr_ready = [None] * chunk_count
        for chunk_no in range(chunk_count-1, -1, -1):
            if scored_shape and INPUT_ADDRESS_CONSUMER_PRIORITY:
                emit_context.update(chunk=chunk_no, round=0, local_seq=0)
            if chunk_no == chunk_count-1 or (chunk_no+1) % INPUT_ADDRESS_CHAIN_LENGTH == 0:
                ready = emit("load", ("const", input_addrs+chunk_no, inp_values_p+chunk_no*VLEN))
            else:
                ready = emit("alu", ("-", input_addrs+chunk_no, input_addrs+chunk_no+1, address_step),
                             input_addr_ready[chunk_no+1])
            input_addr_ready[chunk_no] = ready
''' + source[end:]
m=types.ModuleType('reverse_addresses')
source=source.replace('"adaptive_tail_hetero_360_220_140_140_900"',
                      f'"adaptive_tail_hetero_360_220_140_140_{args.tail_start}"',1)
if args.input_round == 0:
    old='            emit_context.update(chunk=chunk_no, round=-1, local_seq=0)'
    assert old in source
    source=source.replace(old,'            emit_context.update(chunk=chunk_no, round=0, local_seq=0)',1)
exec(compile(source,'<reverse_addresses>','exec'),m.__dict__)
m.INPUT_ADDRESS_CHAIN_LENGTH=args.chain
class Builder(m.KernelBuilder):
    def schedule(self,ops):
        self.operations=ops
        super().schedule(ops)
b=Builder()
b.build_kernel(10,2047,256,16)
verify_emission(b)
for seed in (123,456,789):
    check(b,seed)
slots=Counter()
for bundle in b.instrs:
    slots.update({engine:len(items) for engine,items in bundle.items()})
times=[b.issue_cycles[i] for i,op in enumerate(b.operations)
       if not op.get('is_setup',False) and op['round']>0 and op['engine']=='load'
       and op['slot'][0] in ('load_offset','vload')]
print(json.dumps(dict(**vars(args),cycles=len(b.instrs),scratch=b.scratch_ptr,
                      slots=slots,weighted=slots['valu']+slots['alu']/8,
                      first_lookup=min(times),last_lookup=max(times),policy=b.schedule_policy)))
