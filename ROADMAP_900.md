# Roadmap toward 900 cycles: measure, rebalance, remove gathers

Latest accepted implementation, iteration40 (2026-09-19): **924 cycles**, scratch
**1,424/1,536**, unchanged work6745.625. This checkpoint bundles the verified
three-cycle improvement and research records over the pushed927 parent
`ee87c658e6247232801432d7ab5a548d324c096d` on `optimize/kernel-v2` in the user's
fork. Official9/9, built-in3/3,32 frozen seeds,eight full-word fixtures,
emission/provenance, allocator/workspace checks, six extra shapes and three
alternate path depths pass. No official tests or simulator were modified.

The accepted change is an eligibility-aware stable sort: ready fused
`multiply_add` operations receive VALU capacity before ordinary vectors in
fragmented policies. Scalar ALUs cannot issue FMA, but can perform the binary
operations. Thirteen additional vector operations move to scalar issue,
without removing work or changing the dependency graph. Physical counts:
load1783, VALU5417, ALU10629, flow864, store64. `--compact-fma-priority 0`
exactly restores927;1 gives924. The rule is gated by compact flow exchange,
so generic and exchange-disabled paths retain their previous behavior.

First/last flow14/898 has21 holes, versus24 on927. The last gather advances
916→913; the final store is at zero-based cycle923, for924 elapsed cycles.
The ten-cycle drain is unchanged. Combined lookup traffic remains1664,
first/last54/913, conditional finish bound886. The physical VALU count alone
has floor903; combined compute has an optimistic900 floor and a903 bound
conditioned on the measured startup. Neither is an achieved900 schedule.

Bounded alternatives did not add to the winner. Critical-chain feedback
gets926 by itself but stays924 with FMA priority. Narrowing shallow lookup
readiness to its four relevant stores gets926 alone, but925 with FMA priority.
Completing fragmented vectors within one cycle ties924 and adds scheduling
complexity. Speculatively fetching both possible depth3 children takes935
and increases work. Do not combine independent local gains arithmetically.

Next, in order:

1. Treat the entire consumer frontier as the scheduling unit when examining
   the tail. Its current longest wait names one lane of an eight-load gather;
   promoting that lane merely changes which lane finishes last. Measure
   advancement of all required producers and the end-to-end store.
2. Create real arithmetic margin by changing hash expression topology or
   finding costed cross-item reuse. Pre-encoding all depth8 nodes adds32 setup
   XORs to remove32 body XORs, plus64 memory slots, and displaces useful
   records. It has zero net arithmetic saving before those extra costs.
3. Do not keep tuning constants in the existing final three-MAC/XOR topology.
   A low16-bit UNSAT necessary condition excludes raw-output absorption in
   that template even with arbitrary multipliers, biases and stage1 XOR
   encoding. This is not a proof that the complete hash is globally optimal.

There are still24 elapsed cycles to900. The new flag and pinned experiments
make the924 result reproducible; no leaderboard query, external submission
or global-record claim was made. See iteration40 in the log and
`experiments/README.md` for results, exact controls and proof boundaries.

The following iteration39 status and its budget are historical.

Accepted implementation, iteration39 (2026-09-18): **927 cycles**, scratch
**1,432/1,536**, unchanged work6745.625. The928 baseline was committed and
pushed as `161c60200be0aa071532f6784817a5b562e9c2c8` to the user's fork on
`optimize/kernel-v2`. This checkpoint bundles the927 implementation and
research records. All new isolated probes pin the pushed928 source.
Official9/9,built-in3/3,32 frozen seeds,eight full-word fixtures,provenance,
allocator/workspace checks and all extra shapes/path depths pass.

Prioritize the complete ancestor DAG of the highest TWO groups' first
round1 vselects, ordered by longest remaining distance to those targets.
Do not merely prioritize their input loads. This changes neither operations
nor dependencies. First flow moves19→14; flow ends901 rather than902,
last gather916 rather than917, and completion926 rather than927. There are
24 flow holes instead of20: most of the earlier start is absorbed by later
waiting, so the elapsed win is one cycle, not five. Starting one group can
move first flow to13 but still takes930. Thus19 was never a hardware minimum.
With FMA priority disabled, `--compact-startup-groups 0` restores928;2
enables927. Generic paths and the exchange-disabled control retain their
prior scheduling behavior.
Physical slots are load1783, VALU5430, ALU10525, flow864, store64. The ideal
aggregate compute floor remains900, but the startup-conditioned bound for
this schedule is903. No900-cycle execution or global-best score is claimed.

The late trace is more specific now: the last group's round13 hash feeds
the final depth3 select at902, round14 hash/address construction, last
depth4 gather at917, and hash/store completion at927. The ten-cycle drain
itself has no late scheduling stalls to remove; advance its producer chain.
Of the20 flow holes,16 are early (62..131) and four are at898..901. This
does not mean20 removable elapsed cycles: other engines keep working.

Rejected on the new graph: exchanging loads/flow in the second traversal
takes930–945;18 first-traversal exchange groups take930 with all policies.
Legal zero-lag landing WAR takes929 despite overlapping877 reader/writer
edges. Moving all24 input-address ALU operations to flow.add_imm removes3
compute equivalents but takes929 and raises flow to888; those extra slots
must still fit around select readiness. Cross-root XOR reassociation takes930.
Do not promote these local savings without an end-to-end gain.

Next use the balanced927 startup as the control, then address the laggard's
real producer chain or remove further body work with startup margin. More
exchange groups, blanket critical-path priority, and input bootstrap alone
have been tested and are not demonstrated improvements. Do not assume
independent local savings add together. See iteration39 in the log and the
pinned scripts for reproducible evidence and exact production comparison.

The following budget describes the pushed iteration38 baseline, **928 cycles**,
scratch **1,440/1,536**,
parent `34742f6` (941). This is a **13-cycle** improvement. Official9/9,
built-in3/3,32 frozen seeds,eight full-word fixtures,emission/provenance,
allocator boundaries and six extra shapes/three alternate path depths pass.

The important change is a measured resource exchange, not another hash
rewrite. Shallow direct child landing removes30 net compute equivalents.
Use eight of the shallow record padding words for encoded depth3 nodes;
the other eight retain the final pair from preprocessing. For the highest
16 groups in the FIRST traversal, replace seven cached selects with eight
scalar gathers and one complete address-bias select. That releases96 flow
slots without adding body compute. Spend64 flow slots on depth8/9 bias
selection, eliminating64 more compute equivalents; new setup costs6.

Net versus941: **-88 compute equivalents, +135 loads, -32 flow**. Final
slots: load1,783, VALU5,432, ALU10,509, flow864, store64. Weighted compute
is **6,745.625**, versus the ideal900-cycle total capacity of6,750. Its
optimistic aggregate floor is now **900**, with no aggregate compute/load
deficit. This does NOT show that900 is schedulable: the margin is only4.375
equivalents, and even startup-conditioned ideal compute finishes at902.

Important correction to the older budget: flow count896 did not mean
900 had four usable spare slots. The941 kernel's first flow is at19, so
its conditional finish bound is915. In928, first/last flow is19/902,
count864, conditional finish883;20 idle flow cycles remain. Combined
lookup traffic is1,664 slots, first/last53/917, conditional finish885,
then10 cycles of drain. All are conditional on the measured start times,
not proofs of a globally optimal bound.

The final928 comes from the full existing policy set: delay the priority
of depth8/9 bias SELECTS by one logical round, without delaying their MACs
or changing dependencies. This wins two cycles over the930 full-policy
control. Three-policy screening missed it (932): validate structural
winners with the complete policy set. Setup deadline cap6 reduces storage.

Next, in order:

1. Reduce startup/tail losses on this cheaper graph. Inspect flow's20 holes,
   load readiness and the last10 cycles; preserve the16-group exchange as
   control. Moving all address operations together did not help.
2. Seek additional REAL work removal to create startup margin. The current
   aggregate900 budget is essentially full. More depth3 gathers do not
   lower compute;18 groups already use1,799 loads, leaving only one of the
   nominal1,800 slots. More gathers are not free progress.
3. Rebalance vector versus scalar issue only with complete lane/provenance
   checks. The current physical VALU count alone has floor906; ideal total
   capacity assumes further useful offload and no startup waste.

Workspace contains256 encoded fields representing248 distinct nodes;
there are no zero padding words. Header/forest remain untouched. The hash
is unchanged. No leaderboard query or benchmark submission. The928 baseline
has since been committed and pushed as noted above.
See iteration38 in the log for formulas, A/B and reproduction commands.

The implementation status below is historical.

Latest accepted implementation: iteration 37, **941 cycles**, scratch
**1,480 / 1,536**, parent `2d5bc94` (954). The 13-cycle improvement removes
real lookup/copy work; the ten-operation hash is unchanged.

Keep the 64-word depth4/5 table, replace the linear depth6 copy with 192
words of stride-3 depth6/7 records, and stop caching the final depth4 round.
The complete 256-word index workspace holds 240 encoded nodes and 16 zero
padding words. Odd-stride address conversion is one modular multiply-add,
not division. Select complete address biases at the PREFETCHED parent
rounds4/6, where the following child hash hides the dependency; this is
better than putting the same selects immediately before gathers8/9.

Release terminal hash/parity consumers lane by lane. Then reorder only the
deep records to `[left,parent,right]`: overlapping loads land the left
children directly, deleting another 32 compute equivalents. This old idea
now wins on a graph with load headroom. Protect its entire 16-word landing
span as one allocation and retain strict load/reader dependencies.

Slots: load **1,648**, VALU **5,503**, ALU **10,645**, flow **896**, store
**64**. Weighted compute **6,833.625**, optimistic combined compute floor
**912**. Versus954: **-77.125 compute equivalents, -181 loads, +5 flow,
+16 stores, +3 scratch**. Necessary900 deficits are now **83.625 compute
equivalents and zero loads**; flow has just four aggregate spare slots.
There are still 41 elapsed cycles to900. No complete route is demonstrated.

Combined body lookup traffic is512 record vloads +1,024 scalar gathers
=1,536, first/last **53/930**, drain10. The conditional lookup finish bound
is821: load throughput is no longer the aggregate obstacle. Compute and
flow readiness dominate. Do not mistake smaller lookup traffic for a
guaranteed900 schedule.

This iteration also fixed scratch reuse when an OLD lifetime ends with a
write: it cannot share a cycle with the NEW lifetime's first write. Legal
same-cycle old-read/new-write reuse remains. A new provenance checker
verifies every physical scratch read and rejects write collisions; dedicated
boundary fixtures cover both rules and contiguous spans. This exposed
invalid prototype combinations that are not counted as valid timings.

Next, in order:

1. Cost shallow-record direct landing on THIS load-light graph. Its ceiling
   is approximately another30 net equivalents, not enough alone for900;
   include final-round parent offset, buffer serialization and the56-word
   scratch margin. Keep the accepted deep landing as the control.
2. Find at least another84 net compute equivalents without consuming more
   than four net flow slots. The earlier SMT search excluded eight precise
   encoding/fusion templates, not all shorter hashes. Broaden the expression
   family only with an explicit ISA/endpoint/path-bit budget.
3. Reduce compute/flow waiting on the cheaper graph. Full sorting is not a
   free locality optimization: the scalar-scatter four-pass radix model
   alone requires1,152 store cycles including restoration. Other routing
   algorithms remain open, but need a concrete storage and movement plan.

Official9/9, built-in3/3, 32 frozen seeds, eight full-word fixtures, exact
emission/provenance/workspace checks, six extra shapes and three alternate
path depths pass. Reproduce954 with `--blocked-compact-deep 0`; disabling
only `--compact-deep-landing 0` gives944. `BLOCKED_FINAL_CACHE_CHUNKS`
continues to control the OLD path; compact records deliberately disable
that cache. No tests/simulator changes, leaderboard query or submission.
See iteration37 of the log and pinned scripts for accepted and rejected work.

The implementation status below is historical.

Latest accepted implementation: iteration 36, **954 cycles**, scratch
**1,477**, parent `3d24666`. This is only **one cycle** faster than955.
Runtime-encode the64 depth-6 nodes after the existing64-word record table;
fold entry/exit rebasing into the existing address biases. Four-address
descending chains remove eight constant loads, and initial input loads
carry round-0 priority. On this cheaper graph, a later drain phase at920
instead of900 saves the elapsed cycle; the old policies remain candidates.

Slots: load **1,829**, VALU **5,599**, ALU **10,494**, flow **891**, store
**48**. Weighted compute **6,910.75**, optimistic compute floor **922**.
Versus955: **-20.25 compute equivalents, +3 loads, +8 stores, +18 scratch**.
Necessary900 deficits are now **160.75 compute equivalents and29 loads**;
flow still has only nine spare slots. This improves the compute budget but
worsens the aggregate load budget slightly. There are **54 cycles** to900,
not a demonstrated complete route.

The workspace is128 index words:48 record nodes,16 padding zeros and64
contiguous depth-6 nodes. The remaining128 index words, header and forest
are untouched. Record gathers wait only for record stores; depth-6 gathers
wait for their own copy stores. Combined lookup count remains1,736,
first/last **53/943**, drain10, conditional finish bound921.

The larger reductions did not win: two/four independent child-landing
chains still take961/957 on the old955 graph, even without changing record
order. Deeper6/7 encoding takes963; using flow selects to remove32 deep
index subtractions takes976 despite31 fewer compute equivalents. Moving
the node XOR off the gather dependency chain also regresses. See the full
tables and six pinned reproduction scripts in iteration36 of the log.

Next require a costed body-level change, including its new dependencies,
not just fewer instructions. More final caches, longer address chains,
same-cycle WAR and earlier decoding have now been rechecked on955. Do not
repeat them unchanged. Another packed two-level record layout needs an
explicit copy/merge, flow, address and live-storage budget before coding;
remaining workspace alone is not evidence it will help. Keep the cheaper
depth-6 graph as an A/B control when studying load/compute balance.

Official9/9, built-in3/3, 32 frozen seeds, eight full-word fixtures,
emission/capacity/dependency checks and all extra shapes pass. Reproduce
955 with `--blocked-encode-depth6 0 --blocked-reverse-input-chain-length 0
--blocked-tail-start 900`; defaults give954. No test/simulator change,
leaderboard query or external benchmark submission this iteration.

The implementation status below is historical.

Latest accepted implementation: iteration 35, **955 cycles**, scratch
**1,459**, parent `468f713`. This is **14 cycles faster** than 969. Enable
consumer-round setup priorities on the blocked graph, order the final
coefficient selects by early path bits, and omit one unused address-weight
load/broadcast. The first combined lookup moves **67 -> 54**. No hash or
body lookup-count reduction is claimed.

The changes interact: setup priorities alone give960; early-tail selection
alone ties969; removing the unused weight alone regresses to971. Setup
priorities + early selection + weight removal give **955**. All eight A/B
combinations are in iteration35 of the log and exposed by tuning switches.
Keep semantic setup identity separate from its scheduling round; otherwise
deferred setup vloads pollute the first-lookup metric. The analyzer and
local verifier now check that distinction.

Slots: load **1,826**, VALU **5,604**, ALU **10,616**, flow **891**, store
**40**. Weighted compute **6,931**, optimistic compute floor still **925**.
Necessary aggregate deficits at900 are **181 compute equivalents and
26 loads**, with nine spare flow slots. Combined lookup traffic remains
**1,736**, first/last **54/944**, drain10, conditional finish bound922.
There are still **55 elapsed cycles** to900. The one-equivalent work saving
is much smaller than the elapsed-cycle improvement; readiness was the win.

Next, in order:

1. Reduce body work on this new readiness graph. Direct child landing has
   now been remeasured here: **963**, or **961** with same-cycle WAR,
   despite saving30 net compute equivalents. Its load chain is still too
   serial. A partitioned/banked landing proposal must show how it preserves
   load concurrency, counts any merge/copy cost, and fits live scratch.
2. Require a joint compute/load reduction for the remaining900 budget.
   More final caching alone is not a route: eight groups take961 and
   flow904, already over the900 flow budget; six groups take962.
3. Revisit setup/readiness alongside a genuinely changed body, with the
   existing eight-way A/B as the control. Delaying setup all the way to
   round15 needs1,595 scratch words; the limit remains1,536. Do not trade
   away the new startup gain or bypass the storage constraint.

Official9/9, built-in3/3, 32 frozen seeds, eight full-word fixtures, exact
emission, workspace/padding, six extra shapes and three alternate path
depths pass. Generic paths retain their previous cycles. No leaderboard
query or submission this iteration. Full evidence and rejected probes are
preserved in `OPTIMIZATION_LOG.md` and `experiments/`.

The implementation status below is historical.

Latest accepted implementation: iteration 34, **969 cycles**, scratch
**1,475**, parent `1d27efc`. Fuse encoding into the 48 scalar copies that
already transpose the runtime records. This deletes six setup vector XORs
without adding copies. It saves **one elapsed cycle**, not six. Exact A/B:
`--blocked-fuse-setup-xor 0 1` gives **970 / 969** with other defaults.

Slots: load **1,827**, VALU **5,631**, ALU **10,408**, flow **891**, store
**40**. Weighted compute **6,932**, optimistic compute floor **925**.
Necessary aggregate deficits at 900 remain **182 compute equivalents and
27 loads**, with only nine spare flow slots. First/last combined lookup
load **67/958**, 1,736 lookup loads, drain10; conditional finish bound935.
There is still no validated complete route to 900.

The larger body-reduction experiments did NOT beat 970:

- Direct left-child landing removes 30 net compute equivalents, but takes
  981 cycles; allowing verified same-cycle buffer reads/overwrites reaches
  974, still slower. Applying that scheduler mechanism to the old layout
  takes 972. Neither the contiguous allocator nor the same-cycle scheduler
  is in the accepted kernel.
- Moving records to depths5/6 with a separate depth-4 copy takes 979 with
  no first cache and seven final cached groups. Eight first cached groups
  need 1,548 scratch words even without final caching (limit1,536).
  Do not expand coverage without a new live-storage and readiness plan.
- Reusing the cache's parent loads saves two more loads but takes 973;
  lower aggregate work alone remains insufficient.

Next require both lower work and a non-serial load/consumer layout. A
single contiguous child-landing vector still serializes the eight loads;
same-cycle WAR handling alone did not solve this. The two body probes and
their pinned parent are preserved in `experiments/` for reproducibility.
Hash is unchanged. Full official, frozen, full-word, extra-shape and memory
acceptance passes; no leaderboard query or submission this iteration.

The implementation status below is historical.

Latest accepted implementation: iteration 33, **970 cycles**, scratch
**1,475**, parent `fa65372`. This is a structural improvement, not a new
scheduling-policy sweep. Runtime four-word records hold an encoded depth-4
parent and both depth-5 children. Four independent read buffers per group
prefetch both levels; a retained parity selects the child without another
gather. Fuse the parent's buffer copy with its input XOR. Use the freed flow
budget to replace depth-2/3 interpolation MACs with early-bit-first pure
selection, and cache the final depth-4 lookup for the highest seven groups.

Slots: load **1,827**, VALU **5,638**, ALU **10,400**, flow **891**, store
**40**. Weighted compute **6,938**, optimistic combined compute floor **926**.
Versus 987: **-17 cycles, -184.5 compute equivalents, -86 loads, -48 flow**,
with two more stores and two more scratch words. The workspace holds 48
encoded nodes plus 16 zero padding words; the remaining 192 index words,
forest and header remain unchanged. There is no input-dependent Python
precomputation. Generic shapes and alternate direct-path depths use the old
implementation.

At 900, necessary aggregate deficits are now **188 compute equivalents**
and **27 loads**. Flow fits by just nine slots, before timing constraints;
it is not an unlimited resource. The ten-instruction hash is unchanged.
This is progress toward 900, not a demonstrated complete route to it.

Important diagnostic correction: `load_offset` alone no longer counts all
node lookup traffic. The accepted schedule has **256 record vloads + 1,480
scalar gathers = 1,736 lookup loads**, first/last **68/959**, drain **10**.
At that fixed first-lookup time, their conditional finish bound is **936**.
Even the remaining 27-load aggregate reduction would not alone ensure 900;
startup and body readiness must also improve. `analyze_kernel.py` reports
both the narrow gather metric and the combined lookup-load metric.

Next, in order:

1. Reduce the two child-copy vectors per blocked group (64 compute
   equivalents total), or fuse their consumption, without restoring scalar
   selection congestion or serializing vloads. The parent-copy/XOR fusion
   already saves 32 equivalents and must not be counted again.
2. Co-design another record/lookup reduction against the remaining compute
   and load deficits. Budget runtime rearrangement, padding, scratch and
   addresses first; do not assume wider records are automatically cheaper.
3. Address the 68-cycle first-lookup startup only on that cheaper graph.
   The first serial-buffer prototype took 1,000--1,016 despite fewer
   instructions; parallel readiness and high-group final caches were needed
   to turn the reduced work into a real gain.

Reproduce **987 / 977 / 970** with blocked lookup disabled / enabled without
parent-XOR fusion / enabled with fusion, respectively. Controls are
`--blocked-lookup`, `--blocked-read-banks`, `--blocked-final-cache-chunks`,
and `--blocked-fuse-parent-xor`. The older preencoding and depth-4 cache
controls govern only the fallback while blocked lookup is enabled.
Use `python3 verify_kernel.py --extra-shapes` for the durable full local
acceptance suite. No leaderboard query or submission this iteration.

The implementation status below is historical.

Latest accepted implementation: iteration 32, **987 cycles**, scratch
**1,473**, parent `3f2f4e2`. Direct gather addresses finally win when their
constants are derived from shared scalars and the copied range is reduced
to depths 4--5 (48 nodes). Before the first gather, accumulate weighted path
bits into A; afterward prepare `2*A+bias` while hashing and subtract parity.
Copied-address and exit biases are explicit. Reuse dead address-constant
vectors after their last scheduled access; this lowers the initial candidate's
scratch from 1,497 to 1,473 without changing its issue schedule.

Slots: load **1,913**, VALU **5,814**, ALU **10,468**, flow **939**, store
**38**. Net versus iteration 31: **-10 loads**, **-30.625 compute equivalents**,
**-8 stores**, but only **ONE elapsed cycle**. Weighted work **7,122.5**,
optimistic compute floor **950**. Necessary 900-cycle deficits are now
**372.5 compute equivalents, 113 loads, 39 flow**; 87 elapsed cycles remain.
First/last gather **54/976**, drain 10. These are bounds on our current work,
not evidence that the problem itself cannot reach 900.

The important result is representation/setup co-design, not a claim that
direct addresses alone are faster. Independently loaded address constants,
tail-only addresses, more cached groups, constant-load synthesis, scalar
index FMAs, and narrow per-lane parity release did not beat 988. The log
records their measured tradeoffs. Next improvements must include both body
cost and setup/readiness; avoid repeating those combinations unchanged.
Use `--direct-gather-addresses 0 --node-preencode-depth 6` to recover the
988-cycle parent. The generic and non-direct lookup paths retain S=5-A.
Full official, frozen-reference, schedule-emission, extra-shape and memory
boundary acceptance passes. No leaderboard recheck or submission this turn.

The implementation status below is historical.

Latest accepted implementation: iteration 31, **988 cycles**, scratch
**1,440**, parent `0c89047`. This is a six-cycle improvement, not an
operation-count reduction. Input-address anchors now carry their consuming
group's round-0 scheduling metadata. This moves the first gather from 61
to 55 and alone reaches 990. Lane-fragment ALU issue uses gaps smaller than
eight slots and brings the combination to 988. All old whole-vector
policies remain candidates; fragmented consumers wait for ALL lanes, and
register lifetimes span first issue through last completion.

The causal A/B is **994 / 993 / 990 / 988** for neither change / fragments
only / input priority only / both. Slots: load **1,923**, VALU **5,842**,
ALU **10,489**, flow **939**, store **46**. Weighted compute stays
**7,153.125**, with the same optimistic floor **954**. Necessary deficits
at 900 remain **403.125 compute equivalents, 123 loads, 39 flow**. First/last
gather is **55/977**, drain 10; 88 elapsed cycles remain to reach 900.

Next: separate startup latency from steady-state work. The old input
anchors' dummy group metadata delayed useful loads; deleting more operations
did not fix that. Conversely, better slot filling has NOT solved the body
budget. Larger caches, arithmetic-for-select tradeoffs, direct addresses,
and preencoding load reuse were tested again in bounded combinations and
did not beat this result. Before adding more issue rules, require a measured
reduction in the load/compute/flow deficit, including setup. Use the new
`verify_kernel.py` to check exact emitted slots, cross-cycle dependencies,
32 frozen-reference seeds, and eight full-32-bit memory-boundary fixtures.
Both changes can be disabled with their named switches in `tune_kernel.py`.
No leaderboard recheck or submission was performed in this iteration.

The implementation status below is historical.

Latest accepted implementation: iteration 30, **994 cycles**, scratch
**1,432**, parent `95ca48d`. Runtime preprocessing encodes the 112 tree
nodes at depths 4--6 into the first 112 words of the unused input-index
region. The forest is unchanged. Gather addresses are rebased with a
different existing subtraction constant, and 101 repeated per-vector node
encodings disappear. Including 14 vector copies/XORs and 26 scalar pointer
updates, net compute falls by **83.75 vector-equivalents**. Setup priorities
follow first use; memory dependencies remain explicit. Full acceptance and
memory-boundary checks pass. Disable with `NODE_PREENCODE_DEPTH = 0` to
recover the 995-cycle parent behavior without this workspace usage.

Current work: **7,153.125** compute equivalents, **1,923 loads**, **939 flow**,
**46 stores**. This improves the optimistic compute floor from 965 to 954,
but raises the load floor from 953 to 962. Actual cycles improve by only
ONE: do not present the operation reduction as a comparable elapsed-time
gain. First/last gather 61/983, drain 10. At 900 the remaining necessary
deficits are **403.125 compute equivalents, 123 loads, 39 flow operations**.

Next gate: do not enlarge preprocessing just because it removes more XORs.
Depths 4--7 remove more compute but add too many loads; additional cached
groups, coefficient-first lookup mixes, early ALU reservation, and static
hash offload were tested without further gains. The binding question is how
to remove gather traffic AND body work without adding flow congestion or a
longer startup. Direct-address and logical-hazard reconstruction experiments
also failed to beat the parent. See iteration 30's rejection table before
repeating these approaches. The 994-cycle result is still 94 cycles from
900, and 125 from the **869 snapshot**, which has not been rechecked here.

The entries below, including their "current" budgets, are historical.

Latest accepted implementation: iteration 29, **995 cycles**, scratch
**1,421**, parent `9466e38`. Paired input-address anchors remove 15 net
load slots; scalar parity constants remove one setup broadcast. Official,
built-in, 32-seed and six-extra-shape acceptance passes. Longer address
chains, wider one-hop address fans, and vectorizing parity/address decoding
were tested and rejected. See the log for measured results.

Current work: **7,236.875** compute equivalents, **1,906** loads and **939**
flow operations. This is one MORE compute equivalent than iteration 28,
but better load/compute balance lowers elapsed cycles by three. Necessary
deficits at 900 remain **486.875 compute equivalents, 106 loads, 39 flow**,
before startup and dependencies. First/last gather: 60/983; drain: 11.

### Public target calibration — checked 2026-09-10 PDT

The community leader is now **869**, not approximately 1,000. Both
[Paradigm's board](https://www.paradigm.xyz/puzzles/anthropic-challenge)
and the [VLIW board's Without Indices category](https://vliw-challenge.fly.dev/)
show 869. Paradigm's tenth entry is 900 and `@zartbotF` is twelfth at 908.
VLIW's separate With Indices leader is 899; our kernel does not output final
indices, so that category is not a valid direct comparison. These are
community judge results, not Anthropic's unpublished best-human result.
See [LEADERBOARD_NOTES.md](LEADERBOARD_NOTES.md) for timestamp and sources.

Our local 995 needs 95 fewer cycles to reach 900 and 126 to reach 869.
No solution was submitted to either board. The current compute floor of
965 is a property of OUR emitted work, not a lower bound on the problem.
Do not use it to argue that the published 869 is impossible.

Next prioritize **joint lookup/representation and instruction selection**:
seek net body-operation deletions, explicitly budget setup and live storage,
then choose which engine executes the surviving work. Corsix's
[analysis](https://www.corsix.org/content/anthropics-compiler-challenge)
emphasizes balancing ALU/VALU, load and flow in each cycle, not only in
aggregate; our paired-anchor win and long-chain regressions illustrate that
distinction. The diagram's hash fusion is already present in our kernel;
do not count it as a new opportunity. Keep hash search bounded and require
full 32-bit equivalence for any proposed identity. Small initialization wins
alone do not constitute a route to 900.

The previous status entries below are historical checkpoints.

Latest accepted implementation: iterations 27/28, **998 cycles**, scratch
**1,436**. This session improves 1,004 -> 1,001 -> 998. A single group uses
the final-round depth-4 cache, allowing its entire second traversal's index
construction to disappear. Dead index storage is reused after its last
access. A conservative post-build pass removes 11 unread constant loads.
Full official, built-in, 32-seed and six-extra-shape acceptance passes.

Current work is 7,235.875 compute equivalents, 1,921 loads and 939 flow
operations. Necessary reductions at 900 are still 485.875 compute
equivalents, 121 loads and 39 flow operations, before dependency/startup
costs. Sub-1,000 is achieved; 900 is not. Direct positive-address construction,
root MACs, larger final caches and early-bit lookup variants were tested and
rejected; see the log before repeating them. Future structural changes must
include a post-build dead-code audit, not just a body-instruction budget.

Latest accepted implementation: iteration 26, **1,004 cycles**, scratch
**1,460**. This session improves 1,037 -> 1,017 -> 1,013 -> 1,004.
Path-parity reuse, direct interpolation/early lookup and bounded ALU issue
reservation are accepted; full correctness checks pass. Hash probing found
no valid shorter expression in its limited templates. Older status below
is historical; operation-count claims must use the latest baseline.

The bounded scheduling pass is complete. Current work: compute 7,239.875
vector-equivalents, load 1,940, flow 926. Reaching 900 still necessarily
requires removing 489.875 compute equivalents, 140 load slots and 26 flow
slots; these reductions alone do not guarantee the target. Prioritize a
different state/expression transformation or lower-cost lookup, accounting
for all setup and conversions. Preserve `hash_fusion_probe.py` as a narrow
rejection tool, not a proof that the ten-instruction Hash is optimal.

Latest accepted implementation: iteration 25, **1,013 cycles**, scratch
**1,468**. A is complete; B now uses direct parity interpolation with early
coefficient selection. It preserves incremental index updates: delaying
their reconstruction alone offers no operation-count saving on this scored
shape. Weighted compute is 7,237.875 (optimistic floor 966), load 1,948
(floor 974), flow 915. Full acceptance passes. C's first bounded template
pass found no shorter Hash; see `hash_fusion_probe.py` and the log.

Next: examine issue balance on this changed DAG with a bounded experiment,
then seek a different expression/state transformation or lower-cost lookup.
At 900 the remaining necessary deficits are 487.875 compute equivalents,
148 load slots and 15 flow slots. Do not count early lookup as eliminated
address computation or treat the rejected Hash templates as a minimality proof.

Latest accepted implementation: iteration 24, **1,017 cycles**, scratch
**1,453**. Plan A below is implemented: retained parity eliminates all
2,112 scalar masks at the old cache coverage with no copies. Retuning to
26 cached chunks yields weighted work 7,245.125, load 1,941, flow 926.
Full acceptance passes. Next is plan B, starting with direct interpolation
separately from address reconstruction. Earlier baselines below are historical.

Current accepted result: iteration 23, **1,037 cycles**, scratch **1,357**.
Negative indices plus shared-mask quartet interpolation allow 24 cached
round-4 chunks. Full acceptance passes. Floors: load 979, VALU 1,016,
ALU 941, flow 904. Earlier "current" paragraphs below are historical.

### Next gate: reduce the DAG, not just issue time

The optimistic combined compute bound is now 1,001 cycles:
`ceil((6094 + 11289/8)/(6 + 12/8))`. Reaching 900 needs at least about 755
fewer vector-equivalent ALU/VALU operations, 157 fewer load slots, and four
fewer flow slots, even before dependency/startup costs. These are necessary,
not sufficient, reductions. Stop broad priority sweeps until a structural
candidate improves this budget. Evaluate any tree pre-encoding/preprocessing
including all its setup loads and stores; evaluate cache changes against
both compute and flow costs. Final-round preselection may shorten the tail
but cannot by itself lower the aggregate compute bound to 900.

## Active next experiments — operation reduction (2026-09-10)

Implementation baseline: `d81dfe0`, **1,037 cycles**. The plan below supersedes
the older experiment ordering later in this file. It is a documentation-only
update at that time. Subsequent execution status is recorded at the top of
this file and in the numbered optimization log; A is now implemented.

### Measured compute budget

One vector-equivalent means one vector operation or eight scalar operations.
This is a throughput accounting unit, not a claim that all operations can
move between engines. In particular, MACs require VALU. Flow/load/store are
budgeted separately.

| Purpose | Vector-equivalent work |
| --- | ---: |
| Hash body (512 x 10) and final decode (32) | 5,152 |
| Parity extraction and index updates | 832 |
| Input XOR and gathered-node encoding | 744 |
| Lookup MACs and masks | 488 |
| Gather-address decoding | 232 |
| Setup | 57.125 |
| Total | 7,505.125 |

The ideal 900-cycle compute capacity is 6,750, leaving a necessary reduction
of **755.125** vector-equivalents. Moving instructions between engines does
not reduce this total; count setup, copies, spills and reconstruction too.

### A. Reuse path parity instead of extracting index bits — first priority

Let p0, p1, ... be encoded-hash parity bits within one root-to-leaf traversal.
The mathematical index representation follows S0=-2 and S(d+1)=2*S(d)+pd
(mod 2^32); the implementation currently keeps only parity at the root.
For the shallow levels, bit j of Sd equals p(d-1-j). Thus the predicates
currently obtained with S&2, S&4 and S&8 are already available parity bits.
The existing masks have values 0/2, 0/4 or 0/8; saved 0/1 parity is equivalent
for vselect's zero/nonzero predicate. Preserve the coefficient-table order.

All paths at depths 2, 3 and 4 (4, 8 and 16 paths respectively) were enumerated
and this predicate identity passed. This proves the local identity, not a
scheduled kernel's correctness or speed.

Potential deletions under current cache coverage:

- Depth 2: 64 group-rounds x one mask x eight lanes = 512 scalar operations.
- Depth 3: 64 group-rounds x two masks x eight lanes = 1,024 operations.
- Cached depth 4: 24 group-rounds x three masks x eight lanes = 576 operations.
- Total: **2,112 scalar operations = 264 vector-equivalents**, before any
  additional storage/copy costs. This is not a promised cycle reduction.

Implementation order: depth 2 only, then depth 3, then cached depth 4. Give
parity producers explicit logical versions and retain only required values
until their last consumer. Extend lifetime allocation across these uses;
do not reserve three permanent parity vectors for every group or introduce
copies when renaming can preserve the producer instead. Current scratch has
only 179 words free (1,357/1,536); measure peak live storage for each step.

Gate: verify net operation deletions, correct dependencies and coefficient
selection, scratch <=1,536, and full acceptance before adopting a faster
candidate. A lower work count with worse cycles is diagnostic evidence,
not an accepted performance improvement.

### B. Lookup from path bits; materialize addresses only when needed

For adjacent encoded node values F0 (lower address) and F1 (higher address),
the latest encoded parity p selects the lower address when p=1. Therefore
the selected value is F1+p*(F0-F1). Earlier parity bits select the pair.
This may remove the shallow lookup's need for a complete S value and simplify
coefficient setup. Construct the full address/index at the first consumer
that actually needs it, including the transition from cached to gathered
levels and the second root traversal.

No net savings are claimed yet. Count delayed address reconstruction,
retained parity storage and conversions; do not count A's mask deletions
again. Retain this experiment only if it lowers total work or demonstrates
a separately measured scheduling benefit without correctness regressions.

### C. Bounded search for a shorter hash expression

The body already uses ten vector instructions per group-round. Saving one
more instruction across all 512 instances would remove 512 vector-equivalents.
No such rewrite has been found. Search small expression windows first:
stage 0 plus stage 1; fused stages 2/3 plus stage 4; and the boundary between
the final hash stage and the next round's input XOR.

Use the actual supported ISA and 32-bit modular arithmetic. Require an
algebraic proof or solver-backed equivalence over all 32-bit inputs before
acceptance, then run the full frozen-reference tests. Include constants,
setup and boundary-round exceptions in the savings calculation. Bound each
search and record exhausted windows rather than running open-ended tuning.

A's gross 264 plus a hypothetical 512 from C would exceed the compute deficit
only narrowly. Both net savings are unproven, and the 157-load/four-flow-slot
deficits plus startup, dependencies and drain remain. This does not establish
that 900 is attainable. After a structural gain, remeasure the budgets and
only then retune cache coverage and scheduling. Any tree preprocessing must
include all loads/stores and respect the input/output contract.

### Reporting and acceptance for each experiment

Record parent commit, hypothesis, body/setup operation deltas, net weighted
work, per-engine floors, cycles, scratch, first/last gather and drain. Run
official 9/9 and built-in 3/3 tests, frozen seeds 1000--1031 on the scored
shape, and the six extra shapes x seeds 123/456/789 listed in the log. Keep
tests and simulator unchanged. Commit/push validated speed improvements to
the user's fork; document unsuccessful experiments without retaining broken
or slower candidates in the accepted kernel.

## Historical baselines and earlier roadmap

Current accepted result: iteration 22, **1,040 cycles**, scratch **1,261**.
Persistent I/O addresses remove 32 flow operations; retuned adaptive issue
policies improve overlap. Full acceptance passes. Floors: load 995, VALU
1,019, ALU 931, flow 920. Next experiments must reduce operation counts;
the load floor alone rules out 900 for the present DAG.

Latest: iteration 21 reaches **1,047 cycles**, scratch **1,197**, with adaptive
whole-vector ALU issue and 20 cached round-4 chunks. Full acceptance passes.
Current floors: load 995, VALU 1,018, ALU 935, flow 952. Flow also exceeds
the 900-cycle budget now, so larger uniform cache coverage is not the answer.

Latest: iteration 20 reaches **1,061 cycles**, scratch **1,269**, with permuted
low-bit coefficient lookup and lifetime allocation for hash temporaries.
Acceptance checks pass; details and rejected experiments are in the log.

Latest: iteration 19 reaches **1,066 cycles**, scratch 1,531, by selecting
depth-3 coefficients before one MAC. Full acceptance checks pass. Next is
low-bit coefficient-table permutation to reduce mask-generation work.

2026-09-10 accepted update: iteration 18 implements root-to-depth-2 address
folding and 16 cached round-4 chunks at **1,076 cycles**, scratch **1,531**.
Full official/built-in tests, 32 additional frozen-reference seeds, and all
supported extra shapes pass. See Analysis/Iteration 18 in the log for the
proof and remaining 900-cycle deficits. Optimization work has resumed.

Previous accepted implementation: iteration 17, **1,090 cycles**, with **1,499
scratch words**. Ten chunks use round-4 depth-4 caching; gathered nodes reuse
hash temporaries. Pair-linear shallow lookup, parity reuse, node lifetime
allocation, and readiness reporting are implemented. Work is paused after this
iteration at the user's request. Current floors are VALU 1,062 and load 1,033;
further work must address compute costs and readiness together.
Detailed evidence
is in `OPTIMIZATION_LOG.md`; historical budgets below identify their baselines.

Revised 2026-09-09. Accepted kernel baseline: `d6f0289`, **1,152 cycles**,
scored shape `(height=10, nodes=2047, batch=256, rounds=16)`.
Iteration 1 is now implemented and fully checked at **1,142 cycles**, using
index threshold 23. Current slots: load 2,134, VALU 6,191, ALU 12,417,
flow 736, store 32; scratch 1,530. Historical baseline tables below remain
unchanged for comparison. See iteration 13 in `OPTIMIZATION_LOG.md`.

Documentation commit `684dcbd` did not change the kernel. Approximately 900
is an exploratory target; the current plan does not yet prove it attainable.

## Evidence and corrections

| Engine | Baseline slots | Capacity/cycle | Static floor | Capacity at 900 |
| --- | ---: | ---: | ---: | ---: |
| load | 2,133 | 2 | 1,067 | 1,800 |
| VALU | 6,594 | 6 | 1,099 | 5,400 |
| ALU | 13,281 | 12 | 1,107 | 10,800 |
| flow | 736 | 1 | 736 | 900 |
| store | 32 | 2 | 16 | 1,800 |

Scratch is 1,522 / 1,536 words. The explicit DAG's unit-latency longest path
is 334 operations, excluding resource contention.

The first gather issues at cycle 81 and the last at 1,138. Keeping that first
issue time, 2,048 gathers require a completion boundary of at least
`81 + 2048/2 = 1105`. This is conditional on observed timing, not a universal
lower bound. Startup and post-gather work matter alongside aggregate capacity.

Corrections to the original roadmap:

- Load reduction must begin before targeting sub-1,000 execution: the unchanged
  load count alone requires 1,067 cycles.
- Hash fusion saves 512 body VALU slots, but the diagnostic adds one setup
  load, one broadcast, and eight scratch words.
- Replacing 42 group-round gathers is only a necessary capacity calculation
  under unchanged setup costs; startup, drain, and added computation remain.
- Scratch sharing and scheduling have no guaranteed cycle savings. Measure
  their effects for a concrete candidate.

## Diagnostics already performed

These were in-memory transformations, not changes to the accepted kernel.

| Candidate | Cycles | load | VALU | ALU | flow | Scratch |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Accepted baseline | 1,152 | 2,133 | 6,594 | 13,281 | 736 | 1,522 |
| Hash fusion only | 1,158 | 2,134 | 6,083 | 13,281 | 736 | 1,530 |
| Fusion + index threshold 16 | 1,142 | 2,134 | 6,275 | 11,745 | 736 | 1,530 |

Both diagnostic configurations above passed reference comparisons for seeds
123, 456, and 789 on the scored shape. Full acceptance testing is pending.
Index thresholds 20, 23, 24, and 26 also generated 1,142-cycle schedules but
were not individually executed in those seed checks. Threshold 23 has
6,191 VALU and 12,417 ALU slots. Equal cycles across this sweep suggest another
limiting constraint; they do not prove equivalence after future changes.

The fusion of zero-based hash stages 2 and 3 is:

```text
x2 = 33*x + C2
x3 = (x2 + C3) XOR (x2 << 9)
   = (33*x + C2 + C3) XOR ((33 << 9)*x + (C2 << 9))
```

All arithmetic is modulo 2^32. Two independent MACs and an XOR replace four
body operations. The old shift constant 9 is also another stage's multiplier;
removing that shift does not remove the constant from the whole program.

## Iteration 1: land fusion with resource rebalancing

Status: complete. Threshold 23 balances the compute floors at 1,032 VALU
cycles and 1,035 ALU cycles. The next action is iteration 2's readiness report
and depth-4 cost screening.

Implement the diagnostic cleanly, reproduce 1,142 cycles, and select the
index-engine split using execution time and resource headroom. Complete the
acceptance checks below. Do not accept the fusion-only 1,158-cycle version
as a performance improvement.

Deliverable: a verified improvement and exact setup/body/scratch deltas.
Resolve any discrepancy with the diagnostic before adding another change.

## Iteration 2: measure readiness and screen depth-4 designs early

Add a diagnostic report outside the timed kernel, with per-round engine counts,
first/last issue, ready-but-not-issued work, and dependency waits. Separate
first-gather startup, the gather interval, and post-gather drain. Derive
resource bounds over release/deadline intervals where practical; distinguish
proven bounds from observed timing.

Build complete cost tables for gather, coefficient selection plus MAC,
arithmetic lookup, and mixtures by round/chunk. Count setup, conversions,
masks, live scratch, and dependency depth.

A plain 16-node coefficient lookup selects two coefficients from eight pairs:
14 binary vector selects per lookup. Replacing 42 gathers this way adds
588 flow slots, exceeding the roughly 292 slots available after the estimated
shallow-lookup savings. Reject that plain implementation for the 900 budget;
screen other arithmetic/selection mixes.

Deliverable: a feasible resource tradeoff or a quantified rejection, before
investing in a large scratch allocator.

## Iteration 3: reduce shallow lookup costs

Test depth-2 and depth-3 pair-linear lookup separately:

```text
D = F[k+1] - F[k]
E = F[k] - address(k)*D
F[A] = A*D + E, for A in this pair
```

Initial combined body estimates relative to current shallow lookup:
-1,488 ALU, -128 flow, +122 VALU slots. Coefficient setup is additional and
must be measured. Test depth-1 parity reuse separately: potentially 64 fewer
vector masks, contingent on correct encoded-parity polarity and lifetime.

Rebalance engines after each change. Do not blindly retain earlier index
migration decisions when the available ALU budget changes. Evaluate shallow
lookup with the screened depth-4 design if its main value is freeing flow.

Fusion-only counts plus these body estimates yield about 6,205 VALU,
11,793 ALU, and 608 flow slots before new setup: floors of 1,035, 983, and
608 cycles. Load still requires 1,067. Approximately 805 VALU slots remain
above the 900-cycle capacity before adding depth-4 work. This gap is unresolved.

Deliverable: measured benefit or a demonstrated enabling tradeoff for the
next combined candidate; keep speculative variants separate from the baseline.

## Iteration 4: reduce gathers with targeted scratch reuse

Implement the best screened design on a subset of depth-4 group-rounds, then
vary coverage and engine mix. Depth 4 occurs in rounds 4 and 15; optimize
these independently because their downstream work differs.

Fusion emits 2,134 loads. Capacities at 1,000 and 900 require removing at least
134 and 334 loads: at least 17 and 42 eight-load gathers with unchanged setup.
Replacing all 64 depth-4 gathers removes 512 loads, leaving 1,622 before new
setup. This is a search range, not proof that the compute budget fits.

Allocate scratch for the chosen design's actual live intervals. Start with
dead setup/cache vectors and local temporary reuse. Add explicit WAR/WAW
ordering and measure cross-chunk serialization. Preserve enough concurrently
active chunks to hide latency.

Deliverable: reduced execution time including startup/drain, not just fewer
loads or a smaller scratch footprint.

## Iteration 5: shorten dependencies and close compute deficits

Test preparing the next sibling pair or coefficients while the current hash
runs. The current index determines the candidate pair; only its final choice
needs the new parity. Verify preparation costs, readiness, and live storage.
This is a hypothesis, not an established cheap lookup implementation.

Then evaluate individually:

- per-round raw versus encoded hash state, including every conversion;
- relative/path-bit indices during cached rounds, counting conversion back to
  absolute addresses before gather;
- further hash/constant folding and selected index work on spare engines;
- advancing index arithmetic past its actual last reader instead of waiting
  unnecessarily for the full hash, while preserving overwrite hazards.

Deliverable: measured closure of the remaining compute budgets, or a precise
remaining gap. Fitting loads alone does not establish 900-cycle feasibility.

## Iteration 6: tune scheduling around the new graph

Use critical slack, engine backlog, and readiness to target specific bubbles.
Retune after substantial graph changes with bounded parameter searches.
Speculative child loads are candidates only where load capacity and timing
allow them.

Stop scheduler searches that cease improving the observed bottleneck. There
is no universal 10--20-cycle gap guarantee: aggregate and longest-path lower
bounds can both be loose.

## Record and acceptance protocol

Record the hypothesis, parent commit, exact configuration, setup/body slot
deltas, cycles, scratch, startup/readiness/drain, correctness, and next limiting
factor. Label diagnostics separately from accepted improvements. Preserve
failed findings without accumulating failed code in the accepted kernel.

Before accepting an implementation:

1. Pass built-in tests and all 9 official submission tests.
2. Compare with the frozen reference across at least 32 additional scored-shape
   seeds.
3. Validate previously supported extra shapes; preserve compatibility by
   default and explicitly justify any intended specialization.
4. Confirm `tests/` and `problem.py` are unchanged and `git diff --check` passes.
5. Confirm cycles for the final configuration; broaden or repeat testing only
   when further changes or unresolved concerns justify it.

Document and commit accepted performance improvements to the user's fork
under the established commit/push workflow.

The [Jalapeno discussion](https://zartbot.github.io/blog/arch/jalapeno/en.html)
motivates measuring dependency-driven waiting as well as throughput. For every
experiment, ask both: how many instructions disappeared, and when did the
next gather become ready?
