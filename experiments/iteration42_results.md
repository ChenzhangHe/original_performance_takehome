# Iteration42: rebalance load/flow on923, accept921 cycles

Date:2026-10-30. All new source transforms pin
`d014c591f63298a9b00971decebd6cc004d1ce29`, the pushed923 checkpoint.
The accepted production change is only the depth3 exchange count16→18.
It reaches921 cycles, scratch1400, with all236 existing policies.

## Cost the exchange before choosing its range

Each additional exchanged vector group replaces seven cached depth3 selects
with eight scalar gathers and one address-bias select. It adds eight loads,
removes six flow instructions and preserves arithmetic work. Recompute the
resource-plus-output-tail bounds rather than reusing the old graph's910.

| Exchanged groups | Policies | Cycles | Scratch | Loads | Flow | Load+tail bound | Flow+tail bound |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 16, control | 236 | 923 | 1416 | 1775 | 896 | 898 | 910 |
| 17 | 236 | 922 | 1392 | 1783 | 890 | 902 | 904 |
| 18, accepted | 236 | 921 | 1400 | 1791 | 884 | 906 | 898 |
| 19 | 3 | 925 | 1376 | 1799 | 878 | 910 | 892 |
| 20 | 3 | 929 | 1368 | 1807 | 872 | 914 | 886 |
| 21 | 3 | 933 | 1336 | 1815 | 866 | 918 | 880 |

The search range is bounded by work:22 groups would require1823 loads.
With the unchanged minimum11-operation output tail, the load bound is
ceil(1823/2)+10=922, already above921. Larger counts cannot beat this winner
without another structural change. Counts19–21 are measured screens, not
full-search results or claims of globally best schedules.

The17-group graph has a smaller maximum of these two bounds than18, yet
is one cycle slower. Necessary bounds prune impossible cases; they do not
predict the winning legal schedule. The18-group policy remains
`fragment_adaptive_tail_hetero_360_240_240_220_900`.

Accepted physical counts: load1791, VALU5415, ALU10621, flow884, store64.
Logical VALU5775/ALU7741 and work6742.625 are unchanged. There are360
offloaded vectors,260 fragmented, maximum inclusive fragment span43.
Flow issues0..894 with11 holes; last lookup910 and last store920 give921
elapsed cycles and the same ten-cycle drain. Body lookup count is1680,
first51. The workspace still holds256 encoded fields/248 distinct nodes.

## Alternate input-address graphs

Try replacing independent flow addresses with short pairs, or use three
vector subtracts to produce24 addresses after eight independent anchors.
The vector version additionally loads/broadcasts stride64. Every address
depends only on memory layout, never input/tree values.

| Address graph | Exchange groups | Policies | Cycles | Scratch | Work | Flow |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| All16 scalar pairs | 16 | 3 | 923 | 1448 | 6744.625 | 880 |
| Pairs in low24 groups | 16 | 3 | 923 | 1416 | 6744.125 | 884 |
| Pairs in high24 groups | 16 | 236 | 922 | 1456 | 6744.125 | 884 |
| Vector blocks of8 | 16 | 3 | 924 | 1448 | 6746.625 | 872 |
| High24 pairs | 17 | 3 | 924 | 1456 | 6744.125 | 878 |
| High24 pairs | 18 | 3 | 923 | 1416 | 6744.125 | 872 |
| Vector blocks of8 | 18 | 3 | 922 | 1408 | 6746.625 | 860 |

High-group pairs improve the old923 graph by one cycle, but do not compose
with the stronger exchange change. Vector address generation uses fewer
instructions than scalar chains but requires each eight-address block to
complete before its dependent loads can proceed. None enters production.

## Change cached lookup structure

For encoded sibling words even/odd, runtime setup can form
slope=even−odd modulo2**32. Two two-bit selection trees choose slope and
base, then `base + p2*slope` recovers the node for any32-bit values. This
uses six selects and one MAC instead of seven selects. All temporaries use
the existing node-vector span, preserving older hash values and real hazards.

| Interpolated cached depth3 lookups | Policies | Cycles | Scratch | Flow | Work |
| --- | ---: | ---: | ---: | ---: | ---: |
| All46 cached group/round pairs | 3 | 930 | 1424 | 838 | 6789.125 |
| Final traversal, low8 groups | 236 | 923 | 1424 | 876 | 6755.125 |
| Final traversal, low16 groups | 3 | 923 | 1424 | 868 | 6763.125 |

For the all-cache form, four base and four slope broadcasts replace the
eight original broadcasts; four scalar differences add0.5 equivalents and
the46 body MACs add46. Padding copies retain the actual eight encoded node
words from the scalar setup buffer. Partial forms retain the original cache
and add four broadcasts/four differences, costing4.5 setup equivalents plus
one per changed lookup. The larger flow reduction costs too much compute.
The best partial form remains923 with all236 policies, above921.

Two earlier small ideas were rechecked on the changed18-group graph:
direct root encoding removes one ALU but ties921; narrowing depth3 readiness
to its four relevant stores takes923, whether or not combined with direct
root encoding. Earlier first lookup44 does not yield earlier completion.
These are three-policy screens; neither additional change was promoted.

## Verification and scope

Each reported runtime candidate passes exact primitive/dependency/capacity
reconstruction, physical scratch provenance, exhaustive workspace address
checks, frozen seeds123/456/789 and a full32-bit output/workspace fixture.
Checks apply to the emitted shortest schedule, not every discarded policy.
No official tests, simulator semantics, capacities or inputs were changed.

Integrated921 additionally passes official9/9, built-in3/3,32 frozen seeds,
eight full-word fixtures, allocator/setup checks, six extra shapes and three
alternate path depths. The16/18 production controls exactly match the pinned
prototypes in instructions, logical slots/dependencies, first/final/lane
issue times, scratch and all236 policy timings. Historical acceptance40/41
explicitly select16 groups to preserve their927/924 and923/924 controls.

The default shell initially selected Python3.9, which cannot parse the
repository's existing match statements. Runs above use the installed
`/opt/homebrew/bin/python3.13` (3.13.11); no code was altered for that mismatch.

## What this means for900

The new fixed graph's strongest recorded resource-plus-tail bound is906,
from loads, versus910 from flow on923. It still cannot reach900 through
priorities alone. Keeping an11-operation minimum load tail would require
at most1780 loads, at least11 fewer than1791. The17-group alternative needs
at least three fewer loads and four fewer flow instructions to remove both
obstructions. Replacement work and startup must also fit the compute budget.
These are necessary conditions, not a demonstrated complete900 algorithm.

Reproduction commands are in the experiment README. Production imports none
of these source-transform probes. No leaderboard query or external benchmark
submission occurred.
