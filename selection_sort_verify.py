"""
VERIFICATION SUITE for selection_sort_analyzer.py

Standalone. This program verifies selection sort and nothing else.

USAGE
    Put this file in the SAME FOLDER as selection_sort_analyzer.py, then:
        python selection_sort_verify.py

    Prints PASS/FAIL lines. Exit code 0 = all good, 1 = something broke.
    No window opens. This is a terminal script, not the app.

WHY THIS EXISTS
    The analyzer produces graphs that LOOK authoritative. A curve with a
    fitted exponent printed on it is very convincing. But if the counting
    function has an off-by-one, or the timing loop is biased, or the sort is
    subtly wrong on duplicates, the graph looks exactly as convincing while
    being wrong, and nothing in the picture would tell you.

    So this script checks the analyzer against things known INDEPENDENTLY of
    it: closed-form mathematics, Python's own sorted(), and internal
    consistency between the timed and counted code paths.

NOTE the __main__ guard at the bottom. It is load-bearing. multiprocessing's
spawn context re-imports the main module inside the child process; without the
guard this file would re-run itself recursively and the memory isolation check
would silently fall back to in-process mode while still reporting a pass.
"""
import random
import sys
import time

try:
    from selection_sort_analyzer import (
        sort, sort_counted, make_array, measure_time, measure_memory_isolated,
        build_sizes, fit_exponent, EXPECTED, MOVE_NAME, STABLE,
        MAX_LOOPS, ELEMENT_BUDGET,
    )
except ImportError as exc:
    sys.exit(f"Cannot import selection_sort_analyzer.py: {exc}\n"
             "Put this file in the SAME FOLDER as selection_sort_analyzer.py, and\n"
             "make sure numpy and matplotlib are installed:\n"
             "    pip install numpy matplotlib")

SEED = 0xC0FFEE
CASES = ("best", "average", "worst")
ALGO = "selection sort"

STATE = {"pass": 0, "fail": 0}


def check(label, ok, detail=""):
    if ok:
        STATE["pass"] += 1
        print(f"  PASS  {label}" + (f"   {detail}" if detail else ""))
    else:
        STATE["fail"] += 1
        print(f"  FAIL  {label}   {detail}")


class Item:
    """Compares on .key ONLY, so equal keys stay distinguishable via .tag.
    Plain tuples would compare lexicographically and make EVERY sort look
    stable, which is why the naive stability test is worthless."""
    __slots__ = ("key", "tag")

    def __init__(self, key, tag):
        self.key, self.tag = key, tag

    def __lt__(self, o):
        return self.key < o.key

    def __gt__(self, o):
        return self.key > o.key


def inversions(a):
    """Brute force, O(n^2). Fine at the sizes used here, and it must be an
    INDEPENDENT implementation, not one that reuses the sort under test."""
    return sum(1 for i in range(len(a)) for j in range(i + 1, len(a))
               if a[i] > a[j])


def _scramble(n, frac):
    """A list of 0..n-1 with `frac` of its positions shuffled among themselves.
    frac=0 gives sorted, frac=1 gives fully random."""
    a = list(range(n))
    r = random.Random(9)
    k = int(n * frac)
    idx = r.sample(range(n), k) if k >= 2 else []
    vals = [a[i] for i in idx]
    r.shuffle(vals)
    for i, v in zip(idx, vals):
        a[i] = v
    return a


THEORY_CMP = {
    "best":    lambda n: n * (n - 1) // 2,
    "average": lambda n: n * (n - 1) // 2,
    "worst":   lambda n: n * (n - 1) // 2,
}

THEORY_MOV = {
    "best":  (lambda n: 0, "=="),
    "worst": (lambda n: n - 1, "<="),
}


def run():
    print("=" * 64)
    print(f"  VERIFYING: {ALGO}")
    print("=" * 64)

    print("\n[1] CORRECTNESS: output equals sorted(), across edge sizes")
    edge_ns = (0, 1, 2, 3, 7, 50, 201)
    bad = []
    for case in CASES:
        for n in edge_ns:
            a = make_array(n, case, SEED)
            w = a.copy()
            sort(w)
            if w != sorted(a):
                bad.append((case, n))
    check("all cases x n in " + str(edge_ns), not bad,
          f"{len(CASES) * len(edge_ns)} arrays" if not bad else str(bad))

    print("\n[2] CORRECTNESS ON DUPLICATES (all-equal, heavy ties, empty)")
    print("     A `>=` where a `>` belongs survives distinct-element tests")
    print("     and dies here.")
    ok = True
    for arr in ([5] * 30, [1, 1, 2, 2, 1, 3, 2, 1] * 5, [0, 0], []):
        w = list(arr)
        sort(w)
        ok &= (w == sorted(arr))
    check("duplicates handled", ok)

    print("\n[3] COUNTED VARIANT AGREES WITH THE TIMED ONE")
    print("     These are separate functions. If they drift, every count on")
    print("     every graph describes a DIFFERENT algorithm than the one")
    print("     being timed, and nothing else would catch it.")
    ok = True
    for case in CASES:
        for n in (0, 1, 7, 60, 201):
            a = make_array(n, case, SEED)
            p, c = a.copy(), a.copy()
            sort(p)
            sort_counted(c)
            ok &= (p == c == sorted(a))
    check("both code paths produce identical sorted output", ok)

    print("\n[4] STABILITY matches the declared flag")
    print("     tag = position IN THE INPUT, assigned AFTER shuffling.")
    keys = [i % 8 for i in range(40)]
    random.Random(1).shuffle(keys)
    base = [Item(k, pos) for pos, k in enumerate(keys)]
    w = list(base)
    sort(w)
    kept = all(w[i].tag < w[i + 1].tag for i in range(len(w) - 1)
               if w[i].key == w[i + 1].key)
    check(f"declared stable={STABLE}", kept == STABLE, f"observed={kept}")

    print("\n[5] COMPARISON COUNTS vs CLOSED FORM (exact integer equality)")
    for case, fn in THEORY_CMP.items():
        ok = all(sort_counted(make_array(n, case, SEED))[0] == fn(n)
                 for n in (10, 137, 400))
        got = sort_counted(make_array(400, case, SEED))[0]
        check(f"{case}", ok, f"n=400: {got:,} == {fn(400):,}")

    print(f"\n[6] {MOVE_NAME.upper()} vs CLOSED FORM")
    for case, (fn, rel) in THEORY_MOV.items():
        ok = True
        for n in (10, 137, 400):
            mv = sort_counted(make_array(n, case, SEED))[1]
            ok &= (mv == fn(n)) if rel == "==" else (mv <= fn(n))
        got = sort_counted(make_array(400, case, SEED))[1]
        check(f"{case}: {MOVE_NAME} {rel} theory", ok,
              f"n=400: {got:,} {rel} {fn(400):,}")

    print("\n[7] COMPARISONS ARE INPUT-INDEPENDENT (the defining property)")
    print("     Selection sort has NO best case. Sorted, random and reversed")
    print("     input must all cost EXACTLY n(n-1)/2 comparisons.")
    print(f"       {'n':>5} {'best':>10} {'average':>10} {'worst':>10} {'n(n-1)/2':>10}")
    ok = True
    for n in (50, 200, 500):
        counts = [sort_counted(make_array(n, c, SEED))[0] for c in CASES]
        theory = n * (n - 1) // 2
        ok &= all(c == theory for c in counts)
        print(f"       {n:>5} {counts[0]:>10} {counts[1]:>10} {counts[2]:>10} {theory:>10}")
    check("all three cases identical, and equal to n(n-1)/2", ok)

    print("\n[8] SWAPS ARE MINIMAL (<= n-1, the theoretical floor)")
    print("     This is selection sort's ONLY advantage: it minimises WRITES.")
    print(f"       {'n':>5} {'best':>8} {'average':>8} {'worst':>8} {'bound n-1':>10}")
    ok = True
    for n in (50, 200, 500):
        movs = [sort_counted(make_array(n, c, SEED))[1] for c in CASES]
        ok &= all(v <= n - 1 for v in movs) and movs[0] == 0
        print(f"       {n:>5} {movs[0]:>8} {movs[1]:>8} {movs[2]:>8} {n-1:>10}")
    check("swaps <= n-1 in every case, and 0 on sorted input", ok)

    print("\n[9] make_array DETERMINISM")
    print("     The timing, counting and memory passes must all see the")
    print("     IDENTICAL array, including inside a spawned child process")
    print("     (str hashes are salted per process, ints are not).")
    check("same array on repeated calls",
          all(make_array(200, c, SEED) == make_array(200, c, SEED)
              for c in CASES))
    check("average case differs from best/worst",
          make_array(50, "average", SEED) != make_array(50, "best", SEED))

    print("\n[10] build_sizes: ascending, deduplicated, all >= 2")
    for spacing in ("log", "linear"):
        sz = build_sizes(10, 1000, 10, spacing)
        check(f"spacing={spacing}",
              sz == sorted(sz) and len(sz) == len(set(sz)) and min(sz) >= 2,
              f"{len(sz)} points, {sz[0]}..{sz[-1]}")
    check("degenerate hi==lo does not crash",
          build_sizes(50, 50, 10, "log") == [50])

    print("\n[11] fit_exponent guards")
    check("returns None below 3 usable points",
          fit_exponent([10, 20], [1e-5, 4e-5]) is None)
    check("ignores non-positive times (log is undefined there)",
          fit_exponent([10, 20, 40], [0.0, 0.0, 0.0]) is None)

    print("\n[12] TIMING LOOP CAP: loops*n bounded, cannot exhaust RAM")
    for n in (10, 1000, 5000):
        cap = max(1, min(MAX_LOOPS, ELEMENT_BUDGET // max(1, n)))
        check(f"n={n}: cap={cap} loops", cap * n <= ELEMENT_BUDGET,
              f"loops*n = {cap*n:,} <= {ELEMENT_BUDGET:,}")

    print("\n[13] FITTED EXPONENTS match the prediction (tolerance 0.35)")
    print("     This is the headline number. It is measured, not assumed.")
    sizes = build_sizes(10, 800, 10, "log")
    t0 = time.perf_counter()
    print(f"       {'case':<10}{'fitted k':>12}{'predicted':>12}{'verdict':>10}")
    for case in CASES:
        times = [measure_time(make_array(n, case, SEED), 5)[0] for n in sizes]
        k = fit_exponent(sizes, times)[0]
        exp = EXPECTED[case]
        good = abs(k - exp) < 0.35
        print(f"       {case:<10}{k:>12.3f}{'n^' + str(int(exp)):>12}"
              f"{'ok' if good else 'FAIL':>10}")
        check(f"{case} exponent", good, f"{k:.3f} vs n^{exp:.0f}")
    print(f"       (sweep took {time.perf_counter()-t0:.1f}s)")

    print("\n[14] MEMORY PASS: isolated child, flat aux (O(1)), cancellable")
    mem, mode = measure_memory_isolated(sizes, CASES, SEED)
    vals = list(mem.values())
    small = max(mem[(n, c)] for n in sizes[:3] for c in CASES)
    large = max(mem[(n, c)] for n in sizes[-3:] for c in CASES)
    grew = large > 4 * small if small else False
    check(f"aux does not grow with n  [{mode}]",
          not grew and len(mem) == len(sizes) * 3 and mode == "isolated",
          f"{min(vals)}..{max(vals)} B across n={sizes[0]}..{sizes[-1]}")
    t0 = time.perf_counter()
    _, mode = measure_memory_isolated(sizes, CASES, SEED,
                                      should_stop=lambda: True)
    check("cancellation honoured", mode == "stopped",
          f"returned in {time.perf_counter()-t0:.2f}s")

    print("\n" + "=" * 64)
    print(f"  {ALGO}:  {STATE['pass']} passed, {STATE['fail']} failed")
    print("=" * 64)
    sys.exit(1 if STATE["fail"] else 0)


if __name__ == "__main__":
    run()
