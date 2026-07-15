"""
VERIFICATION SUITE for shell_sort_analyzer.py

Standalone. This program verifies shell sort and nothing else.

USAGE
    Put this file in the SAME FOLDER as shell_sort_analyzer.py, then:
        python shell_sort_verify.py

    Prints PASS/FAIL lines. Exit code 0 = all good, 1 = something broke.
    No window opens. This is a terminal script, not the app.

WHAT IS DIFFERENT ABOUT VERIFYING SHELL SORT
    The elementary sorts have closed-form comparison counts. n(n-1)/2 is a
    number you can check against with integer equality. Shell sort has no such
    formula for ANY gap sequence, and its average case is a genuine open
    problem in computer science.

    So this suite cannot check counts against a closed form. Instead it checks
    the INVARIANTS that define the algorithm:

      * after a pass with gap h, the array is h-SORTED
      * later passes never DESTROY earlier h-sortedness (this is the theorem
        that makes shell sort work at all)
      * the gap sequence must end at 1, or the algorithm does not sort
      * the adversarial input defeats the halving sequence and NOTHING ELSE

    Where no proven bound exists, the suite REPORTS the measurement and says
    so, rather than inventing a number to grade against.

NOTE the __main__ guard at the bottom. multiprocessing's spawn context
re-imports the main module in the child; without the guard this file would
re-run itself recursively and the memory check would silently fall back.
"""
import math
import random
import sys
import time

try:
    from shell_sort_analyzer import (
        sort, sort_counted, h_sort_pass, is_h_sorted, make_array, adversarial,
        measure_time, measure_memory_isolated, build_sizes, fit_exponent,
        set_gap_sequence, GAP_SEQUENCES, GAP_KEY, EXPECTED, STABLE, MOVE_NAME,
        MAX_LOOPS, ELEMENT_BUDGET, gaps_shell, gaps_knuth,
    )
except ImportError as exc:
    sys.exit(f"Cannot import shell_sort_analyzer.py: {exc}\n"
             "Put this file in the SAME FOLDER as shell_sort_analyzer.py, and\n"
             "make sure numpy and matplotlib are installed:\n"
             "    pip install numpy matplotlib")

SEED = 0xC0FFEE
CASES = ("best", "average", "worst")
SEQS = list(GAP_SEQUENCES)

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


def run():
    print("=" * 66)
    print("  VERIFYING: shell sort")
    print("=" * 66)

    print("\n[1] CORRECTNESS: output equals sorted(), every gap sequence")
    edge_ns = (0, 1, 2, 3, 7, 50, 201)
    for name in SEQS:
        set_gap_sequence(name)
        bad = []
        for case in CASES:
            for n in edge_ns:
                a = make_array(n, case, SEED)
                w = a.copy()
                sort(w)
                if w != sorted(a):
                    bad.append((case, n))
        check(name, not bad,
              f"{len(CASES) * len(edge_ns)} arrays" if not bad else str(bad))

    print("\n[2] CORRECTNESS ON DUPLICATES (all-equal, heavy ties, empty)")
    for name in SEQS:
        set_gap_sequence(name)
        ok = True
        for arr in ([5] * 30, [1, 1, 2, 2, 1, 3, 2, 1] * 5, [0, 0], []):
            w = list(arr)
            sort(w)
            ok &= (w == sorted(arr))
        check(name, ok)

    print("\n[3] COUNTED VARIANT AGREES WITH THE TIMED ONE")
    print("     Separate code paths. If they drift, every count on every graph")
    print("     describes a different algorithm than the one being timed.")
    for name in SEQS:
        set_gap_sequence(name)
        ok = True
        for case in CASES:
            for n in (0, 1, 7, 60, 201):
                a = make_array(n, case, SEED)
                p, c = a.copy(), a.copy()
                sort(p)
                sort_counted(c)
                ok &= (p == c == sorted(a))
        check(name, ok)

    print("\n[4] STABILITY matches the declared flag (shell sort is NOT stable)")
    keys = [i % 8 for i in range(40)]
    random.Random(1).shuffle(keys)
    base = [Item(k, pos) for pos, k in enumerate(keys)]
    set_gap_sequence("shell (n/2)")
    w = list(base)
    sort(w)
    kept = all(w[i].tag < w[i + 1].tag for i in range(len(w) - 1)
               if w[i].key == w[i + 1].key)
    check(f"declared stable={STABLE}", kept == STABLE, f"observed={kept}")

    print("\n[5] GAP SEQUENCES ARE WELL FORMED")
    print("     Strictly decreasing, every gap < n, and ENDING AT 1.")
    for name, fn in GAP_SEQUENCES.items():
        ok = True
        for n in (2, 5, 16, 100, 1024, 5000):
            g = fn(n)
            if not g:
                ok &= (n <= 1)
                continue
            ok &= (g == sorted(g, reverse=True))    # strictly decreasing
            ok &= (len(g) == len(set(g)))           # no repeats
            ok &= all(1 <= x < n for x in g)        # in range
            ok &= (g[-1] == 1)                      # MUST end at 1
        check(name, ok, f"n=1024 -> {fn(1024)[:6]}...")

    print("\n[6] A SEQUENCE THAT DOES NOT END AT 1 FAILS TO SORT")
    print("     This is why the final gap must be 1. Demonstrated, not asserted.")
    a = make_array(64, "average", SEED)
    w = a.copy()
    for gap in [8, 4, 2]:          # deliberately stops at 2, never reaches 1
        h_sort_pass(w, gap)
    check("gaps [8,4,2] leave the array UNSORTED", w != sorted(a),
          "2-sorted is not sorted")
    check("but it IS correctly 2-sorted", is_h_sorted(w, 2),
          "the passes did their job; the sequence was the problem")

    print("\n[7] THE h-SORTEDNESS INVARIANT")
    print("     After a pass with gap h, the array must satisfy a[i] <= a[i+h]")
    print("     for every i. This is the definition of an h-sorted array.")
    ok = True
    for n in (64, 100, 256):
        for gap in (13, 8, 5, 3, 1):
            a = make_array(n, "average", SEED)
            h_sort_pass(a, gap)
            ok &= is_h_sorted(a, gap)
    check("every pass leaves the array h-sorted", ok,
          "tested gaps 13,8,5,3,1 at n=64,100,256")

    print("\n[8] LATER PASSES PRESERVE EARLIER h-SORTEDNESS")
    print("     THE theorem that makes shell sort work: k-sorting an h-sorted")
    print("     array leaves it STILL h-sorted. Without this, every pass would")
    print("     undo the last one and the algorithm would be pointless.")
    ok = True
    detail = ""
    for name in SEQS:
        set_gap_sequence(name)
        fn = GAP_SEQUENCES[name]
        for n in (128, 256):
            a = make_array(n, "average", SEED)
            seen = []
            for gap in fn(n):
                h_sort_pass(a, gap)
                seen.append(gap)
                # every gap processed SO FAR must still hold
                for h in seen:
                    if not is_h_sorted(a, h):
                        ok = False
                        detail = f"{name}: {h}-sortedness destroyed by {gap}-sort"
    check("no pass ever destroys a previous h-sortedness", ok, detail)

    print("\n[9] THE ADVERSARY: it defeats the halving sequence and NOTHING ELSE")
    print("     Small values at EVEN indices, large at ODD. Every halving gap")
    print("     before the last is even (when n is a power of two), so no pass")
    print("     ever compares an even index with an odd one. Nothing moves")
    print("     until gap=1, which then faces ~n^2/8 inversions.")
    n = 1024
    print(f"\n       adversarial input, n={n}")
    print(f"       {'gap sequence':<18}{'comparisons':>13}{'vs n^2/8':>11}")
    counts = {}
    for name in SEQS:
        set_gap_sequence(name)
        c, _ = sort_counted(make_array(n, "worst", SEED))
        counts[name] = c
        print(f"       {name:<18}{c:>13,}{c / (n * n / 8):>10.2f}x")
    halving = counts["shell (n/2)"]
    others = [v for k, v in counts.items() if k != "shell (n/2)"]
    check("halving sequence pays ~n^2/8", 0.5 < halving / (n * n / 8) < 2.0,
          f"{halving:,} vs n^2/8 = {int(n*n/8):,}")
    check("every other sequence shrugs it off", all(halving > 5 * v for v in others),
          f"halving is {halving / max(others):.1f}x worse than the next")

    print("\n[10] THE ADVERSARY NEEDS POWERS OF TWO")
    print("     The halving gaps are only all-even when n is a power of two.")
    print("     At n=1000 an odd gap (125) appears and mixes the index classes,")
    print("     silently destroying the construction. This is why the tool has")
    print("     a pow2 spacing mode.")
    set_gap_sequence("shell (n/2)")
    c1000, _ = sort_counted(make_array(1000, "worst", SEED))
    c1024, _ = sort_counted(make_array(1024, "worst", SEED))
    g1000 = gaps_shell(1000)
    g1024 = gaps_shell(1024)
    print(f"\n       n=1000  gaps={g1000[:6]}  all even before 1: "
          f"{all(g % 2 == 0 for g in g1000[:-1])}")
    print(f"       n=1024  gaps={g1024[:6]}  all even before 1: "
          f"{all(g % 2 == 0 for g in g1024[:-1])}")
    print(f"       n=1000 -> {c1000:>8,} comparisons")
    print(f"       n=1024 -> {c1024:>8,} comparisons")
    check("n=1024 gaps are all even before the final 1",
          all(g % 2 == 0 for g in g1024[:-1]))
    check("n=1000 gaps are NOT (an odd gap breaks the adversary)",
          not all(g % 2 == 0 for g in g1000[:-1]))
    check("2% more input, an order of magnitude more work", c1024 > 10 * c1000,
          f"{c1024 / c1000:.1f}x more comparisons")

    print("\n[11] THE BEST CASE IS n log n, NOT n")
    print("     Sorted input is cheap but NOT linear. Each pass still walks")
    print("     every element once to discover it is in place, and there are")
    print("     O(log n) passes. cmp/(n log2 n) must be FLAT, not decaying.")
    sizes = [2 ** k for k in range(5, 12)]
    ok_all = True
    print(f"\n       {'gap sequence':<18}{'fitted k':>10}{'cmp/(n log2 n)':>20}")
    for name in SEQS:
        set_gap_sequence(name)
        cs = [sort_counted(make_array(n, "best", SEED))[0] for n in sizes]
        k = fit_exponent(sizes, cs)[0]
        ratios = [c / (n * math.log2(n)) for n, c in zip(sizes, cs)]
        flat = max(ratios) / min(ratios) < 2.0
        ok_all &= flat and (1.0 < k < 1.5)
        print(f"       {name:<18}{k:>10.3f}{min(ratios):>12.2f}"
              f"..{max(ratios):.2f}")
    check("cmp/(n log2 n) is flat, and k lands between 1.0 and 1.5", ok_all,
          "confirms Theta(n log n): NOT Theta(n)")

    print("\n[12] make_array DETERMINISM")
    check("same array on repeated calls",
          all(make_array(200, c, SEED) == make_array(200, c, SEED)
              for c in CASES))
    check("adversarial is the even/odd interleave",
          adversarial(8) == [1, 5, 2, 6, 3, 7, 4, 8],
          f"n=8 -> {adversarial(8)}")

    print("\n[13] build_sizes, including the pow2 mode")
    p2 = build_sizes(16, 1024, 10, "pow2")
    check("pow2 gives only powers of two",
          all((x & (x - 1)) == 0 for x in p2), f"{p2}")
    check("pow2 rounds min n up to the enclosing power of two",
          build_sizes(10, 1000, 10, "pow2")[0] == 16,
          f"10..1000 -> {build_sizes(10, 1000, 10, 'pow2')}")
    for spacing in ("log", "linear"):
        sz = build_sizes(16, 1024, 10, spacing)
        check(f"spacing={spacing}",
              sz == sorted(sz) and len(sz) == len(set(sz)) and min(sz) >= 2,
              f"{len(sz)} points, {sz[0]}..{sz[-1]}")

    print("\n[14] fit_exponent guards, and the timing loop cap")
    check("returns None below 3 usable points",
          fit_exponent([10, 20], [1e-5, 4e-5]) is None)
    check("ignores non-positive times (log is undefined there)",
          fit_exponent([10, 20, 40], [0.0, 0.0, 0.0]) is None)
    for n in (16, 1024, 8192):
        cap = max(1, min(MAX_LOOPS, ELEMENT_BUDGET // max(1, n)))
        check(f"loop cap at n={n}: {cap}", cap * n <= ELEMENT_BUDGET,
              f"loops*n = {cap*n:,} <= {ELEMENT_BUDGET:,}")

    print("\n[15] FITTED TIME EXPONENTS, per gap sequence")
    print("     Graded ONLY where a proven bound exists. Where none does, the")
    print("     measurement is reported and left ungraded, because there is")
    print("     nothing honest to grade it against.")
    sizes = build_sizes(16, 1024, 10, "pow2")
    t0 = time.perf_counter()
    print(f"\n       {'gaps':<18}{'best':>18}{'average':>18}{'worst':>18}")
    for name in SEQS:
        set_gap_sequence(name)
        key = GAP_KEY[name]
        cells = []
        for case in CASES:
            times = [measure_time(make_array(n, case, SEED), 5)[0]
                     for n in sizes]
            k = fit_exponent(sizes, times)[0]
            e = EXPECTED[key][case]
            if e is None:
                cells.append(f"n^{k:.2f} (no bound)")
            else:
                good = abs(k - e) < 0.35
                cells.append(f"n^{k:.2f} vs {e:.2f} {'ok' if good else 'XX'}")
                check(f"{name} / {case}", good, f"k={k:.3f}, predicted {e:.2f}")
        print(f"       {name:<18}" + "".join(f"{c:>18}" for c in cells))
    print(f"       (sweep took {time.perf_counter()-t0:.1f}s)")
    print("\n     The `no bound` cells are not a failure of this tool. Shell")
    print("     sort's average case is an OPEN PROBLEM: no proof exists for any")
    print("     gap sequence. A tool that printed a confident checkmark there")
    print("     would be lying.")

    print("\n[16] MEMORY PASS: isolated child, flat aux (O(1)), cancellable")
    mem, mode = measure_memory_isolated("shell (n/2)", sizes, CASES, SEED)
    vals = list(mem.values())
    small = max(mem[(n, c)] for n in sizes[:3] for c in CASES)
    large = max(mem[(n, c)] for n in sizes[-3:] for c in CASES)
    grew = large > 4 * small if small else False
    check(f"aux does not grow with n  [{mode}]",
          not grew and len(mem) == len(sizes) * 3 and mode == "isolated",
          f"{min(vals)}..{max(vals)} B across n={sizes[0]}..{sizes[-1]}")
    t0 = time.perf_counter()
    _, mode = measure_memory_isolated("shell (n/2)", sizes, CASES, SEED,
                                      should_stop=lambda: True)
    check("cancellation honoured", mode == "stopped",
          f"returned in {time.perf_counter()-t0:.2f}s")

    print("\n" + "=" * 66)
    print(f"  shell sort:  {STATE['pass']} passed, {STATE['fail']} failed")
    print("=" * 66)
    sys.exit(1 if STATE["fail"] else 0)


if __name__ == "__main__":
    run()
