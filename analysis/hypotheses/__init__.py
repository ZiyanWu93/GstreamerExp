"""Hypothesis verifiers — one script per claim catalogued in HYPOTHESES.md.

Each script reads the relevant experiment record (or set of records),
computes the quantitative prediction stated in the catalog, and prints
one of:

    supported   — prediction holds within the stated thresholds
    refuted     — prediction does not hold; data contradicts it
    inconclusive — data exists but doesn't decide either way (e.g.
                   below required sample size, or scalars too close
                   to call)
    untested    — required experiment record(s) don't exist yet

Each script also prints the supporting numbers, so readers can see
*why* the verdict landed where it did. The verdict itself is
data-derivable; running the script after a fresh experiment regenerates
it without ever editing this directory or HYPOTHESES.md.
"""
