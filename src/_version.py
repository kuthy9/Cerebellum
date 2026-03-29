"""
Cerebellum version information.
Single source of truth — imported by cerebellum.py and setup tools.
"""

__version__      = "0.1.0"
__version_info__ = (0, 1, 0)

# Semantic versioning: MAJOR.MINOR.PATCH
#   MAJOR — breaking API change
#   MINOR — new feature, backwards-compatible
#   PATCH — bug fix, backwards-compatible
#
# History
# -------
# 0.1.0  2026-03-28  Initial release
#          · DAG execution engine with parallel scheduling
#          · Node state machine (8 states, guarded transitions)
#          · SQLite checkpoint store + rollback manager
#          · Typed message bus (pub/sub + request/reply)
#          · Integrated Cerebellum runtime
#          · Budget guard (hard USD cap per run)
#          · Scenarios: research pipeline, trading signals, crash+resume

__author__       = "Cerebellum Contributors"
__license__      = "MIT"
__url__          = "https://github.com/your-org/cerebellum"
__description__  = (
    "Deterministic multi-agent orchestration layer. "
    "LLM handles content. Cerebellum handles coordination."
)