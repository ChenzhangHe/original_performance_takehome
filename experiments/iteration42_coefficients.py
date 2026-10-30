"""Trade depth3 cached selects for one affine interpolation per lookup.

For each sibling pair, runtime setup forms slope=even-odd modulo2**32.
Two independent two-bit selection trees choose the slope and odd value;
one multiply_add uses the newest 0/1 path bit. This uses six selects and
one MAC instead of seven selects. All setup, extra work and lifetimes count.
"""
import argparse
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from experiments import iteration42_addresses as address_probe

base_source = address_probe.make_source


def make_source(args):
    source = base_source(args)
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (old[:100], source.count(old))
        source = source.replace(old, new)
    setup = '''            depth3_pair_slopes = []
            for pair in range(4):
                vector = self.alloc_scratch(f"depth3_pair_slope_{pair}", VLEN)
                self.add("alu", ("-", vector, top_nodes+2*pair, top_nodes+2*pair+1))
                self.add("valu", ("vbroadcast", vector, vector))
                depth3_pair_slopes.append(vector)
'''
    if args.coefficients == 'all':
        start = source.index('            depth3_vectors = []')
        end = source.index('            depth3_shared = [top_nodes]', start)
        source = source[:start]+'''            # Cached lookups need four bases and four slopes. Padding
            # still copies the eight encoded node words directly below.
            depth3_pair_bases = []
            for pair in range(4):
                vector = self.alloc_scratch(f"depth3_pair_base_{pair}", VLEN)
                self.add("valu", ("vbroadcast", vector, top_nodes+2*pair+1))
                depth3_pair_bases.append(vector)
'''+setup+'''            depth3_vectors = depth3_pair_bases + depth3_pair_slopes
'''+source[end:]
        replace('''                                             depth3_vectors[parent ^ 1], readonly_zero))''',
                '''                                             top_nodes+parent, readonly_zero))''')
    else:
        replace('            depth3_shared = [top_nodes]', setup+'            depth3_shared = [top_nodes]')
    start = source.index('                elif blocked_lookup and depth == 3:')
    end = source.index('                elif depth == 3 and LOOKUP_DEPTH >= 3 and PAIR_LOOKUP_DEPTH >= 3 and DEPTH3_COEFF_SELECT:', start)
    original = source[start:end]
    # p0 chooses lower/upper half, p1 a sibling pair, p2 even/odd member.
    # Keep all lookup temporaries in the existing five-vector node span so
    # older path bits can be consumed before the previous hash completes.
    condition = 'blocked_lookup and depth == 3'
    if args.coefficients != 'all':
        condition += f' and round_no > forest_height and chunk_no < {args.final_groups}'
    branch = f'''                elif {condition}:
                    low, middle, high = path_bits[2], path_bits[1], path_bits[0]
                    bases = {'depth3_pair_bases' if args.coefficients == 'all' else '[depth3_vectors[2*p] for p in range(4)]'}
                    a, b, c, d = [chunk_node+i*VLEN for i in range(4)]
                    base_left = select(a, high[0], bases[0], bases[2], high[1])
                    base_right = select(b, high[0], bases[1], bases[3], high[1])
                    base = select(a, middle[0], a, b, base_left, base_right, middle[1])
                    slope_left = select(c, high[0], depth3_pair_slopes[0], depth3_pair_slopes[2], high[1])
                    slope_right = select(d, high[0], depth3_pair_slopes[1], depth3_pair_slopes[3], high[1])
                    slope = select(c, middle[0], c, d, slope_left, slope_right, middle[1])
                    selected = emit("valu", ("multiply_add", a, low[0], c, a), base, slope, low[1])
                    val_ready = emit("valu", ("^", chunk_val, chunk_val, a), val_ready, selected)
'''
    source = source[:start]+branch+(original if args.coefficients != 'all' else '')+source[end:]
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coefficients', choices=('all', 'final'), default='all')
    parser.add_argument('--final-groups', type=int, default=8)
    parser.add_argument('--addresses', choices=address_probe.MODES, default='independent')
    parser.add_argument('--exchange-groups', type=int, choices=(16, 17, 18), default=18)
    parser.add_argument('--full-policies', action='store_true')
    args = parser.parse_args()
    address_probe.make_source = make_source
    address_probe.build(args)


if __name__ == '__main__':
    main()
