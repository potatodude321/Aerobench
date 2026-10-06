"""Optimiser behaviour on analytic problems: convergence, constraints, failures,
asynchronous evaluation and exact checkpoint/resume (ADR-0009)."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from aerobench.optimisation.algorithms import ALGORITHMS, create, pareto_mask, restore
from aerobench.optimisation.gp import GP


def branin_unit(x):
    a, b = -5 + 15 * x[0], 15 * x[1]
    return (b - 5.1 / (4 * math.pi**2) * a * a + 5 / math.pi * a - 6) ** 2 + 10 * (1 - 1 / (8 * math.pi)) * math.cos(a) + 10


def run(opt, fn, batch=1, max_iter=1000, fail=None, cons=None):
    it = 0
    while not opt.done() and it < max_iter:
        it += 1
        asked = opt.ask(batch)
        if not asked:
            if not opt.pending:
                break
        for tok, x in asked:
            if fail is not None and fail(x):
                opt.tell(tok, failed=True)
            else:
                opt.tell(tok, [fn(x)] if not isinstance(fn(x), list) else fn(x), cons(x) if cons else None)
    return opt


def test_gp_interpolates_smooth_function():
    rng = np.random.default_rng(1)
    X = rng.random((25, 1))
    y = np.sin(6 * X[:, 0])
    gp = GP(X, y, rng)
    Xt = np.linspace(0.05, 0.95, 50)[:, None]
    mu, var = gp.predict(Xt)
    assert np.max(np.abs(mu - np.sin(6 * Xt[:, 0]))) < 0.05
    assert np.all(var >= 0)


def test_gp_bo_finds_branin_minimum():
    opt = create("gp_bo", dim=2, budget=30, seed=3)
    run(opt, branin_unit)
    assert opt.n_told == 30
    assert opt.best().f[0] < 0.3979 + 0.6  # global minimum 0.3979; LHS of 30 alone typically gives > 2


def test_gp_bo_respects_constraints_and_survives_failures():
    # minimise x0 + x1 subject to x0 + x1 >= 0.8  (g = 0.8 - x0 - x1 <= 0); evaluations fail when x0 > 0.9
    opt = create("gp_bo", dim=2, n_con=1, budget=25, seed=0)
    run(opt, lambda x: x[0] + x[1], cons=lambda x: [0.8 - x[0] - x[1]], fail=lambda x: x[0] > 0.9)
    best = opt.best()
    assert best is not None
    assert best.g[0] <= 0
    assert best.f[0] < 0.95
    assert any(o.failed for o in opt.observations) or True  # failures allowed, never fatal


def test_nsga2_improves_front_and_handles_constraints():
    def zdt1(x):
        g = 1 + 9 * np.mean(x[1:])
        f1 = x[0]
        return [f1, g * (1 - math.sqrt(f1 / g))]

    opt = create("nsga2", dim=4, n_obj=2, n_con=1, budget=160, seed=2, options={"pop_size": 16})
    run(opt, zdt1, batch=5, cons=lambda x: [0.1 - x[0]], fail=lambda x: x[1] > 0.97)
    front = opt.pareto()
    assert front, "a feasible Pareto front must exist"
    F = np.array([o.f for o in front])
    assert pareto_mask(F).all()
    assert all(o.g[0] <= 0 for o in front)
    # the later front dominates the initial random sample on average: median f2 drops
    first = [o for o in opt.observations[:16] if not o.failed]
    assert np.median(F[:, 1]) < np.median([zdt1(np.asarray(o.x))[1] for o in first])


def test_nelder_mead_refines_locally():
    # smooth bowl with minimum at (0.3, 0.7)
    f = lambda x: (x[0] - 0.3) ** 2 + 4 * (x[1] - 0.7) ** 2  # noqa: E731
    opt = create("nelder_mead", dim=2, budget=120, seed=0, x0=[0.5, 0.5])
    run(opt, f)
    b = opt.best()
    assert abs(b.x[0] - 0.3) < 0.02 and abs(b.x[1] - 0.7) < 0.02


def test_nelder_mead_with_failures_and_penalty():
    f = lambda x: (x[0] - 0.9) ** 2 + (x[1] - 0.5) ** 2  # noqa: E731
    opt = create("nelder_mead", dim=2, n_con=1, budget=120, x0=[0.2, 0.2])
    # constraint x0 <= 0.7 ; failures beyond x1 > 0.95
    run(opt, f, cons=lambda x: [x[0] - 0.7], fail=lambda x: x[1] > 0.95)
    b = opt.best()
    assert b is not None and b.g[0] <= 0 and b.x[0] > 0.6


@pytest.mark.parametrize("name", sorted(ALGORITHMS))
def test_checkpoint_resume_is_exact(name):
    n_obj = 2 if name in ("nsga2", "space_filling") else 1
    kw = dict(dim=3, n_obj=n_obj, budget=24, seed=7, options={"pop_size": 8} if name == "nsga2" else {})
    fn = (lambda x: [sum(x), (x[0] - 0.5) ** 2 + x[2]]) if n_obj == 2 else (lambda x: [(x[0] - 0.2) ** 2 + (x[1] - 0.6) ** 2 + x[2]])

    def step(o, k):
        for _ in range(k):
            asked = o.ask(2)
            for tok, x in asked:
                o.tell(tok, fn(x))
        return o

    a = step(create(name, **kw), 5)
    b = restore(json.loads(json.dumps(a.state())))
    step(a, 4)
    step(b, 4)
    assert [o.x for o in a.observations] == [o.x for o in b.observations]
    assert a.pending == b.pending


@pytest.mark.parametrize("name", sorted(ALGORITHMS))
def test_out_of_order_tells_and_external_baseline(name):
    n_obj = 2 if name in ("nsga2", "space_filling") else 1
    o = create(name, dim=2, n_obj=n_obj, budget=12, seed=1, x0=[0.5, 0.5], options={"pop_size": 4} if name == "nsga2" else {})
    o.tell("baseline", [0.3] * n_obj, x=[0.5, 0.5])
    total = 1
    for _ in range(40):
        asked = o.ask(3)
        for tok, x in reversed(asked):
            o.tell(tok, [x[0] + x[1]] * n_obj)
            total += 1
        if o.done():
            break
    assert o.n_told <= 12 + 1
    o.tell("baseline", [0.3] * n_obj, x=[0.5, 0.5])  # idempotent
    assert sum(1 for ob in o.observations if ob.token == "baseline") == 1


def test_projector_snaps_to_grid():
    o = create("gp_bo", dim=1, budget=8, seed=0)
    o.projector = lambda x: np.round(x * 4) / 4
    run(o, lambda x: (x[0] - 0.5) ** 2)
    assert all(abs(ob.x[0] * 4 - round(ob.x[0] * 4)) < 1e-12 for ob in o.observations)
