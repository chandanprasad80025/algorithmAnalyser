"""
VERIFICATION SUITE for insertion_sort_analyzer.py

Standalone. This program verifies insertion sort and nothing else.

USAGE
    Put this file in the SAME FOLDER as insertion_sort_analyzer.py, then:
        python insertion_sort_verify.py

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
    from insertion_sort_analyzer import (
        sort, sort_counted, make_array, measure_time, measure_memory_isolated,
        build_sizes, fit_exponent, EXPECTED, MOVE_NAME, STABLE,
        MAX_LOOPS, ELEMENT_BUDGET,
    )
except ImportError as exc:
    sys.exit(f"Cannot import insertion_sort_analyzer.py: {exc}\n"
             "Put this file in the SAME FOLDER as insertion_sort_analyzer.py, and\n"
             "make sure numpy and matplotlib are installed:\n"
             "    pip install numpy matplotlib")

SEED = 0xC0FFEE
CASES = ("best", "average", "worst")
ALGO = "insertion sort"

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
    "best":  lambda n: n - 1,
    "worst": lambda n: n * (n - 1) // 2,
}

THEORY_MOV = {
    "best":  (lambda n: 0, "=="),
    "worst": (lambda n: n * (n - 1) // 2, "=="),
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

    print("\n[7] SHIFTS == INVERSION COUNT (the O(n+d) mechanism)")
    print("     Each shift removes exactly ONE inversion, so these must be")
    print("     EQUAL, not merely correlated. This is the adaptivity claim,")
    print("     tested rather than asserted.")
    print(f"       {'disorder':>9} {'inversions d':>13} {'shifts':>8} {'cmps':>7} {'n-1+d':>7}")
    ok_eq = ok_bound = True
    for n, frac in ((60, 0.0), (60, 0.1), (60, 0.5), (60, 1.0)):
        a = _scramble(n, frac)
        d = inversions(a)
        cmps, shifts = sort_counted(a.copy())
        ok_eq &= (shifts == d)
        ok_bound &= (cmps <= n - 1 + d)
        print(f"       {frac:>9.0%} {d:>13} {shifts:>8} {cmps:>7} {n-1+d:>7}")
    check("shifts == d exactly, at every disorder level", ok_eq)
    check("comparisons <= n-1+d  (the O(n+d) bound holds)", ok_bound)

    print("\n[8] SHORT-CIRCUIT IS COUNTED CORRECTLY")
    print("     `while j >= 0 and a[j] > key` does NOT evaluate the comparison")
    print("     when j < 0. Counting it there would inflate best from n-1 to n.")
    ok = True
    for n in (50, 200, 500):
        c, mv = sort_counted(make_array(n, "best", SEED))
        ok &= (c == n - 1 and mv == 0)
    c, mv = sort_counted(make_array(500, "best", SEED))
    check("sorted input: exactly n-1 comparisons and 0 shifts", ok,
          f"n=500: {c} cmps (n-1={499}), {mv} shifts")

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
