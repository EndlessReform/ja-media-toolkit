# Goodness-of-Fit Value Function

This score measures how well one sequence of time intervals fits another after
the candidate intervals have been shifted into their proposed positions. It does
not find those shifts. It only evaluates a proposed alignment.

## Interval Model

Intervals are half-open: the start is included and the end is excluded. An
interval ending at the same time another interval starts is therefore adjacent,
not overlapping.

Before scoring, each interval sequence should be normalized:

1. Sort intervals by start time.
2. Merge intervals that truly overlap.
3. Do not merge intervals that only touch at an endpoint.
4. Remove zero-length intervals, or assign them to neighboring nonzero intervals
   before scoring.

After normalization, each sequence must be sorted and non-overlapping.

## Applying the Proposed Alignment

The candidate sequence is scored after applying its proposed shifts. A shift may
be the same for every candidate interval, or it may vary across the sequence.

The score assumes these shifted candidate intervals are the intervals being
compared. The scoring function does not choose, optimize, or infer shifts.

## Pair Rating

Given two intervals, let their lengths be positive, and let their overlap length
be:

$$
\max(0, \min(end_1, end_2) - \max(start_1, start_2))
$$

If the overlap length is zero, the pair contributes zero.

Otherwise, the pair rating is:

$$
R = S \cdot \frac{overlap}{\min(length_1, length_2)}
$$

The length score depends on the scoring mode.

### Standard Scoring

Standard scoring rewards overlap and penalizes mismatched interval lengths:

$$
S = \frac{\min(length_1, length_2)}{\max(length_1, length_2)}
$$

So the pair rating simplifies to:

$$
R = \frac{overlap}{\max(length_1, length_2)}
$$

### Overlap Scoring

Overlap scoring ignores length mismatch and scores only the amount of overlap:

$$
S = \min(length_1, length_2) \cdot 10^{-5}
$$

So the pair rating simplifies to:

$$
R = overlap \cdot 10^{-5}
$$

## Which Pairs Are Scored

There is no one-to-one matching step. Once both sequences are sorted,
non-overlapping, and the proposed shifts have been applied, the scored pairs are
determined by a two-sequence sweep:

1. Compare the current interval from each sequence.
2. Add their pair rating.
3. Advance past whichever interval ends first.
4. Repeat until either sequence is exhausted.

This means one interval may contribute score against multiple intervals from the
other sequence if it overlaps them in time.

## Split Penalty

If the proposed shifts vary across the candidate sequence, each change of shift
is counted as a split.

The split penalty per shift change is:

$$
P = \min(count_1, count_2) \cdot \frac{C_{split}}{1000}
$$

where the counts are the numbers of normalized nonzero intervals in the two
sequences.

The default split coefficient is:

$$
C_{split} = 7
$$

Useful values are typically in the range 4 to 20. A coefficient of zero makes
shift changes free. Very large values strongly prefer a single constant shift.

## Total Score

The total score is:

$$
V = \sum R - N_{splits} \cdot P
$$

where the sum is over all interval pairs visited by the sweep, and
\(N_{splits}\) is the number of times the proposed shift changes between
consecutive candidate intervals.
