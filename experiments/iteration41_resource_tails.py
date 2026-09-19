"""Resource-plus-output-tail lower bounds for a fixed logical kernel graph.

An operation's tail is the longest strict dependency path to an output
store, including both endpoints. For a fixed-capacity engine, order its
tails from longest to shortest. Among the first k operations, one cannot
issue before cycle ceil(k/capacity)-1, so completion is at least that cycle
plus the kth tail. This ignores release times, other engines and multi-cycle
scalar offloads and is therefore an optimistic necessary bound, not a
schedulability or globally optimal algorithm claim.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import types

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from experiments.iteration41_transfer import make_source as transfer_source, SOURCE_REF
from problem import SLOT_LIMITS
from verify_kernel import verify_emission
from experiments.dataflow_check import verify_dataflow
from analyze_kernel import resource_tail_bounds


def get_tails(deps, outputs):
    successors = [[] for _ in deps]
    pending = [len(ds) for ds in deps]
    order = [i for i, n in enumerate(pending) if not n]
    for i, ds in enumerate(deps):
        for dep in ds:
            successors[dep].append(i)
    for i in order:
        for child in successors[i]:
            pending[child] -= 1
            if not pending[child]:
                order.append(child)
    assert len(order) == len(deps), 'Not a DAG'
    tails = [0] * len(deps)
    for i in outputs:
        tails[i] = 1
    for i in reversed(order):
        useful = [tails[s] for s in successors[i] if tails[s]]
        if useful:
            tails[i] = max(tails[i], 1+max(useful))
    return tails, successors


def summarize(builder, deps):
    ops = builder.operations
    outputs = {i for i, op in enumerate(ops) if op['engine'] == 'store' and not op.get('is_setup', False)}
    tails, successors = get_tails(deps, outputs)
    assert len(outputs) == 32
    results = {}
    for engine in ('flow', 'load', 'store'):
        # These engine assignments cannot use scalar-vector offload. Do not
        # apply this fixed-capacity bound to the logical VALU/ALU partition.
        ids = sorted((i for i, op in enumerate(ops) if op['engine'] == engine and tails[i]),
                     key=lambda i: tails[i], reverse=True)
        capacity = SLOT_LIMITS[engine]
        ranked = [((k+capacity-1)//capacity-1+tails[i], k, tails[i])
                  for k, i in enumerate(ids, 1)]
        bound, k, tail = max(ranked)
        witness = ids[k-1]
        chain = [witness]
        while chain[-1] not in outputs:
            child = max(successors[chain[-1]], key=lambda i: tails[i])
            assert tails[child] == tails[chain[-1]]-1
            chain.append(child)
        results[engine] = dict(output_reaching_operations=len(ids),
                               all_engine_operations=sum(op['engine'] == engine for op in ops),
                               capacity=capacity, minimum_tail=min(tails[i] for i in ids),
                               bound=bound, witness_count=k, witness_tail=tail,
                               tail_histogram=dict(sorted(Counter(tails[i] for i in ids).items())),
                               one_witness_path=[dict(op_id=i, engine=ops[i]['engine'],
                                                      opcode=ops[i]['slot'][0],
                                                      round=ops[i]['round'], group=ops[i]['chunk']) for i in chain])
    return results


def raw_dependencies(builder):
    """Relax to direct scratch RAW edges already present in the strict DAG.

    Memory edges and any only-transitively represented producer edges are
    deliberately omitted. This is not an executable dependency graph; a
    lower bound that survives this relaxation is still necessary.
    """
    last_writer = {}
    deps = []
    for op, slot in zip(builder.operations, builder.logical_slots):
        reads, writes = builder.instruction_accesses(op['engine'], slot)
        deps.append(sorted({last_writer[a] for a in reads if a in last_writer} & set(op['deps'])))
        for address in writes:
            last_writer[address] = len(deps)-1
    # Every reconstructed true producer must occur before its consumer in
    # the measured strict schedule, independently of any omitted WAR/WAW.
    assert all(builder.issue_first_cycles[i] > builder.issue_cycles[d]
               for i, ds in enumerate(deps) for d in ds)
    return deps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs-immediate', action='store_true')
    parser.add_argument('--details', action='store_true', help='Include tail histograms and complete witness paths')
    args = parser.parse_args()
    source = transfer_source(argparse.Namespace(root='none', inputs_flow=False,
                                                inputs_immediate=args.inputs_immediate, full_policies=True))
    module = types.ModuleType('resource_tails_probe')
    exec(compile(source, '<resource_tails_probe>', 'exec'), module.__dict__)
    class Builder(module.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)
        def allocate_node_lifetimes(self, ops, uses, reusable=()):
            self.logical_slots = [op['slot'] for op in ops]
            super().allocate_node_lifetimes(ops, uses, reusable)
    builder = Builder()
    builder.build_kernel(10, 2047, 256, 16)
    verify_emission(builder)
    verify_dataflow(builder)
    strict = summarize(builder, [op['deps'] for op in builder.operations])
    raw = summarize(builder, raw_dependencies(builder))
    # Compare the independent diagnostic implementation with the public
    # analyzer, so future reports retain the same inclusive-tail convention.
    public = resource_tail_bounds(builder)
    for engine in ('load', 'flow'):
        assert all(strict[engine][key] == value for key, value in public[engine].items())
    if not args.details:
        for graph in (strict, raw):
            for item in graph.values():
                item.pop('tail_histogram')
                item.pop('one_witness_path')
    print(json.dumps(dict(source_ref=SOURCE_REF, **vars(args), cycles=len(builder.instrs),
                          scope='fixed operation graph, not every possible algorithm',
                          strict_dependencies=strict, raw_only=raw), indent=2))


if __name__ == '__main__':
    main()
