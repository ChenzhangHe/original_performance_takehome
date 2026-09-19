"""Use shallow-record padding for an encoded depth-3 lookup table.

This composable source transform changes preprocessing only.  It adds sixteen
scalar copies (two weighted compute equivalents), no loads or stores, and no
scratch allocation.  The eight runtime node words are duplicated in the two
halves of the sixteen stride-4 records.  Both parent-first and left-first
shallow record layouts retain their padding at field three.

The root experiment separately owns the actual depth-3 gather/address path.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import types

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tune_kernel import check
from verify_kernel import verify_emission, verify_words
from experiments.dataflow_check import verify_dataflow

SOURCE_REF = "34742f6253c7cb1b1b2a3ca664b6b5f2d9a29991"


def encode_depth3_padding(source, repeat=True):
    """Return source with encoded nodes 7..14 repeated in stride-4 field 3.

    In this layout node address A3 (14..21 in the original memory) is at
    D3 = 4*A3 + W - 53.  Transition to shallow depth-4 record addresses is
    B4 = 2*D3 - W - 2 - 4*p3.  p3 is encoded hash parity.

    depth3_vectors was already permuted with i^1 before shallow setup.  Its
    lane-zero scalar is an existing encoded runtime word, not a table constant.
    """
    anchor = '''                            self.add("alu", (opcode, block_output + 4*member+j, scalar, operand))
                    self.add("store", ("vstore", block_store_addr, block_output))'''
    assert source.count(anchor) == 1
    source = source.replace(anchor, '''                            self.add("alu", (opcode, block_output + 4*member+j, scalar, operand))
                        # Fill existing padding with a runtime-encoded depth-3
                        # word; repeat the same eight-node table in both halves.
                        self.add("alu", ("+", block_output + 4*member+3,
                                         depth3_vectors[parent ^ 1], readonly_zero))
                    self.add("store", ("vstore", block_store_addr, block_output))''')
    alternatives = (
        "for index in (parent, 2*parent+1, 2*parent+2, None)",
        "for index in (2*parent+1, parent, 2*parent+2, None)",
    )
    present = [old for old in alternatives if old in source]
    assert len(present) == 1, present
    old = present[0]
    padding_index = "7+(parent-15)%8"
    if not repeat:
        # After the first eight records, these two scratch fields already hold
        # encoded nodes 13 and 14.  Subsequent shallow stores can keep them;
        # the intended lookup addresses read only the first eight records.
        copy = '''                        self.add("alu", ("+", block_output + 4*member+3,
                                         depth3_vectors[parent ^ 1], readonly_zero))'''
        assert source.count(copy) == 1
        source = source.replace(copy, '''                        if parent_start == 0:
                            self.add("alu", ("+", block_output + 4*member+3,
                                             depth3_vectors[parent ^ 1], readonly_zero))''')
        padding_index = "(7+(parent-15)%8 if parent < 23 else 13+(parent-15)%2)"
    source = source.replace(old, old.replace("None", padding_index))
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-policies", action="store_true")
    parser.add_argument("--reuse-padding", action="store_true",
                        help="Keep the last two encoded words in unused later padding (8 fewer ALUs)")
    args = parser.parse_args()
    source = subprocess.run(["git", "show", f"{SOURCE_REF}:perf_takehome.py"],
                            cwd=REPO, check=True, capture_output=True, text=True).stdout
    source = encode_depth3_padding(source, repeat=not args.reuse_padding)
    if not args.full_policies:
        old = "        for policy in dict.fromkeys(policies):"
        assert source.count(old) == 1
        source = source.replace(old, '''        policies = ("fragment_adaptive_tail_hetero_360_220_140_140_920",
                    "fragment_adaptive_tail_hetero_360_220_140_140_900")
        for policy in dict.fromkeys(policies):''')
    module = types.ModuleType("depth3_padding_probe")
    exec(compile(source, "<depth3_padding_probe>", "exec"), module.__dict__)

    class Builder(module.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)

        def allocate_node_lifetimes(self, ops, uses, reusable=()):
            self.logical_slots = [op["slot"] for op in ops]
            super().allocate_node_lifetimes(ops, uses, reusable)

    builder = Builder()
    builder.build_kernel(10, 2047, 256, 16)
    verify_emission(builder)
    verify_dataflow(builder)
    for seed in (123, 456, 789):
        check(builder, seed)
    verify_words(builder, "full_word", [(i*0x9e3779b9)&0xffffffff for i in range(2047)],
                 [(i*0xabcdef01+0x80000000)&0xffffffff for i in range(256)])
    slots = Counter()
    for bundle in builder.instrs:
        slots.update({engine: len(items) for engine, items in bundle.items()})
    print(json.dumps(dict(source_ref=SOURCE_REF, cycles=len(builder.instrs),
                          scratch=builder.scratch_ptr, slots=slots,
                          weighted=slots["valu"]+slots["alu"]/8,
                          policy=builder.schedule_policy,
                          verification="emission, dataflow, three frozen seeds, full-word workspace")))


if __name__ == "__main__":
    main()
