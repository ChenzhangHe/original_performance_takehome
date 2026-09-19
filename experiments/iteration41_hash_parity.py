"""Exact early parity branch for a bounded final shallow-round cohort.

For z = the middle XOR result, F=9*z+0xfd7046c5 and encoded parity is
((F XOR (F>>16)) & 1). It equals ((0x80048000*z+0xa3628000)>>31), with
32-bit wrapping. The branch adds a MAC and shift and removes the old mask:
net +1 vector-equivalent per selected group/round, plus all constant setup.
The complete hash remains unchanged and uses a separate F vector so the
branch and F can read z independently without an artificial ordering edge.
The final hash write waits for the branch's z read (the real WAR hazard).
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
from experiments.dataflow_check import verify_dataflow
from tune_kernel import check
from verify_kernel import verify_emission, verify_words, verify_workspace_addresses

SOURCE_REF = "109610180033baa744a0fe80a2501e1ba2f73b00"


def proof():
    import z3
    z = z3.BitVec("z", 32)
    f = 9*z+0xFD7046C5
    expected = (f ^ z3.LShR(f, 16)) & 1
    actual = z3.LShR(0x80048000*z+0xA3628000, 31)
    solver = z3.SolverFor("QF_BV")
    solver.set(timeout=10000)
    solver.add(actual != expected)
    answer = solver.check()
    assert answer == z3.unsat, (answer, solver.reason_unknown())
    print(json.dumps(dict(full32_parity_identity="proved", z3_version=z3.get_version_string())), flush=True)


def make_source(args):
    if args.inputs_immediate:
        from experiments.iteration41_transfer import make_source as transfer_source
        from experiments.iteration41_transfer import SOURCE_REF as transfer_ref
        assert transfer_ref == SOURCE_REF
        source = transfer_source(argparse.Namespace(root="none", inputs_flow=False,
                                 inputs_immediate=True, full_policies=True))
    else:
        source = subprocess.check_output(["git", "show", f"{SOURCE_REF}:perf_takehome.py"], cwd=REPO, text=True)

    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:100])
        source = source.replace(old, new)

    if args.groups:
        replace('        final_xor_const = self.const_map[HASH_STAGES[-1][1]]', '''        final_xor_const = self.const_map[HASH_STAGES[-1][1]]
        hash41_multiplier = vector_const(0x80048000, "hash41_multiplier")
        hash41_bias = vector_const(0xa3628000, "hash41_bias")
        hash41_shift = vector_const(31, "hash41_shift")''')
        replace('                for stage, (op1, val1, op2, op3, val3) in enumerate(HASH_STAGES):', '''                hash41_tail = chunk_val
                hash41_shifted = chunk_tmp2
                hash41_read = hash41_ready = hash41_address = None
                for stage, (op1, val1, op2, op3, val3) in enumerate(HASH_STAGES):''')
        replace('''                            left, right,
                        )
                    elif val3 == 16:''', f'''                            left, right,
                        )
                        if chunk_no in {tuple(args.groups)!r} and round_no in {tuple(args.rounds)!r}:
                            # Distinct virtual vectors for the parity branch and
                            # full F preserve independent reads of the old z.
                            hash41_address = block_children_base + 128*batch_size + (round_no*chunk_count+chunk_no)*VLEN
                            hash41_tail = hash41_address + rounds*batch_size
                            # Early address readiness can start the next lookup
                            # while the full hash still runs. Its tmp2 gather or
                            # selects must not overwrite this hash's shift arm.
                            hash41_shifted = hash41_tail + rounds*batch_size
                            path_bit_uses.extend(((hash41_address, len(ops)), (hash41_tail, len(ops)),
                                                  (hash41_shifted, len(ops))))
                            hash41_read = emit("valu", ("multiply_add", hash41_address, chunk_val,
                                               hash41_multiplier, hash41_bias), val_ready)
                            hash41_ready = emit("valu", (">>", hash41_address, hash41_address,
                                                hash41_shift), hash41_read)
                    elif val3 == 16:''')
        replace('''                            (">>", chunk_tmp2, chunk_val, hash_constants[16]),''',
                '''                            (">>", hash41_shifted, hash41_tail, hash_constants[16]),''')
        replace('''                                    ("^", chunk_val, chunk_val, chunk_tmp2),
                                    val_ready,
                                    shifted,
                                )''', '''                                    ("^", chunk_val, hash41_tail, hash41_shifted),
                                    val_ready,
                                    shifted,
                                    hash41_read,
                                )''')
        replace('''                                "multiply_add",
                                chunk_val,
                                chunk_val,
                                hash_constants[multiplier],''', '''                                "multiply_add",
                                hash41_tail,
                                chunk_val,
                                hash_constants[multiplier],''')
        old = '''                parity_dest = chunk_idx if depth == 0 else chunk_tmp1
                if depth < retained_depth or (blocked_lookup and (depth == 4 or (compact_deep and depth == 6))):
                    # Unique logical producer per group/round; its storage is
                    # colored over all consumers without inserting copies.
                    parity_dest = path_bits_base + (round_no * chunk_count + chunk_no) * VLEN
                    path_bit_uses.append((parity_dest, len(ops)))
                if self.compact_lane_tail and depth in (5, 6, 7, 8, 9):
                    parity = [emit("alu", ("&", parity_dest+lane, chunk_val+lane, one),
                                   val_ready[lane]) for lane in range(VLEN)]
                else:
                    parity = emit_scalar_rhs("&", parity_dest, chunk_val, one, val_ready)'''
        replace(old, '''                if hash41_ready is not None:
                    parity_dest = hash41_address
                    parity = [hash41_ready]*VLEN
                else:
''' + "\n".join("    "+line for line in old.splitlines()))
        if args.late_constants:
            replace('        self.schedule(ops)', f'''        for op in ops:
            if (op.get("is_setup", False) and op["slot"][1] in
                    (hash41_multiplier, hash41_bias, hash41_shift)):
                op["chunk"] = {max(args.groups)}
                op["round"] = {min(args.rounds)}
        self.schedule(ops)''')
    if not args.full_policies:
        replace('        for policy in dict.fromkeys(policies):', '''        for policy in (
                "fragment_adaptive_tail_hetero_360_240_240_220_900",
                "fragment_adaptive_tail_hetero_360_220_140_140_920",
                "fragment_adaptive_tail_hetero_360_220_140_140_900"):
'''.rstrip())
    return source


def run(args):
    module = types.ModuleType("iteration41_hash_parity_probe")
    exec(compile(make_source(args), "<iteration41_hash_parity_probe>", "exec"), module.__dict__)

    class Builder(module.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)

        def allocate_node_lifetimes(self, ops, uses, reusable=()):
            self.logical_slots = [op["slot"] for op in ops]
            super().allocate_node_lifetimes(ops, uses, reusable)

    builder = Builder()
    result = dict(source_ref=SOURCE_REF, **vars(args))
    stage = "build"
    try:
        builder.build_kernel(10, 2047, 256, 16)
        stage = "emission"
        verify_emission(builder)
        stage = "scratch_provenance"
        verify_dataflow(builder)
        verify_workspace_addresses(builder)
        for seed in (123, 456, 789):
            stage = f"frozen_seed_{seed}"
            check(builder, seed)
        stage = "full_word_workspace"
        verify_words(builder, "full_word", [(i*0x9e3779b9)&0xffffffff for i in range(2047)],
                     [(i*0xabcdef01+0x80000000)&0xffffffff for i in range(256)])
    except (AssertionError, ValueError) as error:
        result.update(rejected=stage, detail=str(error), scratch=builder.scratch_ptr)
    else:
        slots = Counter()
        for bundle in builder.instrs:
            slots.update({engine: len(items) for engine, items in bundle.items()})
        result.update(cycles=len(builder.instrs), scratch=builder.scratch_ptr,
                      slots=slots, weighted=slots["valu"]+slots["alu"]/8,
                      verification="emission, scratch provenance, workspace addresses, 3 frozen seeds, full-word workspace")
        result["late_groups"] = {str(chunk): {
            "store": max(builder.issue_cycles[i] for i,op in enumerate(builder.operations)
                         if not op.get("is_setup", False) and op["chunk"] == chunk and op["engine"] == "store"),
            "final_load": max(builder.issue_cycles[i] for i,op in enumerate(builder.operations)
                              if not op.get("is_setup", False) and op["chunk"] == chunk and op["round"] == 15 and op["engine"] == "load")}
            for chunk in range(4)}
    if hasattr(builder, "schedule_policy"):
        result.update(policy=builder.schedule_policy, policies=len(builder.schedule_stats), schedule_cycles=len(builder.instrs))
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--groups", type=int, nargs="*", default=[0])
    parser.add_argument("--rounds", type=int, nargs="+", choices=(13, 14), default=[14])
    parser.add_argument("--full-policies", action="store_true")
    parser.add_argument("--proof-only", action="store_true")
    parser.add_argument("--late-constants", action="store_true",
                        help="Give the three new constants their actual consumer cohort/round priority")
    parser.add_argument("--inputs-immediate", action="store_true",
                        help="Compose the accepted independent flow.add_imm input-address transform")
    args = parser.parse_args()
    assert all(0 <= group < 32 for group in args.groups)
    if args.proof_only:
        proof()
    else:
        run(args)
