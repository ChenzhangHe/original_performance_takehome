# Public scoreboards — 2026-09-10 snapshot

Checked at **2026-09-11 03:11 UTC / 2026-09-10 20:11 PDT**. Lower is better.
This is a read-only survey; we did not sign in, upload code, or submit a score.

## Paradigm / former kerneloptimization.fun

The old `kerneloptimization.fun` address redirects to
[Paradigm's Anthropic challenge](https://www.paradigm.xyz/puzzles/anthropic-challenge).
Read the live page and its embedded leaderboard data, not search snippets.
The page describes worst-cycle scoring over nine seeded validation runs.

| Rank | Account | Cycles |
| --- | --- | ---: |
| 1 | @HaydenCc51623 | 869 |
| 2 | @SaifAlHarthi | 872 |
| 3 | @LigengZhu | 875 |
| 4 | @josusanmartin | 876 |
| 5 | @ryan_kirkman | 877 |
| 6 | @506Farley | 883 |
| 7 | @justinwetch | 891 |
| 8 | @yinsong08 | 893 |
| 9 | @redswimmer_ai | 895 |
| 10 | @yfjolne | 900 |
| 11 | @XRorrim | 904 |
| 12 | @zartbotF | 908 |

The leading entry's timestamp is September 10, 2026, 03:06:02 UTC.
31 non-baseline entries have fewer than our local 995 cycles. A numerical
insertion would be around position 32, but **that is not our actual rank**:
we have not passed this site's judge or submitted there. The site-reported
scores were not independently rerun locally, and its full validation
contract was not audited during this survey.

## VLIW Kernel Challenge

The [live board](https://vliw-challenge.fly.dev/) explicitly has two categories.
Its own JavaScript uses the following public read-only endpoints:

- [Without Indices data](https://vliw-challenge.fly.dev/api/scoreboard):
  first `HaydenCC` at **869**, followed by `ligeng_zhu` and `alan_wang` at
  875, then `josusanmartin` at 876. At this snapshot, 90 entries are returned,
  42 below 995. These account entries are not necessarily distinct people.
- [With Indices data](https://vliw-challenge.fly.dev/api/scoreboard?indices=1):
  first `saifalharthi` at **899**, followed by `josusanmartin` at 940 and
  `jamespayor` at 958. This is a separate output contract.

Our current kernel does not output final indices and should be compared to
Without Indices, not to the 899-cycle With Indices leader. Cross-site ranks
and user counts must not be combined.

## Source quality and implications

[Anthropic's README](https://github.com/anthropics/original_performance_takehome/blob/main/Readme.md)
still withholds its best-human score; its 1,363-cycle model benchmark is not
the current community record. GitHub self-reports such as
[1,018](https://github.com/anthropics/original_performance_takehome/issues/43),
[1,063](https://github.com/rubinownz111/1063-cycles-original-performance-takehome),
and [1,076](https://github.com/anthropics/original_performance_takehome/issues/44)
were useful search leads but are not leaderboard leaders. An unrelated
[1,103 self-report](https://github.com/anthropics/original_performance_takehome/issues/30#issuecomment-3803392911)
was challenged for processing only half the batch; that is a reported
reproduction concern, not evidence that all sub-1,000 scores are invalid.

At 995, reaching 900 requires deleting 95 elapsed cycles (9.55%); reaching
869 requires deleting 126 (12.66%). These percentages describe our required
cycle reduction, not speedup percentages. The observed board confirms the
target is worth pursuing, but does not reveal the winning implementation
or prove a particular proposed transformation will work.
