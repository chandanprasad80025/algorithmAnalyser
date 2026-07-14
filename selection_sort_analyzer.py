"""
SELECTION SORT COMPLEXITY ANALYZER

Standalone. This program analyses selection sort and nothing else.

Empirical time, operation counts, auxiliary space, and a log-log exponent fit
that TESTS the theoretical complexity rather than assuming it.

Measurement discipline (why the numbers can be trusted):
  * The timed function carries no counters. Counting is a separate pass.
  * Each measurement calibrates an inner loop until a batch exceeds 10 ms,
    disables the GC, and takes the MINIMUM of N repeats on a FIXED array.
    Noise is additive, so the fastest sample is closest to the true cost.
    The array must be held constant: taking the min across freshly generated
    random arrays would instead select the easiest permutation, and since
    permutations concentrate as n grows, that would tilt the fitted exponent.
  * Memory is measured with tracemalloc in an ISOLATED CHILD PROCESS, because
    tracemalloc traces the whole process. In-process, every matplotlib redraw
    would be charged to the sort.
  * Plots do not redraw mid-run by default. A redraw holds the GIL for tens of
    milliseconds and inflates any timing sample it lands on.
  * The verdict is a least-squares fit of log T = k*log n + c. k is the answer.

Usage:  python selection_sort_analyzer.py
Verify: python selection_sort_verify.py
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

CASES = ("best", "average", "worst")
CASE_COLOR = {"best": "#00e5a0", "average": "#34c3ff", "worst": "#ff5470"}

MONO   = ("Consolas", 9)
MONO_B = ("Consolas", 10, "bold")


# ----------------------------------------------------------------------------
# The algorithm
# ----------------------------------------------------------------------------

ALGO_NAME = "selection sort"
MOVE_NAME = "swaps"          # what the second metric actually IS
MOVE_ABBR = "swp"
STABLE    = False
ADAPTIVE  = False

# Expected exponent k in T(n) ~ n^k, per case. Drives the pass/fail verdict.
EXPECTED = {"best": 2.0, "average": 2.0, "worst": 2.0}

CASE_LABEL = {
    "best":    "Sorted  O(n^2)",
    "average": "Random  O(n^2)",
    "worst":   "Reversed  O(n^2)",
}


def sort(a):
    """
    Pure, uninstrumented selection sort. This is the function that gets timed.

    Note what is ABSENT: any early exit. There cannot be one. You cannot know
    which element is the minimum without examining every candidate, so the
    inner loop must run to completion on every pass no matter how sorted the
    input already is. That is why selection sort is Theta(n^2) on EVERY input.
    """
    n = len(a)
    for i in range(n - 1):
        m = i
        for j in range(i + 1, n):
            if a[j] < a[m]:
                m = j
        if m != i:
            a[i], a[m] = a[m], a[i]


def sort_counted(a):
    """Instrumented variant. Never timed, only counted."""
    n = len(a)
    comparisons = moves = 0
    for i in range(n - 1):
        m = i
        for j in range(i + 1, n):
            comparisons += 1
            if a[j] < a[m]:
                m = j
        if m != i:
            a[i], a[m] = a[m], a[i]
            moves += 1
    return comparisons, moves


# ----------------------------------------------------------------------------
# Input generation
# ----------------------------------------------------------------------------

CASE_INDEX = {"best": 0, "average": 1, "worst": 2}


def make_array(size, case, seed):
    """
    Deterministic for a given (size, case, seed). This matters more than it
    looks. The timing pass, the counting pass and the memory pass all see the
    IDENTICAL array, a run is reproducible regardless of which cases happen to
    be ticked, and because the seed is derived from an int rather than a hashed
    string, the spawned child process reproduces it exactly (str hashes are
    salted per process).
    """
    if case == "best":
        return list(range(size))                # already sorted
    if case == "worst":
        return list(range(size, 0, -1))         # reverse sorted
    rng = random.Random(seed * 1_000_003 + size * 31 + CASE_INDEX[case])
    return [rng.randint(1, 10_000) for _ in range(size)]


# ----------------------------------------------------------------------------
# Measurement
# ----------------------------------------------------------------------------

MIN_BATCH_SECONDS = 0.010
MAX_LOOPS = 4096
ELEMENT_BUDGET = 4_000_000   # cap on loops * n, so a batch cannot exhaust RAM


def _time_batch(arr, loops):
    """Time `loops` sorts, each on a fresh copy. Copy cost is excluded."""
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
    """
    Calibrate an inner loop past clock granularity, then take the MINIMUM
    across repeats of that SAME array. Holding the workload constant is what
    makes the minimum a valid noise estimator.
    """
    cap = max(1, min(MAX_LOOPS, ELEMENT_BUDGET // max(1, len(arr))))
    loops = 1
    while loops < cap:
        per_call = _time_batch(arr, loops)
        if per_call * loops >= MIN_BATCH_SECONDS:
            break
        loops = min(cap, loops * 4)
    samples = [_time_batch(arr, loops) for _ in range(repeats)]
    return min(samples), loops


def _memory_worker(sizes, cases, seed, out_q):
    """
    Runs in a CHILD PROCESS. tracemalloc traces the entire process, not one
    thread, so in the GUI process every matplotlib redraw allocating inside the
    traced window would be attributed to the sort. Here nothing else allocates.
    """
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


def measure_memory_isolated(sizes, cases, seed=0xC0FFEE, timeout=180,
                            should_stop=None):
    """
    Returns (results, mode) where mode is 'isolated', 'in-process' or 'stopped'.

    The result is POLLED rather than fetched with one blocking get(). A blocking
    get would sit for the full timeout if the child died on startup, which is a
    long silent hang for the user.
    """
    proc = None
    try:
        ctx = mp.get_context("spawn")
        q = ctx.Queue()
        proc = ctx.Process(target=_memory_worker, args=(sizes, cases, seed, q),
                           daemon=True)
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

    # Fallback: the child could not spawn or died (frozen exe, odd launcher).
    # Still usable because redraws are suppressed during a run, but not
    # airtight, so the caller labels it.
    out_q = queue.Queue()
    _memory_worker(sizes, cases, seed, out_q)
    return out_q.get(), "in-process"


def build_sizes(lo, hi, step, spacing, points=14):
    """Log spacing gives a better exponent fit per unit of runtime."""
    if hi <= lo:
        return [lo]
    if spacing == "log":
        xs = np.geomspace(lo, hi, num=points)
        return [x for x in sorted({int(round(v)) for v in xs}) if x >= 2]
    return list(range(lo, hi + 1, step))


def input_footprint(arr):
    """Bytes held by the list object itself (the pointer array). O(n)."""
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


# ----------------------------------------------------------------------------
# Reference curves for panel 02
# ----------------------------------------------------------------------------

REFERENCES = [
    ("n(n-1)/2  cmp, ALL cases", lambda n: n * (n - 1) / 2, WARN, ":"),
    ("n-1  swap bound", lambda n: n - 1, "#9c6bff", "-."),
]


# ----------------------------------------------------------------------------
# Help content
# ----------------------------------------------------------------------------

SUMMARY = (
"SELECTION SORT  ::  ANALYTICAL\n"
"----------------------------------------\n"
"TIME\n"
"  best     O(n^2)    NO best case\n"
"  average  O(n^2)\n"
"  worst    O(n^2)\n"
"  tight bound: Theta(n^2)\n"
"\n"
"COMPARISONS\n"
"  ALL cases  n(n-1)/2  exactly\n"
"  input order changes nothing\n"
"\n"
"SWAPS\n"
"  best     0\n"
"  others   <= n - 1   (linear)\n"
"  this is the minimum possible\n"
"\n"
"SPACE\n"
"  auxiliary  O(1)\n"
"\n"
"adaptive: NO     stable: no\n"
"\n"
"Expect all three exponents near 2.0,\n"
"including best. That is the result.\n"
)

HELP_ALGORITHM = """
SELECTION SORT
========================================================================

DEFINITION
  Divides the list into a sorted prefix and an unsorted suffix. On each pass
  it scans the ENTIRE unsorted suffix to find the minimum element, then swaps
  that minimum into the boundary position. The sorted prefix grows by one
  element per pass.

  In-place (O(1) auxiliary space) and NOT stable in this standard swap-based
  form. It can be made stable by shifting instead of swapping, but that turns
  n-1 swaps into O(n^2) moves and throws away its only advantage.

HOW IT WORKS
  for i in 0 .. n-2:
      m = i
      for j in i+1 .. n-1:
          if a[j] < a[m]:
              m = j                 <- track the minimum, do not swap yet
      if m != i:
          swap(a[i], a[m])          <- at most ONE swap per pass

  Note what is ABSENT: there is no early exit and no `swapped` flag. There
  cannot be. You cannot know which element is the minimum without examining
  every candidate, so the inner loop must run to completion on every pass
  regardless of how sorted the input already is.

COMPLEXITY
  ------------------------------------------------------------------
  case       time      comparisons    swaps        input
  ------------------------------------------------------------------
  best       O(n^2)    n(n-1)/2       0            already sorted
  average    O(n^2)    n(n-1)/2       <= n - 1     random
  worst      O(n^2)    n(n-1)/2       <= n - 1     reverse sorted
  ------------------------------------------------------------------
  Auxiliary space: O(1) in every case.

  Read that comparisons column again. It is the SAME NUMBER in all three
  rows. Selection sort performs exactly n(n-1)/2 comparisons on every input of
  size n. There is no best case. Sorted input buys you zero swaps and not one
  comparison less.

  This is why the time bound is written Theta(n^2) rather than O(n^2). The
  bound is tight from both directions. Big-O alone would be an understatement,
  since it claims only an upper limit.

THE ONE THING IT IS GOOD AT
  Selection sort performs at most n-1 swaps, which is the theoretical minimum
  for any algorithm that sorts by exchanging elements.

  So if WRITES are drastically more expensive than READS, selection sort wins
  despite doing the same number of comparisons as its quadratic peers. This is
  not hypothetical. On EEPROM or flash, a write costs orders of magnitude more
  than a read AND consumes one of a finite number of erase cycles. In that
  setting, minimising writes is minimising hardware wear, not just time.

  Outside that niche it is dominated on every axis. It is not adaptive, it is
  not stable, and it does not get faster on easy input.

WHAT TO EXPECT FROM THIS TOOL
  Run with all three cases ticked. All three time curves in panel 01 will lie
  almost on top of each other, and all three fitted exponents in panel 04 will
  land near 2.0, INCLUDING the best case.

  That is not a bug. It IS the result. The absence of a spread between the
  cases is exactly what "not adaptive" means, measured rather than asserted.

  In panel 02 the three comparison curves will coincide exactly with the
  dotted n(n-1)/2 line. The swap curves will hug the x-axis, flattened by the
  quadratic y-scale, because n-1 is invisible next to n^2/2. Untick the other
  cases to rescale the axis and see them.
"""

HELP_PARAMETERS = """
PARAMETERS
========================================================================

min n / max n
  Smallest and largest input size tested. Every size between them (per the
  spacing rule) becomes one point on every graph.

  How to adjust:
    min n below about 10 is not useful. The sort finishes faster than the
    clock can resolve, so loop overhead dominates the measurement.

    max n is the single biggest lever on runtime. This algorithm is quadratic,
    so doubling max n roughly QUADRUPLES the time spent at the top end.

    Recommended: 10 to 1000. Go to 2000 only for a tighter fit.

step
  Gap between consecutive sizes. ONLY APPLIES WHEN SPACING IS LINEAR.
  It is ignored entirely under log spacing, which derives its own sizes.

spacing            linear | log        (default: log)
  linear  min, min+step, min+2*step, ... Dense and even, but it crowds most of
          your points into one narrow band of magnitudes, which is exactly
          where a log-log regression learns the least.

  log     Geometric: 10, 14, 20, 29, 41, 59, 84, 119, 170, 242, 346, 492, 702,
          1000. Fourteen points instead of a hundred, a full sweep in seconds
          instead of minutes, and a BETTER exponent fit, because the fit cares
          about spread across orders of magnitude, not point density.

  How to adjust: leave it on log. Switch to linear only when you want a smooth
  curve for a slide rather than an accurate exponent.

repeats
  How many times each (size, case) pair is timed. The tool takes the MINIMUM
  of the samples, not the mean.

  Why the minimum: measurement noise is strictly additive. A scheduler
  preemption or a cache miss can only make a run slower, never faster. So the
  fastest observed run is the closest estimate of the true cost, and averaging
  merely drags that estimate upward by whatever noise happened to occur.

  Note that every repeat sorts a copy of the SAME array. That is deliberate.
  The minimum is only a valid noise estimator when the workload is held
  constant; taking the min across freshly generated random arrays would select
  the easiest permutation instead.

  How to adjust:
    5   fine for a quick look (default)
    9   noticeably steadier, roughly doubles runtime
    1   only when debugging the tool, not the algorithm

  Each repeat is itself a calibrated batch: for small n the sort runs many
  times in a tight loop until the batch exceeds 10 ms, then divides. That is
  what makes n = 10 measurable at all.

cases              best | average | worst
  Which input orderings to test.

    best     already sorted     list(range(n))
    average  random order       reproducible, seeded
    worst    reverse sorted     list(range(n, 0, -1))

  These names describe the INPUT SHAPE. Whether the "best" input actually
  produces a better running time depends on the algorithm, and finding that
  out is the point of the exercise. See the ALGORITHM tab.

  How to adjust: run all three. The overlay is the whole point. Untick a case
  to let the y-axis rescale onto the remaining ones.

normalize by       n | n log n | n^2      (default: n^2)
  Controls panel 04 only. It plots T(n) divided by the function you pick.

  Read it like this: IF the chosen function is the true growth rate, the line
  goes FLAT. Flat means you found the right complexity class, and the height of
  the line is the constant factor.

  How to adjust: change it after a run. It re-plots instantly, no new
  measurement needed.

live plot                                 (default: OFF)
  Whether the graphs redraw while the analysis is still running.

  Leave it OFF. A matplotlib redraw of four subplots holds Python's GIL for
  tens of milliseconds. If that lands inside a timing window, the sample is
  inflated, which shows up as 20 to 30 percent spikes in panel 01. With it off
  the plots update once, at the end, and the measurement is clean.

  Turn it on only to watch the curve build for a demo, and accept that the
  numbers are then decorative rather than trustworthy.

========================================================================
SUGGESTED PRESETS

  Fast and accurate (default)
    min 10   max 1000   log   repeats 5   all cases   live off
    About 5 seconds. Exponents land near their predictions.

  Publication quality
    min 10   max 2000   log   repeats 9   all cases   live off

  Smooth curve for a slide
    min 10   max 400   linear   step 10   repeats 5   live off
    Pretty parabola, worse fit. Accept the trade.
"""


# ----------------------------------------------------------------------------
# Application
# ----------------------------------------------------------------------------

class Analyzer:

    def __init__(self, root):
        self.root = root
        self.root.title(f"{ALGO_NAME.upper()}  //  COMPLEXITY ANALYZER")
        self.root.geometry("1600x900")
        self.root.configure(bg=BG)

        self.data = {c: self._empty() for c in CASES}
        self.msgq = queue.Queue()
        self.stop_evt = threading.Event()
        self.worker = None
        self.run_id = 0        # increments on every run
        self.data_rid = -1     # which run currently owns self.data
        self.pending_redraw = False
        self._info_win = None

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

        left = tk.Frame(outer, bg=PANEL, width=430)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left.pack_propagate(False)

        tk.Label(left, text=ALGO_NAME.upper(), font=("Consolas", 15, "bold"),
                 bg=PANEL, fg=ACCENT).pack(pady=(16, 2))
        tk.Label(left, text="complexity analyzer  ::  empirical + analytical",
                 font=MONO, bg=PANEL, fg=MUTED).pack(pady=(0, 4))
        tk.Label(left,
                 text=f"adaptive: {'yes' if ADAPTIVE else 'no'}    "
                      f"stable: {'yes' if STABLE else 'no'}",
                 font=MONO, bg=PANEL, fg=MUTED).pack(pady=(0, 12))

        params = tk.LabelFrame(left, text=" PARAMETERS ", bg=PANEL, fg=FG,
                               font=MONO_B, bd=1, relief=tk.SOLID)
        params.pack(fill=tk.X, padx=12, pady=4)

        self.min_size = tk.IntVar(value=10)
        self.max_size = tk.IntVar(value=1000)
        self.step_size = tk.IntVar(value=10)
        self.repeats = tk.IntVar(value=5)

        self._spin(params, "min n", self.min_size, 2, 5000)
        self._spin(params, "max n", self.max_size, 2, 5000)
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
        self.spacing = ttk.Combobox(srow, values=["linear", "log"],
                                    state="readonly", width=12, font=MONO)
        self.spacing.set("log")
        self.spacing.pack(side=tk.RIGHT)

        nrow = tk.Frame(params, bg=PANEL)
        nrow.pack(fill=tk.X, padx=8, pady=(0, 4))
        tk.Label(nrow, text="normalize by", bg=PANEL, fg=FG,
                 font=MONO).pack(side=tk.LEFT)
        self.norm = ttk.Combobox(nrow, values=["n", "n log n", "n^2"],
                                 state="readonly", width=12, font=MONO)
        self.norm.set("n^2")
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
        self.progress = ttk.Progressbar(left, length=390, mode="determinate")
        self.progress.pack(pady=(0, 10), padx=12)

        sf = tk.LabelFrame(left, text=" RESULTS ", bg=PANEL, fg=FG, font=MONO_B,
                           bd=1, relief=tk.SOLID)
        sf.pack(fill=tk.X, padx=12, pady=4)
        self.summary = scrolledtext.ScrolledText(sf, height=13, bg=PANEL_2,
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

    # -- logging (main thread only) --------------------------------------------

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.insert(tk.END, f"[{ts}] {msg}\n")
        self.console.see(tk.END)

    def _banner(self):
        for line in ("=" * 52,
                     f"{ALGO_NAME.upper()} ANALYZER  //  ONLINE",
                     "=" * 52,
                     "timing  : min-of-repeats on a FIXED array, gc off",
                     "counts  : separate uninstrumented pass",
                     "memory  : tracemalloc in an isolated child process",
                     "verdict : exponent fitted on log-log regression",
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
        if not sizes:
            messagebox.showerror("Invalid input", "No sizes in that range.")
            return None
        return sizes, cases, reps

    def run_analysis(self):
        if self.worker and self.worker.is_alive():
            return
        cfg = self._validate()
        if cfg is None:
            return
        sizes, cases, reps = cfg

        self.run_id += 1
        rid = self.run_id
        self.data_rid = rid
        self.data = {c: self._empty() for c in CASES}
        self.stop_evt.clear()
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status.config(text="RUNNING", fg=WARN)
        self.progress["value"] = 0

        self.log(f"run: {len(sizes)} sizes ({self.spacing.get()}) "
                 f"{sizes[0]}..{sizes[-1]}  cases={','.join(cases)}  "
                 f"repeats={reps}")
        if not self.live_plot.get():
            self.log("live plot off: plots update once, at the end")

        self.worker = threading.Thread(target=self._work,
                                       args=(rid, sizes, cases, reps),
                                       daemon=True)
        self.worker.start()

    def stop_analysis(self):
        if self.worker and self.worker.is_alive():
            self.stop_evt.set()
            self.status.config(text="STOPPING", fg=WARN)

    def clear_data(self):
        self.stop_analysis()
        # Disown the arrays. A worker still finishing a measurement will keep
        # emitting points for a moment; the pump drops them because their run
        # id no longer matches.
        self.data_rid = -1
        self.data = {c: self._empty() for c in CASES}
        self.progress["value"] = 0
        self.pending_redraw = False
        self._static_summary()
        self.log("data cleared")
        self.status.config(text="READY", fg=ACCENT)
        self.redraw()

    # -- worker thread (no Tk calls in here) -------------------------------------

    def _work(self, rid, sizes, cases, reps):
        try:
            seed = 0xC0FFEE
            total = len(sizes) * len(cases)
            done = 0

            # Pass 1: timing and counts. Same array for both, and for pass 2.
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
                                   f"n={n:<5} {case:<7} t={t*1e6:9.2f} us  "
                                   f"cmp={comparisons:<8} "
                                   f"{MOVE_ABBR}={moves:<8} loops={loops}"))
                    done += 1
                    self.msgq.put((rid, "progress", 100.0 * done / total))

            # Pass 2: memory, isolated. Interruptible via should_stop.
            if not self.stop_evt.is_set():
                self.msgq.put((rid, "log", "memory pass (isolated process)..."))
                mem, mode = measure_memory_isolated(
                    sizes, cases, seed, should_stop=self.stop_evt.is_set)
                if mode == "stopped":
                    self.msgq.put((rid, "log", "memory pass cancelled"))
                elif mem:
                    self.msgq.put((rid, "memory", mem))
                    self.msgq.put((rid, "log",
                                   f"memory pass ok: {len(mem)} points [{mode}]"))
                else:
                    self.msgq.put((rid, "log", "memory pass returned nothing"))

            # STOP may have landed during the memory pass. Do not claim COMPLETE.
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
                # more points AFTER a clear. Fence them off: data messages are
                # only accepted from the run that currently owns the arrays.
                # `done` is exempt so the buttons always get re-enabled.
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

        # One Text.insert per pump cycle, not one per data point.
        if lines:
            ts = datetime.now().strftime("%H:%M:%S")
            self.console.insert(tk.END,
                                "".join(f"[{ts}] {ln}\n" for ln in lines))
            self.console.see(tk.END)

        # A redraw holds the GIL for tens of milliseconds. Doing it while the
        # timing thread runs is what inflates samples, so: not mid-run.
        running = bool(self.worker and self.worker.is_alive())
        if self.pending_redraw and (force or not running or self.live_plot.get()):
            self.pending_redraw = False
            self.redraw()

        self.root.after(100, self._poll_queue)

    # -- verdict --------------------------------------------------------------------

    def _write_verdict(self):
        lines = [f"{ALGO_NAME.upper()}  ::  EMPIRICAL", "-" * 40]
        any_data = False

        for case in CASES:
            s = self.data[case]
            if not s["sizes"]:
                continue
            any_data = True
            lines.append(case.upper())
            fit = fit_exponent(s["sizes"], s["time"])
            if fit:
                k, _ = fit
                expected = EXPECTED[case]
                ok = "matches" if abs(k - expected) < 0.35 else "DEVIATES from"
                lines.append(f"  k = {k:.3f}   T(n) ~ n^{k:.2f}")
                lines.append(f"  {ok} predicted n^{expected:.0f}")
            else:
                lines.append("  not enough points to fit")

            tail_n, tail_t = s["sizes"][-3:], s["time"][-3:]
            ratios = [t / self._norm(n)
                      for n, t in zip(tail_n, tail_t) if self._norm(n) > 0]
            if ratios:
                lines.append(f"  T(n)/{self.norm.get()} tail = {np.mean(ratios):.3e}")
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
            self.fig.suptitle(f"{ALGO_NAME.upper()}  COMPLEXITY", color=FG,
                              fontsize=13, family="monospace")
            self.canvas.draw_idle()
            return

        # 01 time
        for case in CASES:
            s = self.data[case]
            if not s["sizes"]:
                continue
            self.ax1.plot(s["sizes"], s["time"], "o-", color=CASE_COLOR[case],
                          label=CASE_LABEL[case], linewidth=1.6, markersize=3.5)
        self._legend(self.ax1)

        # 02 counts, with theoretical references
        for case in CASES:
            s = self.data[case]
            if not s["sizes"]:
                continue
            self.ax2.plot(s["sizes"], s["comparisons"], "o-",
                          color=CASE_COLOR[case], label=f"cmp {case}",
                          linewidth=1.6, markersize=3.5)
            self.ax2.plot(s["sizes"], s["moves"], "--", color=CASE_COLOR[case],
                          alpha=0.45, linewidth=1.2,
                          label=f"{MOVE_ABBR} {case}")
        all_n = sorted({n for c in CASES for n in self.data[c]["sizes"]})
        if all_n:
            for label, fn, colour, ls in REFERENCES:
                self.ax2.plot(all_n, [fn(n) for n in all_n], ls, color=colour,
                              linewidth=1.4, label=label)
        self._legend(self.ax2)

        # 03 memory. Log y-axis on purpose: on a linear axis the aux curve (a
        # few hundred bytes) is crushed against the x-axis by the input
        # footprint (kilobytes), so you cannot SEE that it is flat, which is
        # the result. Log scale shows both, and the contrast in slope is the
        # point: aux horizontal, footprint climbing.
        have_mem = False
        for case in CASES:
            s = self.data[case]
            if not s["sizes"] or len(s["aux_bytes"]) != len(s["sizes"]):
                continue
            have_mem = True
            ys = [max(1, v) for v in s["aux_bytes"]]   # log cannot take 0
            self.ax3.plot(s["sizes"], ys, "o-", color=CASE_COLOR[case],
                          label=f"aux {case}", linewidth=1.6, markersize=3.5)
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

        # 04 normalized growth
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
            self.ax4.plot(xs, ys, "o-", color=CASE_COLOR[case], label=case,
                          linewidth=1.6, markersize=3.5)
            if len(xs) >= 2:
                z = np.polyfit(xs, ys, 1)
                self.ax4.plot(xs, np.poly1d(z)(xs), "--", color=CASE_COLOR[case],
                              alpha=0.4, linewidth=1.0)

        fits = []
        for case in CASES:
            s = self.data[case]
            f = fit_exponent(s["sizes"], s["time"]) if s["sizes"] else None
            if f:
                fits.append(f"{case}: n^{f[0]:.2f}")
        if fits:
            self.ax4.set_title(f"04  T(n) / {self.norm.get()}   [" +
                               "  ".join(fits) + "]", color=FG, fontsize=9,
                               loc="left", fontfamily="monospace")
        self._legend(self.ax4)

        self.fig.suptitle(f"{ALGO_NAME.upper()}  COMPLEXITY", color=FG,
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
        win.title(f"REFERENCE  //  {ALGO_NAME.upper()}")
        win.configure(bg=PANEL)
        win.transient(self.root)

        w, h = 880, 660
        self.root.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 3
        win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")

        header = tk.Frame(win, bg=PANEL)
        header.pack(fill=tk.X, padx=16, pady=(14, 6))
        tk.Label(header, text="REFERENCE", font=("Consolas", 14, "bold"),
                 bg=PANEL, fg=ACCENT).pack(side=tk.LEFT)
        tk.Label(header, text=f"  {ALGO_NAME}  ::  theory, complexity, and how "
                              f"to drive this tool",
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
                elif s.startswith(("for ", "if ", "swap(", "swapped", "m = ",
                                   "key = ", "1. ", "2. ")):
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
            initialfile="selection_sort_complexity.csv")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["algorithm", "case", "n", "time_seconds", "comparisons",
                        MOVE_NAME, "aux_bytes", "input_bytes"])
            for case in CASES:
                s = self.data[case]
                aux = s["aux_bytes"]
                for i in range(len(s["sizes"])):
                    w.writerow([ALGO_NAME, case, s["sizes"][i],
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

    # A readonly ttk Combobox ignores plain foreground config. The colours only
    # take effect through style.map on the "readonly" state.
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
