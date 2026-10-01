import socket

import pytest

from cerebellum.config import Settings
from cerebellum.runtime.clock import FakeClock
from cerebellum.runtime.store import Store
from cerebellum.spec import parse_workflow

SIMPLE_YAML = """
name: simple
input:
  order_id: {type: string}
steps:
  - id: first
    type: validate
    rules:
      - {expr: "true", message: never fails}
  - id: second
    type: task
    needs: [first]
    title: "Follow up {{ input.order_id }}"
fallbacks:
  - id: rescue
    type: task
    title: rescue
"""


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def settings(tmp_path):
    return Settings.from_env({"CEREBELLUM_HOME": str(tmp_path / "home")})


@pytest.fixture
def store(settings, clock):
    with Store(settings.db_path, clock=clock) as s:
        yield s


@pytest.fixture
def simple_workflow(tmp_path):
    return parse_workflow(SIMPLE_YAML, base_dir=tmp_path, env={})


@pytest.fixture
def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
