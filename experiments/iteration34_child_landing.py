"""Child landing-layout probe against frozen parent commit 1d27efc."""
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


def make_module(zero_war=False, base_layout=False, source_ref='1d27efc'):
    # Keep the historical default; allow an explicit updated-graph retest.
    source = ((REPO / 'perf_takehome.py').read_text() if source_ref == 'working-tree'
              else subprocess.run(['git', 'show', f'{source_ref}:perf_takehome.py'], cwd=REPO,
                                  check=True, capture_output=True, text=True).stdout)
    baseline = source
    def replace(old, new):
        nonlocal source
        assert old in source, old[:100]
        source = source.replace(old, new, 1)

    replace('enumerate([block_input[0]+parent, *children])',
            'enumerate([children[0], block_input[0]+parent, children[1]])')
    replace('for index in (parent, 2*parent+1, 2*parent+2, None)',
            'for index in (2*parent+1, parent, 2*parent+2, None)')
    replace('            self.preencoded_node_count = 64', '''            block_tail_bases = (vector_const(block_base+29, "block_tail_left"),
                                vector_const(block_base+61, "block_tail_right"))
            direct_vectors.extend(block_tail_bases)
            self.preencoded_node_count = 64''')
    replace('            block_left = block_children_base + 2*offset\n            block_right = block_left + VLEN',
            '            block_left = block_children_base + 3*offset\n            block_right = block_left + 2*VLEN')
    replace('                        idx_ready = select(chunk_idx, root_parity, left_base, right_base, selected)',
            '''                        if blocked_lookup and round_no > forest_height:
                            left_base, right_base = block_tail_bases
                        idx_ready = select(chunk_idx, root_parity, left_base, right_base, selected)''')
    start = source.index('                elif blocked_lookup and depth == 4 and round_no != rounds - 1:')
    end = source.index('                elif blocked_lookup and depth == 5:', start)
    source = source[:start] + '''                elif blocked_lookup and depth == 4 and round_no != rounds - 1:
                    block_use_start = len(ops)
                    pending = None
                    node_loads = []
                    block_children_ready = []
                    node_address_reads = []
                    for lane in range(VLEN):
                        read_buffer = block_left + lane
                        loaded = emit("load", ("vload", read_buffer, chunk_idx+lane),
                                      idx_ready[lane] if isinstance(idx_ready, list) else idx_ready,
                                      block_stores, pending)
                        node_address_reads.append(loaded)
                        parent = emit("alu", ("^", chunk_val+lane, chunk_val+lane, read_buffer+1),
                                      loaded, val_ready)
                        child = emit("alu", ("+", block_right+lane, read_buffer+2, readonly_zero), loaded)
                        pending = [parent, child]
                        node_loads.append(parent)
                        block_children_ready.extend((loaded, child))
                    val_ready = [*node_loads, *pending]
''' + source[end:]
    replace('                    node_pool_uses.extend((dest, block_use_start, len(ops), 1) for dest in (block_left, block_right))',
            '''                    node_pool_uses.append((block_left, block_use_start, len(ops), -2))
                    node_pool_uses.append((block_right, block_use_start, len(ops), 1))''')
    replace('''            for address in (base + n * VLEN for n in range(width)):
                addresses = set(range(address, address + VLEN))''', '''            spans = [(base, -width)] if width < 0 else [(base+n*VLEN, 1) for n in range(width)]
            for address, span in spans:
                addresses = set(range(address, address + span*VLEN))''')
    replace('                                      address, start, end))',
            '                                      address, start, end, span))')
    start = source.index('        for first, last, address, start, end in sorted(intervals):')
    end = source.index('            for op in ops[start:end]:', start)
    source = source[:start] + '''        for first, last, address, start, end, span in sorted(intervals):
            physical_index = {base:i for i, base in enumerate(physical_slots)}
            colors = None
            for base in physical_slots:
                indices = [physical_index.get(base+n*VLEN) for n in range(span)]
                if all(i is not None and slots_end[i] <= first for i in indices):
                    colors = indices
                    break
            if colors is None:
                colors = []
                for _ in range(span):
                    colors.append(len(slots_end))
                    physical_slots.append(self.scratch_ptr + (len(slots_end)-len(reusable))*VLEN)
                    slots_end.append(last)
            for color in colors:
                slots_end[color] = last
            physical = physical_slots[colors[0]]
''' + source[end:]
    replace('                    if address <= original[pos] < address + VLEN:',
            '                    if address <= original[pos] < address + span*VLEN:')
    if base_layout:
        source = baseline
    if zero_war:
        replace('''            op["deps"] = [prefix[d] for d in op["deps"] if d is not None and d not in dead]''', '''            op["deps"] = [prefix[d] for d in op["deps"] if d is not None and d not in dead]
            op["same_cycle_deps"] = [prefix[d] for d in op.get("same_cycle_deps", ())]''')
        if base_layout:
            replace('''                    pending = [None] * BLOCKED_READ_BANKS''', '''                    pending = [None] * BLOCKED_READ_BANKS
                    prior_loads = [None] * BLOCKED_READ_BANKS''')
            replace('''                                      block_stores, pending[bank])
                        node_address_reads.append(loaded)''', '''                                      block_stores, prior_loads[bank])
                        ops[loaded]["same_cycle_deps"] = pending[bank] or []
                        prior_loads[bank] = loaded
                        node_address_reads.append(loaded)''')
        else:
            replace('''                                      block_stores, pending)
                        node_address_reads.append(loaded)''', '''                                      block_stores, node_address_reads[-1] if node_address_reads else None)
                        ops[loaded]["same_cycle_deps"] = pending or []
                        node_address_reads.append(loaded)''')
        replace('''            scheduled_count = 0
            bundles = []''', '''            scheduled_count = 0
            finished = set()
            bundles = []''')
        replace('''                chosen = []
                bundle = {}''', '''                chosen = []
                forced_alu = []
                bundle = {}''')
        replace('''                            and len(bundles) >= ALU_VECTOR_RESERVE_START):''', '''                            and len(bundles) >= ALU_VECTOR_RESERVE_START
                            and len(forced_alu) <= SLOT_LIMITS["alu"]-VLEN):''')
        replace('''                    selected = candidates[:capacity]
                    del candidates[: len(selected)]''', '''                    if engine == "load":
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
        replace('''                scheduled_count += len(chosen)
                for op_id in chosen:''', '''                scheduled_count += len(chosen)
                finished.update(chosen)
                for op_id in chosen:''')
        replace('''        self.offloaded_ops = set(self.lane_issue_cycles)''', '''        for op_id, op in enumerate(ops):
            assert all(self.issue_first_cycles[op_id] >= self.issue_cycles[d]
                       for d in op.get("same_cycle_deps", ()))
        self.offloaded_ops = set(self.lane_issue_cycles)''')
    mod = types.ModuleType('landing_kernel')
    exec(compile(source, '<landing_kernel>', 'exec'), mod.__dict__)
    return mod


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--final-cache', type=int, default=7)
    parser.add_argument('--hash-alu', type=int, default=0)
    parser.add_argument('--zero-war', action='store_true')
    parser.add_argument('--base-layout', action='store_true')
    parser.add_argument('--read-banks', type=int, default=4)
    parser.add_argument('--source-ref', default='1d27efc')
    args = parser.parse_args()
    m = make_module(args.zero_war, args.base_layout, args.source_ref)
    m.BLOCKED_FINAL_CACHE_CHUNKS = args.final_cache
    m.HASH_ALU_CHUNKS = args.hash_alu
    m.BLOCKED_READ_BANKS = args.read_banks
    class B(m.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)
    b = B()
    b.build_kernel(10, 2047, 256, 16)
    verify_emission(b)
    for seed in (123, 456, 789):
        check(b, seed)
    slots = Counter()
    for bundle in b.instrs:
        slots.update({e:len(v) for e,v in bundle.items()})
    print(json.dumps(dict(source_ref=args.source_ref, final_cache=args.final_cache, hash_alu=args.hash_alu, zero_war=args.zero_war,
                          base_layout=args.base_layout, read_banks=args.read_banks,
                          cycles=len(b.instrs), scratch=b.scratch_ptr, slots=slots,
                          weighted=slots['valu']+slots['alu']/8, policy=b.schedule_policy)))


if __name__ == '__main__':
    main()
