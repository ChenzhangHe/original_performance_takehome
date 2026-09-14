"""Encode depth6/7 in the remaining index workspace on the 955 graph."""
import argparse
from collections import Counter
from pathlib import Path
import json
import subprocess
import sys
import types
import random

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tune_kernel import check, Machine, Tree, Input, build_mem_image, reference_kernel2
from verify_kernel import verify_emission, verify_words


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--first', type=int, choices=(6, 7), default=6)
    p.add_argument('--last', type=int, choices=(6, 7), default=7)
    p.add_argument('--cache', type=int, default=7)
    p.add_argument('--drop-unused-bias', action='store_true')
    p.add_argument('--reuse-buffers', action='store_true')
    p.add_argument('--buffers', type=int, choices=(1, 2, 4), default=2)
    p.add_argument('--cap', type=int, default=4)
    p.add_argument('--debug-loads', action='store_true')
    p.add_argument('--reverse-chains', action='store_true')
    p.add_argument('--tail-start', type=int, default=900)
    args = p.parse_args()
    assert args.last >= args.first
    source = subprocess.run(['git', 'show', '3d24666:perf_takehome.py'], cwd=REPO,
                            check=True, capture_output=True, text=True).stdout
    def replace(old, new):
        nonlocal source
        assert old in source, old[:100]
        source = source.replace(old, new, 1)
    first_node = (1 << args.first)-1
    end_node = (1 << (args.last+1))-1
    count = end_node-first_node
    delta = 2047+64-first_node
    if args.first == 6:
        replace('vector_const((73-block_base) & 0xFFFFFFFF, "block_exit_bias")',
                f'vector_const((73-block_base+{delta}) & 0xFFFFFFFF, "block_exit_bias")')
    replace('            self.workspace_layout = "parent_children_stride4"', f'''            self.workspace_layout = "parent_children_stride4_plus_depth{args.first}{args.last}"
            self.preencoded_node_count += {count}
            self.workspace_node_indices.extend(range({first_node}, {end_node}))
            extra_copy_bias = vector_const((-5-{delta}) & 0xFFFFFFFF, "extra_copy_bias")
            extra_exit_bias = vector_const((-5-2*{delta}) & 0xFFFFFFFF, "extra_exit_bias")
            extra_enter_bias = vector_const((-5+{delta}) & 0xFFFFFFFF, "extra_enter_bias") if {args.first} == 7 else None
            direct_vectors.extend((extra_copy_bias, extra_exit_bias))
            if extra_enter_bias is not None:
                direct_vectors.append(extra_enter_bias)
            extra_buffers = [self.alloc_scratch(f"extra_buffer_{{i}}", VLEN) for i in range(2)]
            extra_src = self.alloc_scratch("extra_src")
            extra_dst = self.alloc_scratch("extra_dst")
            self.add("load", ("const", extra_src, forest_values_p+{first_node}))
            self.add("load", ("const", extra_dst, block_base+64))
            for vector_no in range({count}//VLEN):
                buffer = extra_buffers[vector_no % len(extra_buffers)]
                self.add("load", ("vload", buffer, extra_src))
                self.add("valu", ("^", buffer, buffer, final_xor_vec))
                self.add("store", ("vstore", extra_dst, buffer))
                if vector_no != {count}//VLEN-1:
                    self.add("alu", ("+", extra_src, extra_src, address_step))
                    self.add("alu", ("+", extra_dst, extra_dst, address_step))
            block_input.extend(extra_buffers)''')
    old = '        block_children_base = path_bits_base + rounds * batch_size'
    replace(old, f'''        extra_level_stores = {{}}
        if blocked_lookup:
            all_stores = block_stores
            block_stores = all_stores[:8]
            offset = 8
            for level in range({args.first}, {args.last}+1):
                extra_level_stores[level] = all_stores[offset:offset+(1<<level)//VLEN]
                offset += (1<<level)//VLEN
        block_children_base = path_bits_base + rounds * batch_size''')
    replace('encoded_node = ((blocked_lookup and depth == 4)',
            f'encoded_node = ((blocked_lookup and (depth == 4 or {args.first} <= depth <= {args.last}))')
    replace('extra_ready = (block_stores if blocked_lookup and encoded_node else',
            'extra_ready = ((block_stores if depth == 4 else extra_level_stores[depth]) if blocked_lookup and encoded_node else')
    old = '                    index_base_ready = emit(\n                        "valu", ("multiply_add", chunk_idx, chunk_idx, two, bias), node_address_reads'
    replace(old, f'''                    if blocked_lookup:
                        if depth == {args.last}:
                            bias = extra_exit_bias
                        elif {args.first} <= depth < {args.last}:
                            bias = extra_copy_bias
                        elif depth == {args.first}-1 and {args.first} == 7:
                            bias = extra_enter_bias
                    index_base_ready = emit(
                        "valu", ("multiply_add", chunk_idx, chunk_idx, two, bias), node_address_reads''')
    if args.drop_unused_bias and args.first == args.last:
        replace(f'            extra_copy_bias = vector_const((-5-{delta}) & 0xFFFFFFFF, "extra_copy_bias")',
                '            extra_copy_bias = None')
        replace('            direct_vectors.extend((extra_copy_bias, extra_exit_bias))',
                '            direct_vectors.append(extra_exit_bias)')
    if args.reuse_buffers:
        replace('            extra_buffers = [self.alloc_scratch(f"extra_buffer_{i}", VLEN) for i in range(2)]',
                '            extra_buffers = block_input[:2]')
        replace('            block_input.extend(extra_buffers)', '')
    else:
        replace('            extra_buffers = [self.alloc_scratch(f"extra_buffer_{i}", VLEN) for i in range(2)]',
                f'            extra_buffers = [self.alloc_scratch(f"extra_buffer_{{i}}", VLEN) for i in range({args.buffers})]')
    replace('op["round"] = min(4, needed[i])', f'op["round"] = min({args.cap}, needed[i])')
    replace('"adaptive_tail_hetero_360_220_140_140_900"',
            f'"adaptive_tail_hetero_360_220_140_140_{args.tail_start}"')
    if args.reverse_chains:
        start = source.index('        input_addr_ready = []')
        end = source.index('\n        for chunk_no in range(chunk_count):\n            emit_context.update(chunk=chunk_no, round=-1', start)
        source = source[:start] + '''        input_addr_ready = [None] * chunk_count
        for chunk_no in range(chunk_count-1, -1, -1):
            emit_context.update(chunk=chunk_no, round=0, local_seq=0)
            if chunk_no == chunk_count-1 or (chunk_no+1) % INPUT_ADDRESS_CHAIN_LENGTH == 0:
                ready = emit("load", ("const", input_addrs+chunk_no, inp_values_p+chunk_no*VLEN))
            else:
                ready = emit("alu", ("-", input_addrs+chunk_no, input_addrs+chunk_no+1, address_step), input_addr_ready[chunk_no+1])
            input_addr_ready[chunk_no] = ready
''' + source[end:]
        replace('            emit_context.update(chunk=chunk_no, round=-1, local_seq=0)',
                '            emit_context.update(chunk=chunk_no, round=0, local_seq=0)')
    m = types.ModuleType('extra_encoding')
    exec(compile(source, '<extra_encoding>', 'exec'), m.__dict__)
    m.BLOCKED_FINAL_CACHE_CHUNKS = args.cache
    if args.reverse_chains:
        m.INPUT_ADDRESS_CHAIN_LENGTH = 4
    class Builder(m.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)
    b = Builder()
    try:
        b.build_kernel(10, 2047, 256, 16)
    except AssertionError as error:
        if str(error) != 'Out of scratch space':
            raise
        print(json.dumps(dict(**vars(args), rejected='scratch', required=b.scratch_ptr)))
        return
    verify_emission(b)
    if args.debug_loads:
        random.seed(123)
        tree = Tree.generate(10)
        inp = Input.generate(tree, 256, 16)
        mem = build_mem_image(tree, inp)
        trace = {}
        for _ in reference_kernel2(mem.copy(), trace):
            pass
        lookup_ops = {(b.issue_cycles[i], op['slot']):op for i,op in enumerate(b.operations)
                      if op['engine']=='load' and not op.get('is_setup',False) and op['round']>0}
        class TracedMachine(Machine):
            def load(self, core, *slot):
                op = lookup_ops.get((self.cycle, slot))
                if op:
                    r, chunk = op['round'], op['chunk']
                    depth = r % 11
                    lane = slot[3] if slot[0] == 'load_offset' else slot[2] - b.scratch['idx'] - chunk*8
                    idx = trace[r, chunk*8+lane, 'idx']
                    expected_address = (mem[5] + 4*(idx-15) if depth==4 else
                                        7+idx+(delta if args.first<=depth<=args.last else 0))
                    actual_address = core.scratch[slot[2]+(slot[3] if slot[0]=='load_offset' else 0)]
                    assert actual_address == expected_address, (self.cycle,r,chunk,lane,'address',actual_address,expected_address)
                    expected_value = trace[r,chunk*8+lane,'node_val'] ^ (0xb55a4f09 if depth==4 or args.first<=depth<=args.last else 0)
                    assert self.mem[actual_address] == expected_value, (self.cycle,r,chunk,lane,'value',self.mem[actual_address],expected_value)
                return super().load(core,*slot)
        machine = TracedMachine(mem,b.instrs,b.debug_info())
        machine.enable_debug = machine.enable_pause = False
        machine.run()
    for seed in (123, 456, 789):
        try:
            check(b, seed)
        except AssertionError:
            # In particular, the depth-7-only prototype retaining an unused
            # bias failed the frozen check. Never publish its timing as valid.
            print(json.dumps(dict(**vars(args), rejected='correctness', failed_seed=seed)))
            return
    verify_words(b, 'full_word', [(i*0x9e3779b9) & 0xFFFFFFFF for i in range(2047)],
                 [(i*0xabcdef01+0x80000000) & 0xFFFFFFFF for i in range(256)])
    slots = Counter()
    for bundle in b.instrs:
        slots.update({engine: len(items) for engine, items in bundle.items()})
    times = [b.issue_cycles[i] for i,op in enumerate(b.operations)
             if not op.get('is_setup', False) and op['round']>0 and op['engine']=='load'
             and op['slot'][0] in ('load_offset','vload')]
    print(json.dumps(dict(**vars(args), cycles=len(b.instrs), scratch=b.scratch_ptr,
                          slots=slots, weighted=slots['valu']+slots['alu']/8,
                          first_lookup=min(times), last_lookup=max(times),
                          policy=b.schedule_policy)))


if __name__ == '__main__':
    main()
