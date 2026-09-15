"""Retest direct child landing on the load-light compact-record graph.

The deep record becomes [left,parent,right]. Eight overlapping vloads land
left children directly in one vector; strict reader/overwrite dependencies
remain. A probe-only allocator preserves the full 16-word landing span.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import types

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from iteration37_compact_deep import make_source as compact_source, SOURCE_REF
from dataflow_check import verify_dataflow
from tune_kernel import check
from verify_kernel import verify_emission, verify_words


def make_source(full_policies):
    args = argparse.Namespace(lane_tail=True, window=0, select_round=7, index_select=[4, 6],
                              index_groups=32, drop_unused_biases=True, full_policies=full_policies,
                              safe_reuse=True)
    source = compact_source(args)
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:100])
        source = source.replace(old, new)
    replace('for index in (parent, 2*parent+1, 2*parent+2))',
            'for index in (2*parent+1, parent, 2*parent+2))')
    replace('''                            if field == 0:
                                scalar = block_input[0]+parent
                            else:
                                child = 2*parent+field-1''',
            '''                            if field == 1:
                                scalar = block_input[0]+parent
                            else:
                                child = 2*parent+field//2''')
    replace('            deep_left = deep_base+2*offset\n            deep_right = deep_left+VLEN',
            '            deep_left = deep_base+3*offset\n            deep_right = deep_left+2*VLEN')
    start = source.index('                elif blocked_lookup and depth == 6 and round_no != rounds - 1:\n                    deep_use_start')
    end = source.index('                elif blocked_lookup and depth == 7:', start)
    source = source[:start]+'''                elif blocked_lookup and depth == 6 and round_no != rounds - 1:
                    deep_use_start = len(ops)
                    pending = None
                    node_loads = []
                    deep_children_ready = []
                    node_address_reads = []
                    for lane in range(VLEN):
                        read_buffer = deep_left+lane
                        loaded = emit("load", ("vload", read_buffer, chunk_idx+lane),
                                      idx_ready[lane] if isinstance(idx_ready, list) else idx_ready,
                                      extra_level_stores, pending)
                        node_address_reads.append(loaded)
                        parent = emit("alu", ("^", chunk_val+lane, chunk_val+lane, read_buffer+1), loaded, val_ready)
                        child = emit("alu", ("+", deep_right+lane, read_buffer+2, readonly_zero), loaded)
                        pending = [parent, child]
                        node_loads.append(parent)
                        deep_children_ready.extend((loaded, child))
                    val_ready = [*node_loads, *pending]
''' + source[end:]
    replace('                    node_pool_uses.extend((dest, deep_use_start, len(ops), 1) for dest in (deep_left, deep_right))',
            '''                    node_pool_uses.append((deep_left, deep_use_start, len(ops), -2))
                    node_pool_uses.append((deep_right, deep_use_start, len(ops), 1))''')
    replace('''            for address in (base + n * VLEN for n in range(width)):
                addresses = set(range(address, address + VLEN))''',
            '''            spans = [(base, -width)] if width < 0 else [(base+n*VLEN, 1) for n in range(width)]
            for address, span in spans:
                addresses = set(range(address, address + span*VLEN))''')
    replace('                                          for o in touched)))', '                                          for o in touched), span))')
    start = source.index('        for first, last, address, start, end, ends_with_write in sorted(intervals):')
    end = source.index('            for op in ops[start:end]:', start)
    source = source[:start]+'''        for first, last, address, start, end, ends_with_write, span in sorted(intervals):
            physical_index = {base:i for i, base in enumerate(physical_slots)}
            colors = None
            for base in physical_slots:
                indices = [physical_index.get(base+n*VLEN) for n in range(span)]
                if all(i is not None and (slots_end[i] < first or (slots_end[i] == first and read_only_end[i]))
                       for i in indices):
                    colors = indices
                    break
            if colors is None:
                colors = []
                for _ in range(span):
                    colors.append(len(slots_end))
                    physical_slots.append(self.scratch_ptr+(len(slots_end)-len(reusable))*VLEN)
                    slots_end.append(last)
                    read_only_end.append(not ends_with_write)
            for color in colors:
                slots_end[color] = last
                read_only_end[color] = not ends_with_write
            physical = physical_slots[colors[0]]
''' + source[end:]
    replace('                    if address <= original[pos] < address + VLEN:',
            '                    if address <= original[pos] < address + span*VLEN:')
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full-policies', action='store_true')
    args = parser.parse_args()
    module = types.ModuleType('deep_landing_probe')
    exec(compile(make_source(args.full_policies), '<deep_landing_probe>', 'exec'), module.__dict__)
    module.BLOCKED_FINAL_CACHE_CHUNKS = 0
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
        verify_emission(builder)
        verify_dataflow(builder)
        for seed in (123, 456, 789):
            check(builder, seed)
        verify_words(builder, 'full_word', [(i*0x9e3779b9)&0xffffffff for i in range(2047)],
                     [(i*0xabcdef01+0x80000000)&0xffffffff for i in range(256)])
        result.update(cycles=len(builder.instrs), scratch=builder.scratch_ptr,
                      verification='three frozen seeds, full-word workspace, exact emission and scratch provenance')
    counts = Counter(op['engine'] for op in builder.operations)
    result.update(logical_slots=counts, weighted=counts['valu']+counts['alu']/8, policy=builder.schedule_policy)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
