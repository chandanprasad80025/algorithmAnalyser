# Algorithm Complexity Analyzer

A desktop tool that **measures** the time complexity of sorting algorithms instead of asserting it.

Most teaching tools draw a curve that goes up and to the right, label it `O(n²)`, and stop. That curve appears whether or not the code is correct, because a quadratic sort is slow no matter how badly you implement it. This tool takes a different position: it runs the algorithm, times it across a range of input sizes, fits `log T(n) = k·log n + c` by least squares, and reports **k**. If the theory holds, k converges on 2. If your implementation is broken, it does not.

Everything on screen is a measurement. Nothing is a claim.

![Algorithm Complexity Analyzer running a bubble sort analysis across four panels](/screenshot.png)

<p align="center">
<sub>A completed bubble sort run, n = 10 to 1000, log-spaced, minimum of 5 repeats.<br>
Fitted exponents: <b>best n^0.96, average n^2.04, worst n^2.0</b>. The theory survived.</sub>
</p>

Three things in that screenshot are worth pointing at, because they are the whole argument:

- **Panel 01.** The green best-case line lies flat on the x-axis while the other two climb. That flat line is bubble sort's early exit, doing its job.
- **Panel 02.** The red worst-case comparison curve does not merely resemble the dotted `n(n-1)/2` reference. It sits **on** it. At n=1000 that is 499,500 comparisons, predicted and measured.
- **Panel 04.** Blue and red go flat under `T(n)/n²`, so they are quadratic. Green dives toward zero, because `T ~ n` means `T/n² ~ 1/n`. It is not quadratic, and the graph says so without being told.

And in the RESULTS panel, the sentence the whole project exists to print: `k = 0.959 ... matches predicted n^1`.

---

## Table of contents

- [What it does](#what-it-does)
- [Results](#results)
- [Quick start](#quick-start)
- [**Full usage guide →**](USAGE.md)
- [Reading the four panels](#reading-the-four-panels)
- [The parameters](#the-parameters)
- [Verification](#verification)
- [Measurement methodology](#measurement-methodology)
- [Project structure](#project-structure)
- [Adding an algorithm](#adding-an-algorithm)
- [Known limitations](#known-limitations)
- [Roadmap](#roadmap)
- [License](#license)

---

## What it does

For each algorithm, across best, average and worst case inputs, it measures:

| Panel | What it shows |
|---|---|
| **01 Time vs n** | Wall-clock time. The shape you expect, measured honestly. |
| **02 Operation counts** | Comparisons and moves, plotted against the closed-form theoretical curve. |
| **03 Memory** | Auxiliary space (flat, `O(1)`) against the input footprint (climbing, `O(n)`). |
| **04 T(n) / f(n)** | Normalised growth. If `f(n)` is the true growth rate, the line goes **flat**. |

Plus a fitted exponent per case, an interactive reference modal covering the algorithm theory and every parameter, and CSV export.

**Currently implemented:** bubble sort, selection sort, insertion sort.

---

## Results

Measured on the included test suite, log-spaced sizes from n=10 to n=800, minimum of 5 repeats:

| Algorithm | Best | Average | Worst | Adaptive | Stable |
|---|---|---|---|---|---|
| Bubble sort | **0.975** vs n¹ | 2.060 vs n² | 1.999 vs n² | passes only | yes |
| Selection sort | **1.953** vs n²  | 1.921 vs n² | 1.932 vs n² | **no** | no |
| Insertion sort | **1.040** vs n¹ | 2.037 vs n² | 2.003 vs n² | fully, `O(n+d)` | yes |

The bolded column is the interesting one. Bubble and insertion collapse to a linear best case. **Selection sort does not, and that is the whole point.** It has no best case: it performs exactly `n(n-1)/2` comparisons on every input of size n, sorted or not, because you cannot know the minimum without examining every candidate. Sorted input buys you zero swaps and not one comparison less.

That is not a footnote in a textbook. It is a number you can watch the tool produce.

Exponents will vary by a few hundredths on your hardware. The fits are reproducible run to run because the inputs are seeded.

---

## Quick start

**Requirements:** Python 3.9+, `numpy`, `matplotlib`, `tkinter` (bundled with Python on Windows and macOS; `sudo apt install python3-tk` on Debian/Ubuntu).

```bash
git clone https://github.com/YOURNAME/complexity-analyzer.git
cd complexity-analyzer
pip install numpy matplotlib
```

Run the unified tool, which has all three algorithms behind a dropdown:

```bash
python complexity_analyzer.py
```

Or run a single-algorithm build, which is fully self-contained:

```bash
cd standalone/selection
python selection_sort_analyzer.py
```

Verify the numbers before you trust them:

```bash
python verify.py            # 49 checks, exit code 0 if all pass
```

**New here?** Read [USAGE.md](USAGE.md) for a step-by-step walkthrough: your first run, how to read the four panels, and the one experiment that makes the whole thing click.

---

## Reading the four panels

**Panel 01, Time vs n.** The raw measurement. Bubble and insertion show a best-case curve pinned to the x-axis while average and worst climb quadratically. Selection shows three curves lying almost on top of each other.

**Panel 02, Operation counts.** Comparisons (solid) and moves (dashed) with a dotted theoretical reference line. Selection sort's three comparison curves coincide exactly with the `n(n-1)/2` line, which is the input-independence result made visible. Insertion sort's shift curve is a shifted metric, not swaps: a swap is three writes, a shift is one, and calling one the other would be a factual error in the legend.

**Panel 03, Memory.** Log y-axis, deliberately. On a linear axis the auxiliary curve (a few hundred bytes) is crushed against the x-axis by the input footprint (kilobytes), so you cannot *see* that it is flat, which is the entire result. Log scale shows both, and the contrast in slope is the finding: aux horizontal, footprint climbing.

**Panel 04, Normalised growth.** Read it like this: **if the chosen function is the true growth rate, the line goes flat**, and the height of that flat line is the constant factor. With `n²` selected, bubble's average and worst go flat while its best decays toward zero (because T ~ n, so T/n² ~ 1/n). Switch the normaliser to `n` and best goes flat instead while the others climb. That inversion is the demonstration. On selection sort there is no inversion, because there is nothing to invert.

---

## The parameters

| Parameter | Meaning |
|---|---|
| **min n / max n** | Size range. `max n` is the biggest lever on runtime: the sorts are quadratic, so doubling it roughly quadruples the time at the top end. |
| **step** | Gap between sizes. **Ignored under log spacing.** |
| **spacing** | `log` (default) or `linear`. See below. |
| **repeats** | Times each measurement is taken. The tool keeps the **minimum**, not the mean. |
| **cases** | best (sorted), average (random, seeded), worst (reverse sorted). |
| **normalize by** | `n`, `n log n`, or `n²`. Controls panel 04 only, re-plots instantly with no new measurement. |
| **live plot** | Off by default. See below. Leave it off. |

**Why log spacing is the default.** Linear spacing crowds most of your points into a narrow band of magnitudes, which is exactly where a log-log regression learns the least. Log spacing gives 14 points instead of 100, a full sweep in about 5 seconds instead of minutes, and a *better* fit. Use linear only when you want a smooth parabola for a slide and are willing to accept a worse exponent.

**Why the minimum, not the mean.** Measurement noise is strictly additive. A scheduler preemption or a cache miss can only make a run slower, never faster. The fastest observed run is therefore the closest estimate of the true cost, and averaging simply drags that estimate upward by whatever noise happened to occur.

**Why live plot is off.** A matplotlib redraw of four subplots holds Python's GIL for tens of milliseconds. If that lands inside a timing window, the sample is inflated. Turning it on produced visible 20 to 30 percent spikes in panel 01. With it off, the plots update once at the end and the measurement is clean. The checkbox is labelled honestly: enabling it makes the numbers decorative rather than trustworthy.

---

## Verification

`verify.py` is a terminal script, not a GUI. It prints PASS/FAIL lines and exits 0 or 1, so it is scriptable:

```bash
python verify.py && python complexity_analyzer.py    # launch only if the numbers are sound
```

It exists because **a graph with a fitted exponent printed on it is extremely convincing whether or not it is correct.** If the counting function has an off-by-one, or the timing loop is biased, or the sort mishandles duplicates, the graph looks exactly as authoritative while being wrong, and nothing in the picture would tell you.

So the suite checks the analyzer against things known *independently* of it:

| Check | What it proves |
|---|---|
| Output equals `sorted()` | Correctness across n = 0, 1, 2, 3, 7, 50, 201 |
| Duplicates and empty lists | A `>=` where a `>` belongs survives distinct-element tests and dies here |
| Timed path vs counted path | These are separate functions. If they drift, every count describes a *different algorithm* than the one being timed |
| Comparison counts vs closed form | Exact integer equality. At n=400, selection sort must give 79,800, not "about 80,000" |
| Move counts vs closed form | Selection ≤ n−1 swaps (minimal writes); insertion = n(n−1)/2 shifts in the worst case |
| Stability vs the declared flag | Uses a compare-on-key-only wrapper, because plain tuples compare lexicographically and make *every* sort look stable |
| **Shifts == inversion count, exactly** | This is the `O(n+d)` adaptivity claim, tested rather than asserted |
| Fitted exponent vs prediction | The headline number, graded against each algorithm's own spec |
| Memory pass isolated and flat | Confirms `O(1)` auxiliary space and that cancellation works |

**The suite immediately earned its keep.** On its first run it exposed two bugs in itself, one of which was silently making the memory check test the wrong code path while still reporting a pass. It also confirmed a real bias I had introduced in the measurement core (see below).

---

## Measurement methodology

This is the part that separates the tool from a plotting exercise.

**The timed function carries no counters.** Counting runs as a separate pass on a separate function. Instrumenting the hot loop would inflate the constant factor of the thing you are trying to measure.

**The workload is held constant across repeats.** An earlier version generated a fresh random array on every repeat and then took the minimum. That is wrong, and subtly so: the minimum is only a valid noise estimator when the workload is fixed. Across five different permutations it instead selects the **easiest** one. And because random permutations concentrate as n grows (13.3% spread in comparison count at n=10, but only 1.6% at n=500), the bug deflated small n far more than large n, **tilting the fitted exponent upward**. It was quietly inflating the one number the tool exists to produce. Inputs are now deterministic per `(n, case, seed)`.

**Memory is measured in an isolated child process.** `tracemalloc` traces the entire process, not one thread. Measured in-process, every matplotlib redraw allocating inside the traced window gets charged to the sort, which produced spurious 800 kB spikes on a value whose true answer is a flat few hundred bytes. The child process does nothing but sort.

**Timing batches are calibrated.** For small n the sort finishes faster than the clock can resolve, so the tool runs it in a tight loop until the batch exceeds 10 ms, then divides. Array copies are pre-allocated outside the timing window, and the GC is disabled during it.

**The verdict is a regression, not an eyeball.** `T(n)/n²` going flat is suggestive. A least-squares fit of `log T = k·log n + c` giving k = 1.999 is evidence.

---

## Project structure

```
complexity_analyzer.py          Unified tool. All algorithms, dropdown selector.
verify.py                       49-check suite for the unified tool.

standalone/
    bubble_sort_analyzer.py     Self-contained. Knows about bubble sort only.
    bubble_sort_verify.py       25 checks.
    selection_sort_analyzer.py  Self-contained. Knows about selection sort only.
    selection_sort_verify.py    26 checks.
    insertion_sort_analyzer.py  Self-contained. Knows about insertion sort only.
    insertion_sort_verify.py    26 checks.
```

The `standalone/` builds import nothing from each other and reference no algorithm but their own. Use them if you want a single-purpose tool to hand to a student. Use the unified build if you want to compare algorithms back to back, which is where the tool is most instructive.

---

## Adding an algorithm

In the unified build, an algorithm is a dataclass:

```python
"merge": AlgoSpec(
    key="merge",
    name="merge sort",
    sort=merge_sort,                 # pure, gets timed
    sort_counted=merge_sort_counted, # instrumented, never timed
    move_name="writes",              # NOT always "swaps". Name it honestly.
    move_abbr="wrt",
    expected={"best": 1.1, "average": 1.1, "worst": 1.1},
    ...
)
```

`expected` is the exponent the verifier grades your fit against. Register merge sort, run the suite, and if the fit comes back at 2.0 rather than roughly 1.1, you have a bug and you will know inside a minute rather than squinting at a curve.

Note that merge sort will also be the first algorithm here with genuinely `O(n)` auxiliary space, so panel 03 will finally show a climbing aux curve instead of a flat one. The isolated memory pass is what makes that measurable.

---

## Known limitations

- **`gc.disable()` inside the timing batch is process-global.** With live plot enabled, the main thread does its redraws with the GC off. Harmless, brief, and one more reason to leave live plot off.
- **CPython constant factors bend the fits slightly.** Selection sort lands near 1.93 rather than 2.00 because its inner loop is a bare comparison with no swap, so per-call interpreter overhead is proportionally larger at small n and flattens the low end. Push `max n` higher and it tightens. This is a property of the interpreter, not a defect in the measurement.
- **The auxiliary memory floor is a few hundred bytes.** That is `tracemalloc`'s own bookkeeping, not the sort. What matters is that it does not *grow* with n.
- **Sorts are pure Python.** This measures the algorithm as the interpreter executes it, which is the honest thing to measure for a teaching tool, but it is not a benchmark of the algorithm in C.

---

## Roadmap

- [ ] Merge sort and quicksort (the first `O(n log n)` entries, and the first with non-trivial auxiliary space)
- [ ] Side-by-side algorithm overlay on one axis
- [ ] Nearly-sorted input generator with a tunable inversion count, to make adaptivity visible as a dial rather than three fixed cases
- [ ] Export the fitted model, not just the raw points

---

## License

MIT. See [LICENSE](LICENSE).

---

## A closing note

Bubble sort survives in curricula, not in production. Insertion sort dominates it on every axis, and Python's own `sorted()` runs Timsort, which uses insertion sort as its base case for short runs. This tool is not here to help you pick a sorting algorithm. It is here to demonstrate that complexity claims are **testable**, and to make the act of testing them routine rather than exceptional.
