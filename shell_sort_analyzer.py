"""
SHELL SORT COMPLEXITY ANALYZER

Standalone. This program analyses shell sort and nothing else.

WHAT MAKES SHELL SORT DIFFERENT FROM THE ELEMENTARY SORTS
  Its complexity is NOT a property of the algorithm. It is a property of the
  GAP SEQUENCE. Change the gaps and you change the complexity class. So this
  tool has a gap sequence selector, and the expected exponents move with it.

  Three consequences the other sorts never forced:

  1. Reverse-sorted input is NOT shell sort's worst case. Not even close. The
     genuine worst case for the halving sequence has to be CONSTRUCTED, and
     this program constructs it. See the ADVERSARIAL note below.

  2. The adversary only lands when n is a power of two, which is why this tool
     has a `pow2` spacing mode. On other sizes an odd gap sneaks in and
     destroys the construction.

  3. The AVERAGE case complexity of shell sort is an OPEN PROBLEM. Nobody has
     a proof, for any gap sequence. This program says so rather than printing
     a confident number, because printing one would be a lie.

Measurement discipline (why the numbers can be trusted):
  * The timed function carries no counters. Counting is a separate pass.
  * Each measurement calibrates an inner loop until a batch exceeds 10 ms,
    disables the GC, and takes the MINIMUM of N repeats on a FIXED array.
  * Memory is measured with tracemalloc in an ISOLATED CHILD PROCESS.
  * Plots do not redraw mid-run: a redraw holds the GIL and inflates samples.
  * The verdict is a least-squares fit of log T = k*log n + c.

Usage:  python shell_sort_analyzer.py
Verify: python shell_sort_verify.py
"""

import csv
import gc
import math
import multiprocessing as mp
import queue
import random
import sys
import threading
import time
import tracemalloc
from datetime import datetime

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


# ----------------------------------------------------------------------------
# Theme
# ----------------------------------------------------------------------------

BG      = "#080c11"
PANEL   = "#111820"
PANEL_2 = "#0d131a"
GRID    = "#1e2a36"
EDGE    = "#2b3947"
FG      = "#c7d5e0"
MUTED   = "#6f8496"
ACCENT  = "#00e5a0"
WARN    = "#ffb020"
DANGER  = "#ff5470"
VIOLET  = "#9c6bff"

CASES = ("best", "average", "worst")
CASE_COLOR = {"best": "#00e5a0", "average": "#34c3ff", "worst": "#ff5470"}

MONO   = ("Consolas", 9)
MONO_B = ("Consolas", 10, "bold")


# ----------------------------------------------------------------------------
# The algorithm
# ----------------------------------------------------------------------------

ALGO_NAME = "shell sort"
MOVE_NAME = "shifts"     # it shifts, like a gapped insertion sort. Not swaps.
MOVE_ABBR = "shf"
STABLE    = False        # long-range moves jump equal elements past each other
ADAPTIVE  = True         # sorted input is cheap, but O(n log n), never O(n)


# -- gap sequences -----------------------------------------------------------
# Every sequence must be strictly decreasing and END AT 1. If it does not end
# at 1, the array is never fully 1-sorted and the algorithm does not sort.

def gaps_shell(n):
    """Shell's original, 1959: n/2, n/4, ... 1.  Worst case Theta(n^2)."""
    g, out = n // 2, []
    while g >= 1:
        out.append(g)
        g //= 2
    return out


def gaps_knuth(n):
    """Knuth: 1, 4, 13, 40, 121 ... h = 3h+1.  Worst case Theta(n^1.5)."""
    seq, h = [], 1
    while h < n:
        seq.append(h)
        h = 3 * h + 1
    return seq[::-1]


def gaps_hibbard(n):
    """Hibbard: 2^k - 1 = 1, 3, 7, 15, 31 ...  Worst case Theta(n^1.5)."""
    seq, k = [], 1
    while (2 ** k - 1) < n:
        seq.append(2 ** k - 1)
        k += 1
    return seq[::-1]


def gaps_ciura(n):
    """
    Ciura, 2001: 1, 4, 10, 23, 57, 132, 301, 701, extended by *2.25.
    Found by EXPERIMENT, not derived. It has the best known empirical
    performance and NO proven bound of any kind. That is not a gap in this
    program's documentation; it is a gap in the literature.
    """
    seq = [1, 4, 10, 23, 57, 132, 301, 701]
    while seq[-1] < n:
        seq.append(int(seq[-1] * 2.25))
    out = [g for g in reversed(seq) if g < n]
    return out or ([1] if n > 1 else [])


GAP_SEQUENCES = {
    "shell (n/2)":     gaps_shell,
    "knuth (3h+1)":    gaps_knuth,
    "hibbard (2^k-1)": gaps_hibbard,
    "ciura":           gaps_ciura,
}

GAP_KEY = {
    "shell (n/2)": "shell", "knuth (3h+1)": "knuth",
    "hibbard (2^k-1)": "hibbard", "ciura": "ciura",
}

# The gap function the timed sort will use. Set before every run.
GAP_FN = gaps_shell
GAP_NAME = "shell (n/2)"


def set_gap_sequence(name):
    global GAP_FN, GAP_NAME
    GAP_FN = GAP_SEQUENCES[name]
    GAP_NAME = name


# -- expected exponents, PER GAP SEQUENCE ------------------------------------
# None means: no proven bound exists. The tool reports the measurement and
# refuses to grade it, because there is nothing honest to grade it against.
#
# best:    Theta(n log n) for every sequence. Each pass costs one comparison
#          per element and there are O(log n) passes. A power-law fit of
#          n log n over n = 16..1024 lands near 1.25, which is why that is the
#          number here rather than 1.0.
# average: OPEN PROBLEM. No proof exists for any gap sequence.
# worst:   only meaningful when the input is adversarial FOR THAT SEQUENCE.
#          The construction below defeats the halving sequence and nothing
#          else, so only "shell" is graded on it.

EXPECTED = {
    "shell":   {"best": 1.25, "average": None, "worst": 2.00},
    "knuth":   {"best": 1.25, "average": None, "worst": None},
    "hibbard": {"best": 1.25, "average": None, "worst": None},
    "ciura":   {"best": 1.25, "average": None, "worst": None},
}

WORST_NOTE = {
    "shell":   "",
    "knuth":   "this input is not adversarial for Knuth gaps; the proven\n"
               "  worst case is n^1.5 but no input here reaches it",
    "hibbard": "this input is not adversarial for Hibbard gaps; the proven\n"
               "  worst case is n^1.5 but no input here reaches it",
    "ciura":   "no proven bound of any kind exists for Ciura's sequence",
}

CASE_LABEL = {
    "best":    "Sorted  O(n log n)",
    "average": "Random  (unproven)",
    "worst":   "Adversarial  (see help)",
}


def h_sort_pass(a, gap):
    """
    One gapped insertion pass. After this returns, `a` is h-sorted for h=gap,
    meaning a[i] <= a[i+gap] for every valid i.

    `sort` calls this rather than inlining it, so there is exactly ONE copy of
    the logic and the verifier can test the h-sortedness invariant against the
    same code that actually runs.
    """
    n = len(a)
    for i in range(gap, n):
        tmp = a[i]
        j = i
        while j >= gap and a[j - gap] > tmp:
            a[j] = a[j - gap]
            j -= gap
        a[j] = tmp


def sort(a):
    """Pure, uninstrumented shell sort. This is the function that gets timed."""
    for gap in GAP_FN(len(a)):
        h_sort_pass(a, gap)


def sort_counted(a):
    """
    Instrumented variant. Never timed, only counted.

    The short-circuit matters: `while j >= gap and a[j-gap] > tmp` does NOT
    evaluate the comparison when j < gap, so it must not be counted there.
    """
    n = len(a)
    comparisons = moves = 0
    for gap in GAP_FN(n):
        for i in range(gap, n):
            tmp = a[i]
            j = i
            while j >= gap:
                comparisons += 1
                if a[j - gap] > tmp:
                    a[j] = a[j - gap]
                    moves += 1
                    j -= gap
                else:
                    break
            a[j] = tmp
    return comparisons, moves


def is_h_sorted(a, h):
    """The invariant shell sort maintains. Exposed for the verifier."""
    return all(a[i] <= a[i + h] for i in range(len(a) - h))


# ----------------------------------------------------------------------------
# Input generation
# ----------------------------------------------------------------------------

CASE_INDEX = {"best": 0, "average": 1, "worst": 2}


def adversarial(n):
    """
    The genuine worst case for the HALVING gap sequence.

    Small values go to EVEN indices, large values to ODD indices:
        [1, 5, 2, 6, 3, 7, 4, 8]

    Now look at the halving gaps for n = 8: 4, 2, 1. Every gap before the last
    is EVEN. An even gap only ever compares an even index with an even index,
    or odd with odd. It NEVER compares an even index with an odd one. So the
    small values are already sorted among themselves, the large values are
    already sorted among themselves, and every pass before the final one moves
    NOTHING. The array looks beautifully h-sorted at every stage while being a
    disaster overall.

    Then gap = 1 arrives and faces roughly n^2/8 inversions with a plain
    insertion pass. All the work lands at the end. Theta(n^2).

    THE CATCH, and it is a good one: the halving gaps are only all-even when n
    is a POWER OF TWO. At n = 1000 the sequence is 500, 250, 125, ... and that
    odd 125 immediately mixes the even and odd index classes, destroying the
    adversary. Measured: n=1000 costs 10,444 comparisons, n=1024 costs 140,033.
    A 2 percent bigger input does 13x more work.

    That is why this tool has a `pow2` spacing mode. Use it, or the worst case
    you are looking at is not the worst case.
    """
    half = (n + 1) // 2
    small = list(range(1, half + 1))
    large = list(range(half + 1, n + 1))
    out = [0] * n
    out[0::2] = small[:len(out[0::2])]
    out[1::2] = large[:len(out[1::2])]
    return out


def make_array(size, case, seed):
    """Deterministic for a given (size, case, seed). The timing, counting and
    memory passes all see the IDENTICAL array, and the seed is an int rather
    than a hashed string so the spawned child reproduces it exactly."""
    if case == "best":
        return list(range(size))
    if case == "worst":
        return adversarial(size)
    rng = random.Random(seed * 1_000_003 + size * 31 + CASE_INDEX[case])
    return [rng.randint(1, 10_000) for _ in range(size)]


# ----------------------------------------------------------------------------
# Measurement
# ----------------------------------------------------------------------------

MIN_BATCH_SECONDS = 0.010
MAX_LOOPS = 4096
ELEMENT_BUDGET = 4_000_000


def _time_batch(arr, loops):
    copies = [arr.copy() for _ in range(loops)]
    gc_was_on = gc.isenabled()
    gc.disable()
    try:
        t0 = time.perf_counter()
        for c in copies:
            sort(c)
        t1 = time.perf_counter()
    finally:
        if gc_was_on:
            gc.enable()
    return (t1 - t0) / loops


def measure_time(arr, repeats):
    """Calibrate past clock granularity, then take the MINIMUM across repeats
    of that SAME array. Holding the workload constant is what makes the minimum
    a valid noise estimator."""
    cap = max(1, min(MAX_LOOPS, ELEMENT_BUDGET // max(1, len(arr))))
    loops = 1
    while loops < cap:
        per_call = _time_batch(arr, loops)
        if per_call * loops >= MIN_BATCH_SECONDS:
            break
        loops = min(cap, loops * 4)
    samples = [_time_batch(arr, loops) for _ in range(repeats)]
    return min(samples), loops


def _memory_worker(gap_name, sizes, cases, seed, out_q):
    """Runs in a CHILD PROCESS. tracemalloc traces the whole process, so in the
    GUI process every matplotlib redraw would be charged to the sort."""
    set_gap_sequence(gap_name)
    results = {}
    for n in sizes:
        for case in cases:
            arr = make_array(n, case, seed)
            gc.collect()
            tracemalloc.start()
            try:
                base, _ = tracemalloc.get_traced_memory()
                sort(arr)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            results[(n, case)] = max(0, peak - base)
    out_q.put(results)


def measure_memory_isolated(gap_name, sizes, cases, seed=0xC0FFEE, timeout=180,
                            should_stop=None):
    """Returns (results, mode): 'isolated', 'in-process', or 'stopped'.
    Polled rather than blocking, so a child that dies on startup fails fast
    instead of hanging for the full timeout."""
    proc = None
    try:
        ctx = mp.get_context("spawn")
        q = ctx.Queue()
        proc = ctx.Process(target=_memory_worker,
                           args=(gap_name, sizes, cases, seed, q), daemon=True)
        proc.start()

        deadline = time.monotonic() + timeout
        results = None
        while time.monotonic() < deadline:
            if should_stop is not None and should_stop():
                proc.terminate()
                proc.join(timeout=2)
                return {}, "stopped"
            try:
                results = q.get(timeout=0.2)
                break
            except queue.Empty:
                if not proc.is_alive():
                    try:
                        results = q.get(timeout=0.5)
                    except queue.Empty:
                        results = None
                    break

        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
        if results:
            return results, "isolated"
    except Exception:
        if proc is not None and proc.is_alive():
            proc.terminate()

    out_q = queue.Queue()
    _memory_worker(gap_name, sizes, cases, seed, out_q)
    return out_q.get(), "in-process"


def build_sizes(lo, hi, step, spacing, points=14):
    """
    pow2 exists because of the adversarial case. The halving gap sequence is
    only all-even when n is a power of two, and the adversary only lands when
    the gaps are all even. On any other size an odd gap appears and the worst
    case quietly stops being the worst case.
    """
    if hi <= lo:
        return [lo]
    if spacing == "pow2":
        k0 = max(1, math.ceil(math.log2(lo)))
        out = []
        k = k0
        while 2 ** k <= hi:
            out.append(2 ** k)
            k += 1
        return out or [lo]
    if spacing == "log":
        xs = np.geomspace(lo, hi, num=points)
        return [x for x in sorted({int(round(v)) for v in xs}) if x >= 2]
    return list(range(lo, hi + 1, step))


def input_footprint(arr):
    return sys.getsizeof(arr)


def fit_exponent(sizes, times):
    """Least squares on log T = k*log n + c. k is the empirical exponent."""
    pts = [(n, t) for n, t in zip(sizes, times) if n > 1 and t > 0]
    if len(pts) < 3:
        return None
    xs = np.log(np.array([p[0] for p in pts], dtype=float))
    ys = np.log(np.array([p[1] for p in pts], dtype=float))
    k, c = np.polyfit(xs, ys, 1)
    return float(k), float(c)


# Reference curves for panel 02. n^2/8 is the adversarial prediction; n log2 n
# is the sorted-input prediction. Both are what the theory actually claims.
REFERENCES = [
    ("n^2/8  adversarial (pow2)", lambda n: n * n / 8, WARN, ":"),
    ("n log2 n  sorted", lambda n: n * math.log2(n) if n > 1 else 1, VIOLET, "-."),
]


# ----------------------------------------------------------------------------
# Help content
# ----------------------------------------------------------------------------

SUMMARY = (
    "SHELL SORT  ::  ANALYTICAL\n"
    "----------------------------------------\n"
    "Complexity is a property of the GAP\n"
    "SEQUENCE, not of the algorithm.\n"
    "\n"
    "WORST CASE, by sequence\n"
    "  shell    n/2       Theta(n^2)\n"
    "  hibbard  2^k-1     Theta(n^1.5)\n"
    "  knuth    3h+1      Theta(n^1.5)\n"
    "  ciura              NO PROOF\n"
    "\n"
    "BEST CASE (sorted input)\n"
    "  Theta(n log n), all sequences.\n"
    "  One cmp per element per pass,\n"
    "  O(log n) passes. Never O(n).\n"
    "\n"
    "AVERAGE CASE\n"
    "  OPEN PROBLEM. No proof exists\n"
    "  for ANY gap sequence.\n"
    "\n"
    "SPACE\n"
    "  auxiliary  O(1)   in-place\n"
    "\n"
    "adaptive: yes    stable: NO\n"
    "\n"
    "Use pow2 spacing for the worst case\n"
    "or the adversary will not land.\n"
)

HELP_ALGORITHM = """
SHELL SORT
========================================================================

DEFINITION
  A generalisation of gapped insertion. Instead of comparing neighbours, it
  compares elements a distance `gap` apart, and it repeats this for a
  decreasing sequence of gaps ending at 1.

  Sorting with a large gap moves elements a long way in a single step, which
  removes many inversions cheaply. By the time the gap reaches 1, the array is
  nearly sorted, and the final pass, which IS a plain insertion pass, has very
  little left to do.

  In-place (O(1) auxiliary space) and NOT stable: a long-range move can jump an
  element past an equal one.

HOW IT WORKS
  for gap in gap_sequence(n):        <- must end at 1, or it does not sort
      for i in gap .. n-1:
          tmp = a[i]
          j = i
          while j >= gap and a[j-gap] > tmp:
              a[j] = a[j-gap]        <- SHIFT by gap. One write.
              j -= gap
          a[j] = tmp

  The invariant: after the pass with gap h, the array is h-SORTED, meaning
  a[i] <= a[i+h] for every i. The theorem that makes the whole thing work is
  that an h-sorted array REMAINS h-sorted after you k-sort it for any k. Later
  passes never undo the work of earlier ones. The verifier tests this directly.

THE GAP SEQUENCE IS THE ALGORITHM
  This is the part that makes shell sort different from every elementary sort.
  Its complexity is not a property of the algorithm. It is a property of the
  gaps. Change the sequence, change the complexity class.

  ------------------------------------------------------------------
  sequence            gaps                    worst case
  ------------------------------------------------------------------
  Shell 1959          n/2, n/4, ... 1         Theta(n^2)
  Hibbard             2^k - 1                 Theta(n^1.5)
  Knuth               (3^k - 1)/2             Theta(n^1.5)
  Sedgewick           various                 O(n^(4/3))
  Ciura 2001          1,4,10,23,57,132,301    NO PROVEN BOUND
  ------------------------------------------------------------------

  Ciura's sequence was found by EXPERIMENT, not derived. It has the best known
  empirical performance and no proof of anything. That is not a gap in this
  program's documentation. It is a gap in the literature.

  Try each one in the dropdown and watch the fitted exponent move.

THE ADVERSARIAL WORST CASE
  Reverse-sorted input is NOT shell sort's worst case. Not even close. The
  first large-gap pass tears a reversed array apart almost immediately.

  The genuine worst case for the halving sequence has to be CONSTRUCTED, and
  this program constructs it. Put the small values at EVEN indices and the
  large values at ODD indices:

      [1, 5, 2, 6, 3, 7, 4, 8]

  Now look at the halving gaps for n=8: 4, 2, 1. Every gap before the last is
  EVEN. An even gap only ever compares even-index with even-index, or odd with
  odd. It NEVER compares an even index with an odd one. So the small values are
  already sorted among themselves, the large values are already sorted among
  themselves, and every pass before the final one moves NOTHING.

  The array looks beautifully h-sorted at every stage while being a disaster
  overall. Then gap=1 arrives and faces roughly n^2/8 inversions with a plain
  insertion pass. All of the work lands at the end. Theta(n^2).

  THE CATCH, and it is a good one:
  The halving gaps are only all-even when n is a POWER OF TWO. At n = 1000 the
  sequence is 500, 250, 125, ... and that odd 125 immediately mixes the even
  and odd index classes, destroying the adversary.

      n = 1000   ->    10,444 comparisons
      n = 1024   ->   140,033 comparisons

  A two percent bigger input does THIRTEEN TIMES more work. That is why this
  tool has a `pow2` spacing mode. Use it for the worst case, or the worst case
  you are looking at is not the worst case.

  Now switch the gap sequence to Knuth and run the adversarial case again. The
  comparison count collapses from 140,033 to about 9,400, because Knuth's gaps
  include odd values that mix the two index classes immediately. The adversary
  is aimed at ONE sequence and misses every other. That is the demonstration.

THE BEST CASE IS NOT O(n)
  Sorted input is cheap but it is not linear. Each gapped pass still walks
  every element once and performs one comparison to discover that it is already
  in place. There are O(log n) passes. So the best case is Theta(n log n),
  not Theta(n).

  Set `normalize by` to `n log n` and watch the best-case line go flat. That is
  the confirmation, and it is the one place in this tool where the n log n
  normaliser earns its keep.

THE AVERAGE CASE IS AN OPEN PROBLEM
  There is no proof of the average-case complexity of shell sort. Not for
  Shell's sequence, not for Knuth's, not for Ciura's, not for any of them. It
  is one of the better-known open problems in the analysis of algorithms.

  Empirically it lands around n^1.3, and this tool will measure something in
  that neighbourhood. But the RESULTS panel will refuse to grade it against a
  prediction, and print `no proven bound` instead, because there is nothing
  honest to grade it against. A tool that printed a confident checkmark there
  would be lying to you.

WHAT TO EXPECT FROM THIS TOOL
  1. Default run (shell gaps, pow2 spacing). Best fits near n^1.25, matching
     n log n. Adversarial fits near n^1.8, climbing toward n^2 as you raise
     max n. Average is reported but not graded.
  2. Switch to Knuth. The adversarial curve COLLAPSES. Same input, same
     algorithm, different gaps, completely different behaviour.
  3. Switch spacing from pow2 to log. The adversarial curve collapses again,
     for a completely different reason: the adversary needs even gaps.
"""

HELP_PARAMETERS = """
PARAMETERS
========================================================================

gap sequence       shell | knuth | hibbard | ciura     (default: shell)
  THE most important control in this program. Shell sort's complexity is a
  property of the gap sequence, not of the algorithm. Changing this changes the
  complexity class, and the expected exponents in the verdict move with it.

    shell (n/2)       n/2, n/4, ... 1        worst Theta(n^2)
    knuth (3h+1)      1, 4, 13, 40, 121      worst Theta(n^1.5)
    hibbard (2^k-1)   1, 3, 7, 15, 31        worst Theta(n^1.5)
    ciura             1, 4, 10, 23, 57 ...   NO PROVEN BOUND, best in practice

  How to adjust: run shell first, then knuth, and compare the adversarial
  curve. That comparison is the single most instructive thing here.

spacing            pow2 | log | linear       (default: pow2)
  pow2    Powers of two: 16, 32, 64, ... USE THIS FOR THE WORST CASE. The
          adversarial input only defeats the halving sequence when every gap
          before the last is even, and the halving gaps are only all-even when
          n is a power of two. On any other size an odd gap appears and the
          adversary silently stops working. This is not a detail. At n=1000 the
          adversarial input costs 10,444 comparisons; at n=1024 it costs
          140,033.

  log     Geometric, 14 points. Good for the best and average cases, and good
          for exponent fitting generally, but it will UNDERSTATE the worst case
          because most of its sizes are not powers of two.

  linear  Dense and even. Worst choice for a log-log fit. Use it only when you
          want a smooth curve for a slide.

min n / max n
  Size range. Under pow2 spacing these are rounded to the enclosing powers of
  two, so 10 to 1000 becomes 16 to 512.

  max n is the biggest lever on runtime. Raise it to tighten the adversarial
  fit: the Theta(n^2) bound is approached FROM BELOW, so a fit over 16..1024
  gives about n^1.80 while 128..8192 gives about n^1.93. Neither is wrong. The
  asymptotic bound is simply not yet fully expressed at small n.

step
  Gap between sizes. ONLY APPLIES TO LINEAR SPACING. Ignored under pow2 and log.

repeats
  How many times each measurement is taken. The tool keeps the MINIMUM, not the
  mean, because measurement noise is strictly additive: a scheduler preemption
  can only make a run slower, never faster. The fastest observed run is
  therefore closest to the true cost.

  Every repeat sorts a copy of the SAME array. The minimum is only a valid
  noise estimator when the workload is held constant.

    5   fine for a quick look (default)
    9   steadier, roughly doubles runtime

cases              best | average | worst
    best      already sorted        Theta(n log n). Cheap, but NOT linear.
    average   random, seeded        Empirically ~n^1.3. NO PROOF EXISTS.
    worst     ADVERSARIAL           Constructed to defeat the halving gaps.
                                    Not reverse-sorted. Reverse-sorted is easy
                                    for shell sort. See the ALGORITHM tab.

normalize by       n | n log n | n^2       (default: n log n)
  Controls panel 04. It plots T(n) divided by the function you pick, and if
  that function is the true growth rate, THE LINE GOES FLAT.

  This is the one algorithm where `n log n` is the interesting setting. Select
  it and the best-case line flattens, confirming Theta(n log n) rather than the
  Theta(n) that people often assume by analogy with insertion sort.

  Select n^2 and the adversarial line flattens instead, confirming the
  quadratic worst case for the halving sequence.

live plot                                  (default: OFF)
  Leave it off. A matplotlib redraw holds Python's GIL for tens of
  milliseconds, and if that lands inside a timing window the sample is
  inflated. With it off the plots update once, at the end, and the measurement
  is clean. Turn it on only for a demo, and accept that the numbers become
  decorative.

========================================================================
SUGGESTED PRESETS

  The headline demonstration
    gap=shell   spacing=pow2   min 16   max 1024   all cases
    Then change ONLY the gap sequence to knuth and run again. The adversarial
    curve collapses by a factor of about 15. Same input, same algorithm.

  The best case is not linear
    gap=knuth   normalize by = n log n   best case only
    The line goes flat. It would not if the best case were O(n).

  The adversary needs powers of two
    gap=shell   worst case only, spacing=pow2, then run again with spacing=log
    The pow2 curve is roughly n^1.8. The log curve is roughly n^1.5. Same
    algorithm, same input construction, different sizes.

  Publication quality
    spacing=pow2   min 32   max 4096   repeats 9
    The adversarial fit tightens toward n^2 as the range grows.
"""


# ----------------------------------------------------------------------------
# Application
# ----------------------------------------------------------------------------

class Analyzer:

    def __init__(self, root):
        self.root = root
        self.root.title("SHELL SORT  //  COMPLEXITY ANALYZER")
        self.root.geometry("1600x900")
        self.root.configure(bg=BG)

        self.data = {c: self._empty() for c in CASES}
        self.msgq = queue.Queue()
        self.stop_evt = threading.Event()
        self.worker = None
        self.run_id = 0
        self.data_rid = -1
        self.pending_redraw = False
        self._info_win = None
        self.run_gap = GAP_NAME

        self._build_ui()
        self._poll_queue()
        self._banner()

    @staticmethod
    def _empty():
        return {"sizes": [], "time": [], "comparisons": [], "moves": [],
                "aux_bytes": [], "input_bytes": []}

    # -- UI -------------------------------------------------------------------

    def _build_ui(self):
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        left = tk.Frame(outer, bg=PANEL, width=440)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left.pack_propagate(False)

        tk.Label(left, text="SHELL SORT", font=("Consolas", 15, "bold"),
                 bg=PANEL, fg=ACCENT).pack(pady=(16, 2))
        tk.Label(left, text="complexity analyzer  ::  empirical + analytical",
                 font=MONO, bg=PANEL, fg=MUTED).pack(pady=(0, 4))
        tk.Label(left, text="adaptive: yes    stable: no", font=MONO,
                 bg=PANEL, fg=MUTED).pack(pady=(0, 12))

        params = tk.LabelFrame(left, text=" PARAMETERS ", bg=PANEL, fg=FG,
                               font=MONO_B, bd=1, relief=tk.SOLID)
        params.pack(fill=tk.X, padx=12, pady=4)

        # THE control that matters
        grow = tk.Frame(params, bg=PANEL)
        grow.pack(fill=tk.X, padx=8, pady=(6, 6))
        tk.Label(grow, text="gap sequence", bg=PANEL, fg=ACCENT,
                 font=MONO_B).pack(side=tk.LEFT)
        self.gap = ttk.Combobox(grow, values=list(GAP_SEQUENCES),
                                state="readonly", width=16, font=MONO)
        self.gap.set(GAP_NAME)
        self.gap.pack(side=tk.RIGHT)
        self.gap.bind("<<ComboboxSelected>>", self._on_gap_change)

        self.min_size = tk.IntVar(value=16)
        self.max_size = tk.IntVar(value=1024)
        self.step_size = tk.IntVar(value=10)
        self.repeats = tk.IntVar(value=5)

        self._spin(params, "min n", self.min_size, 2, 16384)
        self._spin(params, "max n", self.max_size, 2, 16384)
        self._spin(params, "step", self.step_size, 1, 500)
        self._spin(params, "repeats", self.repeats, 1, 25)

        crow = tk.Frame(params, bg=PANEL)
        crow.pack(fill=tk.X, padx=8, pady=(4, 4))
        tk.Label(crow, text="cases", bg=PANEL, fg=FG, font=MONO).pack(side=tk.LEFT)
        self.case_vars = {}
        for c in CASES:
            v = tk.BooleanVar(value=True)
            self.case_vars[c] = v
            tk.Checkbutton(crow, text=c, variable=v, bg=PANEL, fg=CASE_COLOR[c],
                           selectcolor=PANEL_2, activebackground=PANEL,
                           activeforeground=CASE_COLOR[c], font=MONO, bd=0,
                           highlightthickness=0).pack(side=tk.LEFT, padx=4)

        srow = tk.Frame(params, bg=PANEL)
        srow.pack(fill=tk.X, padx=8, pady=(0, 4))
        tk.Label(srow, text="spacing", bg=PANEL, fg=FG, font=MONO).pack(side=tk.LEFT)
        self.spacing = ttk.Combobox(srow, values=["pow2", "log", "linear"],
                                    state="readonly", width=12, font=MONO)
        self.spacing.set("pow2")
        self.spacing.pack(side=tk.RIGHT)

        nrow = tk.Frame(params, bg=PANEL)
        nrow.pack(fill=tk.X, padx=8, pady=(0, 4))
        tk.Label(nrow, text="normalize by", bg=PANEL, fg=FG,
                 font=MONO).pack(side=tk.LEFT)
        self.norm = ttk.Combobox(nrow, values=["n", "n log n", "n^2"],
                                 state="readonly", width=12, font=MONO)
        self.norm.set("n log n")
        self.norm.pack(side=tk.RIGHT)
        self.norm.bind("<<ComboboxSelected>>", lambda _e: self.redraw())

        lrow = tk.Frame(params, bg=PANEL)
        lrow.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.live_plot = tk.BooleanVar(value=False)
        tk.Checkbutton(lrow, text="live plot (adds timing noise)",
                       variable=self.live_plot, bg=PANEL, fg=WARN,
                       selectcolor=PANEL_2, activebackground=PANEL,
                       activeforeground=WARN, font=MONO, bd=0,
                       highlightthickness=0).pack(side=tk.LEFT)

        btns = tk.Frame(left, bg=PANEL)
        btns.pack(fill=tk.X, padx=12, pady=12)
        self.run_btn = tk.Button(btns, text="RUN", command=self.run_analysis,
                                 bg=ACCENT, fg=BG, font=MONO_B, bd=0,
                                 activebackground="#00b880", height=2, width=10)
        self.run_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.stop_btn = tk.Button(btns, text="STOP", command=self.stop_analysis,
                                  bg=WARN, fg=BG, font=MONO_B, bd=0,
                                  state="disabled", height=2, width=10)
        self.stop_btn.pack(side=tk.LEFT, padx=6)
        self.clear_btn = tk.Button(btns, text="CLEAR", command=self.clear_data,
                                   bg=DANGER, fg=BG, font=MONO_B, bd=0,
                                   height=2, width=10)
        self.clear_btn.pack(side=tk.LEFT, padx=6)

        io_row = tk.Frame(left, bg=PANEL)
        io_row.pack(fill=tk.X, padx=12)
        tk.Button(io_row, text="? ALGORITHM + PARAMETERS", command=self.show_info,
                  bg=PANEL_2, fg=ACCENT, font=MONO, bd=1, relief=tk.SOLID,
                  activebackground=EDGE, activeforeground=ACCENT
                  ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        tk.Button(io_row, text="EXPORT CSV", command=self.export_csv,
                  bg=PANEL_2, fg=FG, font=MONO, bd=1, relief=tk.SOLID,
                  activebackground=EDGE, activeforeground=FG
                  ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        self.status = tk.Label(left, text="READY", bg=PANEL, fg=ACCENT, font=MONO)
        self.status.pack(pady=(10, 2))
        self.progress = ttk.Progressbar(left, length=400, mode="determinate")
        self.progress.pack(pady=(0, 10), padx=12)

        sf = tk.LabelFrame(left, text=" RESULTS ", bg=PANEL, fg=FG, font=MONO_B,
                           bd=1, relief=tk.SOLID)
        sf.pack(fill=tk.X, padx=12, pady=4)
        self.summary = scrolledtext.ScrolledText(sf, height=14, bg=PANEL_2,
                                                 fg=FG, font=MONO, bd=0)
        self.summary.pack(fill=tk.X, padx=6, pady=6)

        cf = tk.LabelFrame(left, text=" CONSOLE ", bg=PANEL, fg=FG, font=MONO_B,
                           bd=1, relief=tk.SOLID)
        cf.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 12))
        self.console = scrolledtext.ScrolledText(cf, height=8, bg=PANEL_2,
                                                 fg=MUTED, font=MONO, bd=0)
        self.console.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        right = tk.Frame(outer, bg=BG)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.fig = Figure(figsize=(12, 8), facecolor=BG)
        self.fig.subplots_adjust(hspace=0.35, wspace=0.28, left=0.08,
                                 right=0.97, top=0.92, bottom=0.08)
        self.ax1 = self.fig.add_subplot(2, 2, 1)
        self.ax2 = self.fig.add_subplot(2, 2, 2)
        self.ax3 = self.fig.add_subplot(2, 2, 3)
        self.ax4 = self.fig.add_subplot(2, 2, 4)
        self.axes = (self.ax1, self.ax2, self.ax3, self.ax4)

        self.canvas = FigureCanvasTkAgg(self.fig, right)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self._static_summary()
        self.redraw()

    def _spin(self, parent, label, var, lo, hi):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill=tk.X, padx=8, pady=3)
        tk.Label(row, text=label, bg=PANEL, fg=FG, font=MONO).pack(side=tk.LEFT)
        tk.Spinbox(row, from_=lo, to=hi, textvariable=var, width=12,
                   bg=PANEL_2, fg=FG, font=MONO, buttonbackground=EDGE,
                   insertbackground=ACCENT, relief=tk.FLAT).pack(side=tk.RIGHT)

    def _on_gap_change(self, _event=None):
        """Changing the gap sequence changes the complexity class, so the data
        is no longer comparable. Clear it."""
        if self.worker and self.worker.is_alive():
            self.gap.set(self.run_gap)
            self.log("cannot change gap sequence while a run is in progress")
            return
        self.data_rid = -1
        self.data = {c: self._empty() for c in CASES}
        self.progress["value"] = 0
        self._static_summary()
        key = GAP_KEY[self.gap.get()]
        w = EXPECTED[key]["worst"]
        self.log(f"gap sequence: {self.gap.get()}   worst case: "
                 f"{'Theta(n^' + str(w) + ')' if w else 'no proven bound'}")
        self.redraw()

    # -- logging (main thread only) --------------------------------------------

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.insert(tk.END, f"[{ts}] {msg}\n")
        self.console.see(tk.END)

    def _banner(self):
        for line in ("=" * 52,
                     "SHELL SORT ANALYZER  //  ONLINE",
                     "=" * 52,
                     "the gap sequence IS the algorithm. change it and the",
                     "complexity class changes with it.",
                     "",
                     "worst case input is ADVERSARIAL, not reverse-sorted.",
                     "it needs pow2 spacing to land. see the help.",
                     ""):
            self.log(line)

    def _static_summary(self):
        self.summary.delete("1.0", tk.END)
        self.summary.insert("1.0", SUMMARY)

    # -- run / stop / clear -----------------------------------------------------

    def _validate(self):
        try:
            lo, hi = self.min_size.get(), self.max_size.get()
            step, reps = self.step_size.get(), self.repeats.get()
        except tk.TclError:
            messagebox.showerror("Invalid input", "Parameters must be integers.")
            return None
        if lo < 2:
            messagebox.showerror("Invalid input", "min n must be at least 2.")
            return None
        if hi < lo:
            messagebox.showerror("Invalid input", "max n must be >= min n.")
            return None
        if step < 1:
            messagebox.showerror("Invalid input", "step must be at least 1.")
            return None
        cases = [c for c in CASES if self.case_vars[c].get()]
        if not cases:
            messagebox.showerror("Invalid input", "Select at least one case.")
            return None
        sizes = build_sizes(lo, hi, step, self.spacing.get())
        if len(sizes) < 3:
            messagebox.showerror(
                "Too few sizes",
                f"That range gives only {len(sizes)} size(s): {sizes}.\n\n"
                "An exponent fit needs at least 3 points. Widen the range, or "
                "switch spacing away from pow2.")
            return None
        return sizes, cases, reps

    def run_analysis(self):
        if self.worker and self.worker.is_alive():
            return
        cfg = self._validate()
        if cfg is None:
            return
        sizes, cases, reps = cfg

        gap_name = self.gap.get()
        set_gap_sequence(gap_name)
        self.run_gap = gap_name

        self.run_id += 1
        rid = self.run_id
        self.data_rid = rid
        self.data = {c: self._empty() for c in CASES}
        self.stop_evt.clear()
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.gap.config(state="disabled")
        self.status.config(text="RUNNING", fg=WARN)
        self.progress["value"] = 0

        self.log(f"run: gaps={gap_name}  {len(sizes)} sizes "
                 f"({self.spacing.get()}) {sizes[0]}..{sizes[-1]}  "
                 f"cases={','.join(cases)}  repeats={reps}")
        if "worst" in cases and self.spacing.get() != "pow2":
            self.log("WARNING: worst case without pow2 spacing. The adversary "
                     "only lands on powers of two. See the help.")
        if not self.live_plot.get():
            self.log("live plot off: plots update once, at the end")

        self.worker = threading.Thread(
            target=self._work, args=(rid, gap_name, sizes, cases, reps),
            daemon=True)
        self.worker.start()

    def stop_analysis(self):
        if self.worker and self.worker.is_alive():
            self.stop_evt.set()
            self.status.config(text="STOPPING", fg=WARN)

    def clear_data(self):
        self.stop_analysis()
        self.data_rid = -1
        self.data = {c: self._empty() for c in CASES}
        self.progress["value"] = 0
        self.pending_redraw = False
        self._static_summary()
        self.log("data cleared")
        self.status.config(text="READY", fg=ACCENT)
        self.redraw()

    # -- worker thread (no Tk calls in here) -------------------------------------

    def _work(self, rid, gap_name, sizes, cases, reps):
        try:
            set_gap_sequence(gap_name)
            seed = 0xC0FFEE
            total = len(sizes) * len(cases)
            done = 0

            for n in sizes:
                for case in cases:
                    if self.stop_evt.is_set():
                        self.msgq.put((rid, "log", "stopped by user"))
                        self.msgq.put((rid, "done", "STOPPED"))
                        return

                    arr = make_array(n, case, seed)
                    t, loops = measure_time(arr, reps)
                    comparisons, moves = sort_counted(arr.copy())

                    self.msgq.put((rid, "point", case, {
                        "n": n, "time": t, "comparisons": comparisons,
                        "moves": moves, "input": input_footprint(arr),
                    }))
                    self.msgq.put((rid, "log",
                                   f"n={n:<6} {case:<7} t={t*1e6:9.2f} us  "
                                   f"cmp={comparisons:<9} "
                                   f"{MOVE_ABBR}={moves:<9} loops={loops}"))
                    done += 1
                    self.msgq.put((rid, "progress", 100.0 * done / total))

            if not self.stop_evt.is_set():
                self.msgq.put((rid, "log", "memory pass (isolated process)..."))
                mem, mode = measure_memory_isolated(
                    gap_name, sizes, cases, seed,
                    should_stop=self.stop_evt.is_set)
                if mode == "stopped":
                    self.msgq.put((rid, "log", "memory pass cancelled"))
                elif mem:
                    self.msgq.put((rid, "memory", mem))
                    self.msgq.put((rid, "log",
                                   f"memory pass ok: {len(mem)} points [{mode}]"))
                else:
                    self.msgq.put((rid, "log", "memory pass returned nothing"))

            final = "STOPPED" if self.stop_evt.is_set() else "COMPLETE"
            self.msgq.put((rid, "done", final))

        except Exception as exc:
            import traceback
            self.msgq.put((rid, "log", "ERROR: " + "".join(
                traceback.format_exception_only(type(exc), exc)).strip()))
            self.msgq.put((rid, "done", "ERROR"))

    # -- main-thread queue pump ---------------------------------------------------

    def _poll_queue(self):
        lines = []
        force = False
        try:
            while True:
                rid, kind, *rest = self.msgq.get_nowait()

                # A stopped worker can still be mid-measurement and will push
                # points AFTER a clear. Fence them off by run id.
                stale = (rid != self.data_rid)
                if stale and kind in ("point", "memory", "progress"):
                    continue

                if kind == "log":
                    lines.append(rest[0])
                elif kind == "progress":
                    self.progress["value"] = rest[0]
                elif kind == "point":
                    case, p = rest
                    s = self.data[case]
                    s["sizes"].append(p["n"])
                    s["time"].append(p["time"])
                    s["comparisons"].append(p["comparisons"])
                    s["moves"].append(p["moves"])
                    s["input_bytes"].append(p["input"])
                    self.pending_redraw = True
                elif kind == "memory":
                    results = rest[0]
                    for case in CASES:
                        s = self.data[case]
                        s["aux_bytes"] = [results.get((n, case), 0)
                                          for n in s["sizes"]]
                    self.pending_redraw = True
                elif kind == "done":
                    self.run_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    self.gap.config(state="readonly")
                    state = rest[0]
                    self.status.config(
                        text=state, fg=ACCENT if state == "COMPLETE" else DANGER)
                    lines.append(f"analysis {state.lower()}")
                    if not stale:
                        self._write_verdict()
                    self.pending_redraw = True
                    force = True
        except queue.Empty:
            pass

        if lines:
            ts = datetime.now().strftime("%H:%M:%S")
            self.console.insert(tk.END,
                                "".join(f"[{ts}] {ln}\n" for ln in lines))
            self.console.see(tk.END)

        running = bool(self.worker and self.worker.is_alive())
        if self.pending_redraw and (force or not running or self.live_plot.get()):
            self.pending_redraw = False
            self.redraw()

        self.root.after(100, self._poll_queue)

    # -- verdict --------------------------------------------------------------------

    def _write_verdict(self):
        key = GAP_KEY[self.run_gap]
        exp = EXPECTED[key]
        lines = [f"SHELL SORT  ::  EMPIRICAL",
                 f"gaps: {self.run_gap}",
                 "-" * 40]
        any_data = False

        for case in CASES:
            s = self.data[case]
            if not s["sizes"]:
                continue
            any_data = True
            lines.append(case.upper())
            fit = fit_exponent(s["sizes"], s["time"])
            if not fit:
                lines.append("  not enough points to fit")
                lines.append("")
                continue

            k, _ = fit
            lines.append(f"  k = {k:.3f}   T(n) ~ n^{k:.2f}")
            e = exp[case]
            if e is None:
                # This is the honest branch. There is no proven bound to grade
                # against, so the tool refuses to pretend there is.
                lines.append("  NO PROVEN BOUND")
                note = WORST_NOTE[key] if case == "worst" else \
                    "average-case shell sort is an OPEN PROBLEM;\n" \
                    "  no proof exists for any gap sequence"
                for ln in note.split("\n"):
                    lines.append("  " + ln.strip() if not ln.startswith("  ")
                                 else ln)
            else:
                ok = "matches" if abs(k - e) < 0.35 else "DEVIATES from"
                if case == "best":
                    lines.append(f"  {ok} predicted n log n (k~{e:.2f})")
                else:
                    lines.append(f"  {ok} predicted n^{e:.2f}")

            if case == "best" and s["comparisons"]:
                ratios = [c / (n * math.log2(n))
                          for n, c in zip(s["sizes"], s["comparisons"]) if n > 1]
                if ratios:
                    lines.append(f"  cmp/(n log2 n) = {min(ratios):.2f}"
                                 f"..{max(ratios):.2f}  (flat => n log n)")

            if s["comparisons"]:
                lines.append(f"  cmp @ n={s['sizes'][-1]}: {s['comparisons'][-1]:,}")
                lines.append(f"  {MOVE_ABBR} @ n={s['sizes'][-1]}: "
                             f"{s['moves'][-1]:,}")
            aux = s["aux_bytes"]
            lines.append(f"  aux peak = {max(aux) if aux else 0} B  (flat => O(1))")
            lines.append("")

        if not any_data:
            self._static_summary()
            return

        self.summary.delete("1.0", tk.END)
        self.summary.insert("1.0", "\n".join(lines))

    def _norm(self, n):
        mode = self.norm.get()
        if mode == "n":
            return float(n)
        if mode == "n log n":
            return float(n) * math.log2(n) if n > 1 else 1.0
        return float(n) ** 2

    # -- plotting -------------------------------------------------------------------

    def _style(self, ax, title, xlabel, ylabel):
        ax.set_facecolor(PANEL_2)
        ax.set_title(title, color=FG, fontsize=10, loc="left",
                     fontfamily="monospace")
        ax.set_xlabel(xlabel, color=MUTED, fontsize=9)
        ax.set_ylabel(ylabel, color=MUTED, fontsize=9)
        ax.tick_params(colors=MUTED, labelsize=8)
        for sp in ax.spines.values():
            sp.set_color(EDGE)
        ax.grid(True, alpha=0.25, color=GRID, linewidth=0.8)

    @staticmethod
    def _legend(ax):
        handles, _ = ax.get_legend_handles_labels()
        if not handles:
            return
        leg = ax.legend(facecolor=PANEL, edgecolor=EDGE, labelcolor=FG,
                        fontsize=8, framealpha=0.9)
        for txt in leg.get_texts():
            txt.set_fontfamily("monospace")

    def _has_data(self):
        return any(self.data[c]["sizes"] for c in CASES)

    def redraw(self):
        for ax in self.axes:
            ax.clear()

        self._style(self.ax1, "01  TIME vs n", "input size n", "time (s)")
        self._style(self.ax2, "02  OPERATION COUNTS", "input size n", "count")
        self._style(self.ax3, "03  MEMORY", "input size n", "bytes")
        self._style(self.ax4, f"04  T(n) / {self.norm.get()}",
                    "input size n", f"time / {self.norm.get()}")

        if not self._has_data():
            for ax in self.axes:
                ax.text(0.5, 0.5, "awaiting run", ha="center", va="center",
                        color=EDGE, fontsize=12, family="monospace",
                        transform=ax.transAxes)
            self.fig.suptitle(f"SHELL SORT  //  {self.gap.get()}", color=FG,
                              fontsize=13, family="monospace")
            self.canvas.draw_idle()
            return

        for case in CASES:
            s = self.data[case]
            if not s["sizes"]:
                continue
            self.ax1.plot(s["sizes"], s["time"], "o-", color=CASE_COLOR[case],
                          label=CASE_LABEL[case], linewidth=1.6, markersize=3.5)
        self._legend(self.ax1)

        # 02: counts on a log-log axis. Shell sort's cases span orders of
        # magnitude (140,033 vs 9,400 comparisons on the SAME input with
        # different gaps), so a linear axis flattens the cheap cases into the
        # floor and hides the entire point.
        for case in CASES:
            s = self.data[case]
            if not s["sizes"]:
                continue
            self.ax2.plot(s["sizes"], [max(1, v) for v in s["comparisons"]],
                          "o-", color=CASE_COLOR[case], label=f"cmp {case}",
                          linewidth=1.6, markersize=3.5)
            self.ax2.plot(s["sizes"], [max(1, v) for v in s["moves"]], "--",
                          color=CASE_COLOR[case], alpha=0.45, linewidth=1.2,
                          label=f"{MOVE_ABBR} {case}")
        all_n = sorted({n for c in CASES for n in self.data[c]["sizes"]})
        if all_n:
            for label, fn, colour, ls in REFERENCES:
                self.ax2.plot(all_n, [max(1, fn(n)) for n in all_n], ls,
                              color=colour, linewidth=1.4, label=label)
            self.ax2.set_xscale("log")
            self.ax2.set_yscale("log")
            self.ax2.set_ylabel("count (log)", color=MUTED, fontsize=9)
        self._legend(self.ax2)

        # 03: log y. On a linear axis the flat O(1) aux curve is crushed against
        # the x-axis by the O(n) input footprint, hiding the very result.
        have_mem = False
        for case in CASES:
            s = self.data[case]
            if not s["sizes"] or len(s["aux_bytes"]) != len(s["sizes"]):
                continue
            have_mem = True
            self.ax3.plot(s["sizes"], [max(1, v) for v in s["aux_bytes"]], "o-",
                          color=CASE_COLOR[case], label=f"aux {case}",
                          linewidth=1.6, markersize=3.5)
        ref = next((self.data[c] for c in CASES if self.data[c]["sizes"]), None)
        if ref:
            self.ax3.plot(ref["sizes"], ref["input_bytes"], "--", color=WARN,
                          linewidth=1.4, label="input footprint  O(n)")
        if have_mem or ref:
            self.ax3.set_yscale("log")
            self.ax3.set_ylabel("bytes (log)", color=MUTED, fontsize=9)
        if not have_mem:
            self.ax3.text(0.5, 0.9, "memory pass runs after timing",
                          ha="center", va="center", color=MUTED, fontsize=9,
                          family="monospace", transform=self.ax3.transAxes)
        self._legend(self.ax3)

        # 04: normalized. Exponents in the LEGEND, not the title, so they cannot
        # run off the right edge.
        for case in CASES:
            s = self.data[case]
            if not s["sizes"]:
                continue
            xs, ys = [], []
            for n, t in zip(s["sizes"], s["time"]):
                d = self._norm(n)
                if d > 0:
                    xs.append(n)
                    ys.append(t / d)
            if not xs:
                continue
            f = fit_exponent(s["sizes"], s["time"])
            lbl = f"{case}  n^{f[0]:.2f}" if f else case
            self.ax4.plot(xs, ys, "o-", color=CASE_COLOR[case], label=lbl,
                          linewidth=1.6, markersize=3.5)
            if len(xs) >= 2:
                z = np.polyfit(xs, ys, 1)
                self.ax4.plot(xs, np.poly1d(z)(xs), "--", color=CASE_COLOR[case],
                              alpha=0.4, linewidth=1.0)
        self._legend(self.ax4)

        self.fig.suptitle(f"SHELL SORT  //  {self.gap.get()}", color=FG,
                          fontsize=13, family="monospace")
        self.canvas.draw_idle()

    # -- help dialog ------------------------------------------------------------------

    def show_info(self):
        if self._info_win and self._info_win.winfo_exists():
            self._info_win.lift()
            self._info_win.focus_force()
            return

        win = tk.Toplevel(self.root)
        self._info_win = win
        win.title("REFERENCE  //  SHELL SORT")
        win.configure(bg=PANEL)
        win.transient(self.root)

        w, h = 900, 680
        self.root.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 3
        win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")

        header = tk.Frame(win, bg=PANEL)
        header.pack(fill=tk.X, padx=16, pady=(14, 6))
        tk.Label(header, text="REFERENCE", font=("Consolas", 14, "bold"),
                 bg=PANEL, fg=ACCENT).pack(side=tk.LEFT)
        tk.Label(header, text="  shell sort  ::  the gap sequence is the "
                              "algorithm",
                 font=MONO, bg=PANEL, fg=MUTED).pack(side=tk.LEFT, padx=(8, 0))

        nb = ttk.Notebook(win)
        nb.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 8))

        for label, body in (("  THE ALGORITHM  ", HELP_ALGORITHM),
                            ("  THE PARAMETERS  ", HELP_PARAMETERS)):
            tab = tk.Frame(nb, bg=PANEL_2)
            nb.add(tab, text=label)
            txt = scrolledtext.ScrolledText(tab, bg=PANEL_2, fg=FG, font=MONO,
                                            bd=0, wrap=tk.NONE, padx=14, pady=12)
            txt.pack(fill=tk.BOTH, expand=True)
            txt.tag_configure("head", foreground=ACCENT,
                              font=("Consolas", 9, "bold"))
            txt.tag_configure("rule", foreground=EDGE)
            txt.tag_configure("code", foreground=WARN)

            body = body.strip("\n")
            txt.insert("1.0", body)
            for i, line in enumerate(body.splitlines(), start=1):
                s = line.strip()
                if not s:
                    continue
                if set(s) <= set("=-") and len(s) > 3:
                    txt.tag_add("rule", f"{i}.0", f"{i}.end")
                elif line[:1] not in (" ", "\t") and s.isupper():
                    txt.tag_add("head", f"{i}.0", f"{i}.end")
                elif s.startswith(("for ", "while ", "tmp ", "a[j]", "j -=",
                                   "1. ", "2. ", "3. ", "[1, 5")):
                    txt.tag_add("code", f"{i}.0", f"{i}.end")
            txt.configure(state="disabled")

        footer = tk.Frame(win, bg=PANEL)
        footer.pack(fill=tk.X, padx=16, pady=(0, 14))
        tk.Label(footer, text="Esc to close", font=MONO, bg=PANEL,
                 fg=MUTED).pack(side=tk.LEFT)
        tk.Button(footer, text="CLOSE", command=win.destroy, bg=ACCENT, fg=BG,
                  font=MONO_B, bd=0, width=12,
                  activebackground="#00b880").pack(side=tk.RIGHT)

        win.bind("<Escape>", lambda _e: win.destroy())
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.grab_set()
        win.focus_force()

    # -- export ----------------------------------------------------------------------

    def export_csv(self):
        if not self._has_data():
            messagebox.showinfo("Nothing to export", "Run an analysis first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile="shell_sort_complexity.csv")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["algorithm", "gap_sequence", "case", "n",
                        "time_seconds", "comparisons", MOVE_NAME,
                        "aux_bytes", "input_bytes"])
            for case in CASES:
                s = self.data[case]
                aux = s["aux_bytes"]
                for i in range(len(s["sizes"])):
                    w.writerow([ALGO_NAME, self.run_gap, case, s["sizes"][i],
                                f"{s['time'][i]:.9f}", s["comparisons"][i],
                                s["moves"][i],
                                aux[i] if i < len(aux) else "",
                                s["input_bytes"][i]])
        self.log(f"exported: {path}")


def main():
    root = tk.Tk()
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure("TProgressbar", troughcolor=PANEL_2, background=ACCENT,
                    bordercolor=EDGE, lightcolor=ACCENT, darkcolor=ACCENT)

    # A readonly ttk Combobox ignores plain foreground config. Colours only take
    # effect through style.map on the "readonly" state.
    style.configure("TCombobox", arrowcolor=ACCENT, bordercolor=EDGE,
                    lightcolor=PANEL_2, darkcolor=PANEL_2)
    style.map("TCombobox",
              fieldbackground=[("readonly", PANEL_2)],
              background=[("readonly", PANEL_2)],
              foreground=[("readonly", FG)],
              selectbackground=[("readonly", PANEL_2)],
              selectforeground=[("readonly", FG)],
              arrowcolor=[("readonly", ACCENT)])
    root.option_add("*TCombobox*Listbox.background", PANEL_2)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", BG)
    root.option_add("*TCombobox*Listbox.font", "Consolas 9")

    style.configure("TNotebook", background=PANEL, borderwidth=0,
                    tabmargins=[2, 4, 2, 0])
    style.configure("TNotebook.Tab", background=PANEL_2, foreground=MUTED,
                    font=("Consolas", 9, "bold"), padding=[10, 6], borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", PANEL_2)],
              foreground=[("selected", ACCENT), ("active", FG)],
              expand=[("selected", [1, 1, 1, 0])])

    Analyzer(root)
    root.mainloop()


if __name__ == "__main__":
    mp.freeze_support()   # required for the spawn context on Windows
    main()
