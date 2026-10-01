import os
import subprocess
import sys
from pathlib import Path

import pytest

from cerebellum.evals.lock import EvalBusy, EvalLock

SRC = Path(__file__).resolve().parents[2] / "src"
HOLDER = """
import sys
from pathlib import Path
from cerebellum.evals.lock import EvalLock

EvalLock(Path(sys.argv[1])).acquire()
print("locked", flush=True)
sys.stdin.read()  # hold the lock until the parent kills this process
"""


def test_a_second_eval_in_the_same_home_is_refused(tmp_path):
    """Review finding: two evals in one home shared a sandbox and interleaved its fail modes."""
    home = tmp_path / "home"
    with EvalLock(home):
        with pytest.raises(EvalBusy, match="another `cerebellum eval` is running in"):
            EvalLock(home).acquire()
        with EvalLock(tmp_path / "other"):  # another home is independent
            pass
    with EvalLock(home):  # released on exit
        pass


def test_a_lock_left_by_a_killed_process_does_not_block(tmp_path):
    home = tmp_path / "home"
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(home)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC)},
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(EvalBusy):
            EvalLock(home).acquire()
    finally:
        holder.kill()  # no clean-up runs: the lock file stays behind
        holder.wait(timeout=10)
        holder.stdin.close()
        holder.stdout.close()
    with EvalLock(home):
        pass
