import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "start_batch_evaluation",
    _ROOT / "deploy" / "start_batch_evaluation.py",
)
_mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_mod)


def test_batch_request_targets_runtime_log_group() -> None:
    body = _mod.batch_request(lookback_days=3, name="cre_batch_unit")
    assert body["batchEvaluationName"] == "cre_batch_unit"
    ids = [e["evaluatorId"] for e in body["evaluators"]]
    assert ids == list(_mod.DEFAULT_EVALUATORS)
    cw = body["dataSourceConfig"]["cloudWatchLogs"]
    assert cw["serviceNames"] == [_mod.SERVICE]
    assert cw["logGroupNames"] == [_mod.LOG_GROUP]
    tr = cw["filterConfig"]["timeRange"]
    assert "startTime" in tr and "endTime" in tr
