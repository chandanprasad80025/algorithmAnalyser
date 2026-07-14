# How to use it

A walkthrough, from install to a result you can trust. Nothing here assumes you have read the code.

---

## 1. Install

You need Python 3.9 or newer.

```bash
pip install numpy matplotlib
```

`tkinter` ships with Python on Windows and macOS. On Debian or Ubuntu it is a separate package:

```bash
sudo apt install python3-tk
```

Check it worked:

```bash
python -c "import tkinter, numpy, matplotlib; print('ready')"
```

If that prints `ready`, you are done.

---

## 2. Verify before you trust

Do this once, before your first run. It takes about 40 seconds and it is the difference between believing the graphs and knowing they are right.

```bash
python verify.py
```

You want to see this at the bottom:

```
================================================================
  49 passed, 0 failed
================================================================
```

No window opens. This is a terminal script. If anything fails, it names the exact check and prints the measured value next to the expected one, so you know what broke without hunting.

You do not need to run this before every session. It is a change detector, for after you edit the measurement core or add an algorithm.

---

## 3. Launch

```bash
python complexity_analyzer.py
```

A window opens. The left panel is controls, the right is four empty graphs saying `awaiting run`. Nothing is measured yet.

Before touching anything, click **`? ALGORITHM + PARAMETERS`**. Read the ALGORITHM tab for whichever sort is selected. It takes two minutes and everything that follows will make sense.

---

## 4. Your first run

Do not change anything. The defaults are already the recommended settings:

| | |
|---|---|
| algorithm | bubble sort |
| min n / max n | 10 / 1000 |
| spacing | log |
| repeats | 5 |
| cases | all three ticked |
| live plot | off |

Click **RUN**.

**What happens, in order:**

1. Status goes amber, `RUNNING`. The RUN button greys out.
2. The console scrolls one line per measurement: `n=170  worst  t= 4821.33 us  cmp=14365  swp=14365  loops=2`
3. The progress bar fills.
4. The console says `memory pass (isolated process)...` and pauses for a second or two. This is a separate child process measuring auxiliary memory. It is meant to take a moment.
5. Status goes green, `COMPLETE`. **All four graphs appear at once.**

Total time: about 5 to 8 seconds.

**The graphs do not update while it runs.** That is deliberate, not a bug. A matplotlib redraw holds Python's GIL for tens of milliseconds, and if that lands inside a timing window it inflates the sample. Drawing live would corrupt the very numbers you are collecting.

---

## 5. Read the result

Look at the **RESULTS** panel on the left first. It now says something like:

```
BUBBLE SORT  ::  EMPIRICAL
----------------------------------------
BEST
  k = 0.975   T(n) ~ n^0.98
  matches predicted n^1
  cmp @ n=1000: 999
  swp @ n=1000: 0
  aux peak = 340 B  (flat => O(1))

AVERAGE
  k = 2.060   T(n) ~ n^2.06
  matches predicted n^2
  ...
```

**`k` is the whole point.** It is the exponent fitted from your actual measurements, not a label copied from a textbook. `matches predicted n^1` means the theory survived contact with reality on your machine.

Then the graphs:

**Panel 01, Time vs n.** The green best-case line lies flat on the x-axis while blue and red climb. That flat green line is bubble sort's early exit doing its job.

**Panel 02, Operation counts.** The red worst-case comparison curve sits exactly on the dotted `n(n-1)/2` reference line. Not near it. On it.

**Panel 03, Memory.** Log scale. The aux curves are horizontal at a few hundred bytes; the amber input footprint climbs. Horizontal means O(1). That contrast in slope is the finding.

**Panel 04, T(n)/n².** Blue and red go flat. Green dives toward zero. Read it as: **if the normaliser is the true growth rate, the line goes flat.** Blue and red are flat under n², so they are O(n²). Green is not, because it is O(n).

Now, to make that click, change **normalize by** from `n^2` to `n` and watch. No re-measurement happens; it re-plots instantly. Green goes flat. Blue and red climb. The inversion is the demonstration.

---

## 6. The experiment worth doing

This is what the tool is actually for.

1. Note bubble sort's exponents: roughly **0.98 / 2.06 / 2.00**.
2. Change **algorithm** to `selection sort`. The data clears, because the two are not comparable point for point.
3. Click **RUN**. Change nothing else.
4. Read the exponents: roughly **1.95 / 1.92 / 1.93**.

Look at the best case. Bubble: n^0.98. Selection: **n^1.95**.

Selection sort has no best case. Sorted input costs it exactly as many comparisons as reverse-sorted input, because you cannot know which element is the minimum without examining every candidate. Panel 01 shows three curves lying on top of each other. Panel 02 shows all three comparison curves coinciding with the `n(n-1)/2` line.

That is not a bug in the tool. It is adaptivity, or its absence, measured rather than asserted.

Then run insertion sort. It gets its best case back (n^1.04), and it is roughly **2.3x faster than bubble sort** at the same n in the average case, because it shifts (one write) where bubble swaps (three writes). Same complexity class, different constant factor, and you can see the gap in panel 01.

---

## 7. Controls you will actually use

**STOP** cancels a run in progress, including the memory pass. The status goes red `STOPPED` and you keep whatever data was collected.

**CLEAR** wipes the data and resets the graphs.

**EXPORT CSV** writes one row per (case, n) with time, comparisons, moves, aux bytes and input footprint. Note the move column is named for the algorithm: `swaps` for bubble and selection, `shifts` for insertion, because a shift is not a swap and labelling it as one would be wrong.

**live plot** turns on mid-run redrawing. Only use it to watch the curve build for a demo. The checkbox says `adds timing noise` and it means it: expect 20 to 30 percent spikes in panel 01. The numbers become decorative.

---

## 8. Tuning the parameters

The defaults are good. When you need to change them:

**Want a tighter, more publication-worthy fit?**
Set `max n = 2000` and `repeats = 9`. Slower, but the exponents tighten and vary less between runs.

**Want a smooth parabola for a slide?**
Set `spacing = linear`, `step = 10`, `max n = 400`. You get a dense pretty curve. The exponent fit gets *worse*, because a log-log regression learns from spread across orders of magnitude, not from point density. Accept the trade knowingly.

**Best-case curve is invisible, squashed against the axis?**
Untick `average` and `worst`, then run. The y-axis rescales onto the best case alone. Or set `normalize by = n` and look at panel 04 instead.

**Run is taking too long?**
`max n` is the lever. The sorts are quadratic, so halving `max n` cuts the top-end cost by roughly four. Do not lower `repeats` below 5 unless you are debugging the tool rather than the algorithm.

**Do not set `min n` below 10.** The sort finishes faster than the clock can resolve and you measure loop overhead instead of the algorithm.

---

## 9. Single-algorithm builds

If you want a tool that does one thing, use the standalone builds. Each folder is self-contained and references no other algorithm:

```bash
cd standalone/selection
python selection_sort_verify.py      # 26 checks, exit 0 if sound
python selection_sort_analyzer.py    # the app
```

Same interface, same measurement core, no dropdown. Useful for handing a student exactly one algorithm without the distraction of the others.

---

## 10. Troubleshooting

**`ModuleNotFoundError: No module named 'tkinter'`**
On Linux: `sudo apt install python3-tk`. It is not installable via pip.

**The verifier says `Cannot import complexity_analyzer.py`**
`verify.py` must sit in the same folder as the analyzer it tests.

**Windows: the app relaunches itself, or spawns extra windows**
The memory pass uses `multiprocessing` with the `spawn` context, which re-imports the main module in the child. That is safe here because of the `if __name__ == "__main__":` guard at the bottom of the file. If you copy code out of this project into a script of your own, **keep that guard**, or the script will recursively re-run itself.

**Console says `memory pass ok: 42 points [in-process]` instead of `[isolated]`**
The child process could not spawn and it fell back. The numbers are still usable because redraws are suppressed during a run, but they are not airtight. Most likely cause is running inside an unusual launcher or a frozen executable.

**Exponents come out slightly off, like 1.93 instead of 2.00**
Expected, and not a defect. CPython's per-call interpreter overhead is proportionally larger at small n, which flattens the low end of the curve and drags the fit down. Raise `max n` and it tightens. The tolerance in the verifier is 0.35 for exactly this reason.

**Timings look noisy or spiky**
Check that `live plot` is off. If it is, close other heavy applications; you are measuring on a shared machine and the minimum-of-repeats can only filter out so much.
