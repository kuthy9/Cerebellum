from cerebellum.evals import eval_home
from cerebellum.evals.prune import prune_eval_homes


def make_home(store, settings, eval_run_id, suite, *, running=False):
    store.create_eval_run(
        eval_run_id,
        suite=suite,
        suite_path="/x/evals.yaml",
        workflow_name="wf",
        workflow_digest="d1",
        mock=True,
        total=1,
        baseline_id=None,
    )
    if not running:
        store.finish_eval_run(eval_run_id, status="completed")
    home = eval_home(settings, eval_run_id)
    home.mkdir(parents=True)
    (home / "sandbox_orders_db.db").write_bytes(b"sqlite")
    return home


def test_prune_keeps_the_newest_sandboxes_of_each_suite(store, settings, clock):
    """Review finding: .cerebellum/evals/<id>/ grew by one directory per eval, forever."""
    make_home(store, settings, "ev_00000000", "a", running=True)  # its process was killed
    clock.advance(100)
    homes = {}
    for eval_run_id, suite in (
        ("ev_00000001", "a"),
        ("ev_00000002", "a"),
        ("ev_00000003", "b"),
        ("ev_00000004", "a"),
    ):
        clock.advance(1)
        homes[eval_run_id] = make_home(store, settings, eval_run_id, suite)
    clock.advance(1)
    live = make_home(store, settings, "ev_00000005", "a", running=True)

    pruned = prune_eval_homes(store, settings, keep=2)
    assert [(p.eval_run_id, p.suite) for p in pruned] == [
        ("ev_00000002", "a"),
        ("ev_00000001", "a"),
        ("ev_00000000", "a"),
    ]
    assert all(p.path == eval_home(settings, p.eval_run_id) for p in pruned)
    assert not any(p.path.exists() for p in pruned)
    assert homes["ev_00000004"].is_dir() and homes["ev_00000003"].is_dir() and live.is_dir()
    assert len(store.list_eval_runs()) == 6  # the history stays in the database

    assert prune_eval_homes(store, settings, keep=2) == []
    pruned = prune_eval_homes(store, settings, keep=0)
    assert [p.eval_run_id for p in pruned] == ["ev_00000004", "ev_00000003"]
    assert live.is_dir()  # a live eval is using its sandbox
