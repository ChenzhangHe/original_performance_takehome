"""Setup-deadline probes pinned to 468f713 (969 cycles), including rejects.

Source transformations happen only in memory. Accepted changes also have
independent production switches in tune_kernel.py; this preserves the
bounded deadline-cap, inherited-cohort, and parent/child-overlap probes.
"""
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cap', type=int, default=4)
    parser.add_argument('--inherit-chunk', action='store_true')
    parser.add_argument('--final-cache', type=int, default=7)
    parser.add_argument('--overlap', action='store_true')
    parser.add_argument('--early-tail', action='store_true')
    parser.add_argument('--drop-weight', action='store_true')
    args = parser.parse_args()
    source = subprocess.run(['git', 'show', '468f713:perf_takehome.py'], cwd=REPO,
                            check=True, capture_output=True, text=True).stdout
    if args.drop_weight:
        old = '            for shift in (1, 2, 3):'
        assert old in source
        source = source.replace(old, '            for shift in (1, 2):', 1)
    if args.overlap:
        old = 'val_ready = ([*node_loads, *all_copies] if BLOCKED_FUSE_PARENT_XOR else'
        assert old in source
        source = source.replace(old, 'val_ready = (node_loads if BLOCKED_FUSE_PARENT_XOR else', 1)
    if args.early_tail:
        start = source.index('                    intercept_result = chunk_node + VLEN')
        end = source.index('                elif (depth == 4', start)
        source = source[:start] + '''                    masks = [path_bits[d][0] for d in (0, 1, 2)]
                    ready = [path_bits[d][1] for d in (0, 1, 2)]
                    pairs = sorted(range(8), key=lambda p: ((5 - 22 - 2*p) >> 1) & 7)
                    order = [pairs[((i & 1) << 2) | (i & 2) | (i >> 2)] for i in range(8)]
                    dlow, dhigh, elow, ehigh = [chunk_node + i * VLEN for i in range(4)]
                    def quartet(values, destination, *deps):
                        left = select(chunk_tmp1, masks[0], values[1], values[0], ready[0], *deps)
                        right = select(chunk_tmp2, masks[0], values[3], values[2], ready[0], *deps)
                        return select(destination, masks[1], chunk_tmp2, chunk_tmp1, left, right, ready[1])
                    dl = quartet([depth4_vectors[2*p+1] for p in order[:4]], dlow)
                    dh = quartet([depth4_vectors[2*p+1] for p in order[4:]], dhigh, dl)
                    el = quartet([depth4_vectors[2*p] for p in order[:4]], elow, dh)
                    eh = quartet([depth4_vectors[2*p] for p in order[4:]], ehigh, el)
                    slope = select(dlow, masks[2], dhigh, dlow, dl, dh, ready[2])
                    intercept = select(elow, masks[2], ehigh, elow, el, eh, ready[2])
                    selected = emit("valu", ("multiply_add", chunk_node, interpolation, dlow, elow),
                                    slope, intercept, interpolation_ready)
                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected)
''' + source[end:]
    kernel = types.ModuleType('deadline_kernel')
    exec(compile(source, '<deadline_kernel>', 'exec'), kernel.__dict__)
    kernel.BLOCKED_FINAL_CACHE_CHUNKS = args.final_cache

    class Builder(kernel.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            # This pinned graph has 32 body groups, so cohort 32 is setup.
            setup = [op['chunk'] == kernel.SETUP_CHUNK for op in ops]
            needed = [len(ops) if setup[i] else max(0, op['round']) for i, op in enumerate(ops)]
            consumers = [set() if setup[i] else {op['chunk']} for i, op in enumerate(ops)]
            for i in range(len(ops)-1, -1, -1):
                for d in ops[i]['deps']:
                    if d is not None:
                        assert d < i
                        needed[d] = min(needed[d], needed[i])
                        consumers[d].update(consumers[i])
            # Capture semantic lookup membership BEFORE priority retagging.
            self.lookup_ids = [i for i, op in enumerate(ops) if not setup[i] and op['round'] > 0
                               and op['engine'] == 'load' and op['slot'][0] in ('vload', 'load_offset')]
            for i, op in enumerate(ops):
                if setup[i]:
                    op['round'] = min(args.cap, needed[i])
                    if args.inherit_chunk and consumers[i]:
                        op['chunk'] = max(consumers[i])
            super().schedule(ops)

    builder = Builder()
    config = dict(parent='468f713', **vars(args))
    try:
        builder.build_kernel(10, 2047, 256, 16)
    except AssertionError as error:
        if str(error) != 'Out of scratch space':
            raise
        print(json.dumps(dict(**config, rejected='scratch', required=builder.scratch_ptr)))
        return
    verify_emission(builder)
    for seed in (123, 456, 789):
        check(builder, seed)
    counts = Counter()
    for bundle in builder.instrs:
        counts.update({engine: len(slots) for engine, slots in bundle.items()})
    times = [builder.issue_cycles[i] for i in builder.lookup_ids]
    print(json.dumps(dict(**config, cycles=len(builder.instrs), scratch=builder.scratch_ptr,
                          slots=counts, weighted=counts['valu']+counts['alu']/8,
                          policy=builder.schedule_policy,
                          lookup_first=min(times), lookup_last=max(times), checked_seeds=[123, 456, 789])))


if __name__ == '__main__':
    main()
