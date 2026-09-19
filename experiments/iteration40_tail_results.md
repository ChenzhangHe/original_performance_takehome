# Iteration40: real critical-chain feedback

Parent: `ee87c658e6247232801432d7ab5a548d324c096d`, **927 cycles**.
Probe: `iteration40_tail.py`. This script changes scheduling priority only;
instructions, real dependencies, ISA limits, and allocator rules stay intact.
Every retained result is checked by exact emission, scratch provenance,
frozen seeds123/456/789, and one full32-bit workspace/output fixture.
Intermediate feedback-pass lengths are diagnostic schedule lengths; the
shortest retained schedule is the one allocated and validated.

## The actual927 tail

Group0 stores at926, group1 at922, group2 at918, group3 at911. The final
group0 gather is at916; the remaining hash/store dependency chain occupies
exactly the following ten cycles with no ready-queue stalls.

The important earlier waits on the actual latest-predecessor chain are:

| Operation in group0 | Ready | First issue | Wait |
| --- | ---: | ---: | ---: |
| Round5 input XOR | 635 | 659 | 24 |
| Round7 input XOR | 716 | 739 | 23 |
| Round8 input XOR | 758 | 767 | 9 |
| Round11 first hash MAC | 843 | 861 | 18 |
| Round12 first hash MAC | 873 | 878 | 5 |

These waits are not additive elapsed savings. Promoting the waiting input
XOR often moves the wait to its dependent first MAC; promoting one hash arm
moves the wait to the other arm. Earlier broad critical-tail and whole-group
priority changes from iteration39 are not repeated here.

## Bounded feedback gives926 in isolation

Start from the best existing policy. Reconstruct first/last issue times of
all logical operations, including fragmented vectors. Follow the latest
producer backwards from the last store. Among operations in the late phase
(`round >= 10`), take the single largest ready wait and add240 to only that
operation's priority. Reschedule, retain the overall best, then repeat once.
No operation IDs are hard-coded; the trace selects the operation each time.

The initial experiment used issue time>=830. The semantic round>=10 gate
selects the same two operations and gives the same926 result.

1. Promote the round11 first MAC, whose measured wait was18. Candidate928.
2. Its new critical chain exposes the left affine hash-arm MAC, waiting11.
   Promote that operation. Candidate**926**, scratch1432, unchanged
   work6745.625. The complete existing policy set confirms this result.
3. Promoting the other affine arm gives927; extending the bounded feedback
   to six passes yields928,926,927,926,927,926. No further gain.

The926 schedule ends its last gather at915 and final store at925. It does
not remove the final ten-cycle chain or reduce arithmetic.

Other bounded controls (retaining the927 baseline when refinement loses):

| Feedback | Intermediate lengths | Retained |
| --- | --- | ---: |
| Top1 waiter after650, gain100, three passes | 927,927,927 | 927 |
| Top4 waiters after650, gain100 | 928,929,927 | 927 |
| Top4 waiters after830, gain200 | 927,927,927 | 927 |
| True late ancestor-chain deadlines2cycles earlier, two passes | 928,928 | 927 |
| Same with4cycles earlier | 928,928 | 927 |

The deadline controls cover only the measured late ancestor chain, not all
operations after a wall-clock threshold. They still do not improve elapsed
time, despite moving individual issues earlier.

## Do not integrate it on top of the simpler924 winner

The separate ALU/VALU agent's MAC-first priority reaches924/1424. Combining
that exact stable sort with this two-pass semantic feedback and running the
full existing policies stays **924/1424**. Both feedback passes also have
length924, so the original MAC-first schedule is retained unchanged.

The new longest late wait is a round10 gather: ready816, final lane issued828.
The first feedback pass promotes lane0, the second promotes lane1, with no
timing gain. A latest-predecessor chain names one arbitrary final lane of an
eight-load frontier; promoting one lane can merely make a different lane
last. A future feedback method would need to recognize complete consumer
frontiers, not treat every reported single-operation wait as removable.

Recommendation: retain the simpler MAC-first924 implementation and leave
this more complex feedback mechanism as an independently reproducible
research control. Production was not edited by this probe.

```sh
python3 -B experiments/iteration40_tail.py --inspect
python3 -B experiments/iteration40_tail.py --feedback-passes 2 --feedback-gain 240 --feedback-min-round 10 --full-policies
python3 -B experiments/iteration40_tail.py --feedback-passes 2 --feedback-gain 240 --feedback-min-round 10 --mac-first --full-policies --inspect
```
