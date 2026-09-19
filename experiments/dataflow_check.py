"""Check scheduled physical scratch provenance against original logical slots.

This is an instruction dataflow diagnostic, not another simulator or score.
Memory contents remain covered by the frozen simulator and workspace checks.
"""


def accesses(engine, slot, lanes=None):
    """(read/write, operand position, lane offset) for concrete scratch words."""
    def words(kind, pos, offsets=(0,)):
        return [(kind, pos, offset) for offset in offsets]
    vector = tuple(range(8)) if lanes is None else tuple(lanes)
    op = slot[0]
    if engine == 'alu':
        return words('w', 1)+words('r', 2)+words('r', 3)
    if engine == 'valu':
        result = words('w', 1, vector)
        if op == 'vbroadcast':
            return result+words('r', 2)
        for pos in range(2, 5 if op == 'multiply_add' else 4):
            result += words('r', pos, vector)
        return result
    if engine == 'flow' and op == 'vselect':
        return words('w', 1, vector)+sum((words('r', pos, vector) for pos in (2, 3, 4)), [])
    if engine == 'flow' and op == 'add_imm':
        return words('w', 1)+words('r', 2)
    if engine == 'load':
        if op == 'const':
            return words('w', 1)
        if op == 'load_offset':
            return words('w', 1, (slot[3],))+words('r', 2, (slot[3],))
        return words('w', 1, vector if op == 'vload' else (0,))+words('r', 2)
    if engine == 'store':
        return words('r', 1)+words('r', 2, vector if op == 'vstore' else (0,))
    raise ValueError((engine, slot))


def verify_dataflow(builder):
    expected, last = {}, {}
    for op_id, op in enumerate(builder.operations):
        original = builder.logical_slots[op_id]
        fields = accesses(op['engine'], original)
        expected[op_id] = {original[pos]+lane: last.get(original[pos]+lane)
                           for kind, pos, lane in fields if kind == 'r'}
        for kind, pos, lane in fields:
            if kind == 'w':
                address = original[pos]+lane
                last[address] = (op_id, address)
    cycles = [[] for _ in builder.instrs]
    for op_id in range(len(builder.operations)):
        if op_id in builder.offloaded_ops:
            for lane, cycle in enumerate(builder.lane_issue_cycles[op_id]):
                cycles[cycle].append((op_id, [lane]))
        else:
            cycles[builder.issue_cycles[op_id]].append((op_id, None))
    physical = {}
    for cycle, entries in enumerate(cycles):
        writes = {}
        for op_id, lanes in entries:
            op = builder.operations[op_id]
            original, renamed = builder.logical_slots[op_id], op['slot']
            for kind, pos, lane in accesses(op['engine'], original, lanes):
                logical_address, physical_address = original[pos]+lane, renamed[pos]+lane
                if kind == 'r':
                    wanted = expected[op_id][logical_address]
                    actual = physical.get(physical_address)
                    assert actual == wanted, ('scratch_read_provenance', cycle, op_id, op['round'],
                                              original, renamed, logical_address, physical_address, wanted, actual)
                else:
                    assert physical_address not in writes, ('same_cycle_scratch_writes', cycle,
                                                            physical_address, writes.get(physical_address),
                                                            (op_id, logical_address))
                    writes[physical_address] = (op_id, logical_address)
        physical.update(writes)


def guard_write_reuse(source):
    """Preserve same-cycle read/write reuse, but forbid two end-cycle writes."""
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:100])
        source = source.replace(old, new)
    replace('                                      address, start, end))',
            '''                                      address, start, end,
                                      any(times[id(o["slot"])] == max(times[id(t["slot"])] for t in touched)
                                          and addresses & self.instruction_accesses(o["engine"], o["slot"])[1]
                                          for o in touched)))''')
    replace('        last_use = {address: -1 for address in reusable}',
            '        last_use = {address: -1 for address in reusable}\n        last_write = {address: -1 for address in reusable}')
    replace('                    last_use[address] = max(last_use[address], times[id(op["slot"])])',
            '''                    last_use[address] = max(last_use[address], times[id(op["slot"])])
                if any(address <= a < address + VLEN for a in writes):
                    last_write[address] = max(last_write[address], times[id(op["slot"])])''')
    replace('        slots_end = [last_use[address] for address in reusable]',
            '''        slots_end = [last_use[address] for address in reusable]
        read_only_end = [last_write[address] < last_use[address] for address in reusable]''')
    replace('        for first, last, address, start, end in sorted(intervals):',
            '        for first, last, address, start, end, ends_with_write in sorted(intervals):')
    replace('            color = next((i for i, stop in enumerate(slots_end) if stop <= first),',
            '            color = next((i for i, stop in enumerate(slots_end) if stop < first or (stop == first and read_only_end[i])),')
    replace('                slots_end.append(last)\n                physical_slots.append',
            '                slots_end.append(last)\n                read_only_end.append(not ends_with_write)\n                physical_slots.append')
    replace('                slots_end[color] = last\n            physical =',
            '                slots_end[color] = last\n                read_only_end[color] = not ends_with_write\n            physical =')
    return source
