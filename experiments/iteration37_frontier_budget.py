"""Cost-only rejection screen for specific prefix-routing implementations.

Not a new kernel or measured score. Workload samples illustrate skew only;
the slot counts below do not assume uniformly populated buckets.
"""
from collections import Counter
import json
from pathlib import Path
import random
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tune_kernel import Tree, Input, build_mem_image, reference_kernel2
from problem import SLOT_LIMITS


def main():
    n, bits = 256, 4
    # Two payload words: value + packed prefix/original input identity.
    # Scalar-scatter radix writes both each pass, then restores output order.
    radix_stores = bits*2*n+n
    stages = 8*9//2
    comparators = (n//2)*stages
    # Each comparator conditionally exchanges both ends of two payload words.
    bitonic_flow = comparators*4
    # Masked exchange: compare, negate mask, then xor/and/two xors per word.
    bitonic_alu = comparators*(2+4*2)
    # Idealized single-scatter memory-counter scheme: histogram update,
    # cursor update, two payload stores; plus final order restoration.
    # Initialization, prefix sum, conflict handling, addressing and scratch
    # feasibility are omitted, so this is deliberately an optimistic bound.
    direct_stores = n*(1+1+2+1)
    print(json.dumps(dict(
        kind='cost_model_not_execution', current_first_traversal_lookup_slots=1536,
        maximum_lookup_engine_work_removable_cycles=1536//SLOT_LIMITS['load'],
        scalar_scatter_four_pass_radix=dict(stores=radix_stores,
                                           store_floor_cycles=radix_stores//SLOT_LIMITS['store']),
        bitonic=dict(comparators=comparators, scalar_select_flow_slots=bitonic_flow,
                     flow_floor_cycles=bitonic_flow//SLOT_LIMITS['flow'],
                     masked_exchange_alu_slots=bitonic_alu,
                     alu_floor_cycles=bitonic_alu//SLOT_LIMITS['alu']),
        idealized_memory_counter_scatter=dict(stores=direct_stores,
                                             store_floor_cycles=direct_stores//SLOT_LIMITS['store'],
                                             counter_loads=2*n,
                                             workspace_feasibility='not established'),
        conclusion='Reject these full radix/bitonic implementations, not every possible prefix-grouping algorithm')))
    for seed in (123, 456, 789):
        random.seed(seed)
        tree = Tree.generate(10)
        inp = Input.generate(tree, n, 16)
        trace = {}
        for _ in reference_kernel2(build_mem_image(tree, inp), trace):
            pass
        buckets = Counter(trace[4, i, 'idx'] for i in range(n))
        counts = [buckets[parent] for parent in range(15, 31)]
        print(json.dumps(dict(kind='illustration_only', seed=seed, prefix_bucket_sizes=counts,
                              occupied=len(buckets), largest=max(counts), smallest=min(counts),
                              unique_nodes_by_depth={depth: len({trace[depth, i, 'idx'] for i in range(n)})
                                                     for depth in range(4, 11)})))


if __name__ == '__main__':
    main()
