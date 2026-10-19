import json
import sys
from types import ModuleType

from cre_rentroll.agents import (
    BOOK,
    INVENTORIES,
    _emit_react_turn,
    _short_tool_args,
    draft_from_strands,
    run_openai_tools,
    run_strands,
)
from cre_rentroll.harness import run_task
from cre_rentroll.run_events import bind, reset
from cre_rentroll.schemas import CanonicalRentRoll, EvalScores, RollAbstract, UnitRow
from cre_rentroll.tools import create_plan, reflection, score_gcr
from cre_rentroll.turn_ctx import eval_scores, llm_call_count, llm_usage, record, reset_turn, set_checklist, traces


def _roll_abstract() -> RollAbstract:
    return RollAbstract(
        property_name="Hill",
        unit_count=3,
        occupied_count=2,
        vacant_count=1,
        occupancy_pct=2 / 3,
        in_place_monthly=3100.0,
        mtm_count=0,
        as_of="2021-04-20",
        citations=["compute_income"],
    )


def test_short_tool_args_keeps_full_create_plan_json():
    tasks = (
        "1. Query unit count and vacant count. 2. Compute occupancy percent and "
        "in-place income from the vault. 3. Draft the abstract with citations."
    )
    note = _short_tool_args({"tasks": tasks})
    assert "…" not in note
    assert tasks in note
    assert '"tasks"' in note
    grid = _short_tool_args(
        {"tasks": tasks, "units": [{"unit_id": "1", "tenant": "Secret LLC"}]}
    )
    assert "Secret LLC" not in grid
    assert "unit_id" not in grid
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        _emit_react_turn(1, ["create_plan"], note, "tool_use")
    finally:
        reset(token)
    msg = seen[0]["message"]
    assert "ReAct turn 1" in msg
    assert tasks in msg
    assert "…" not in msg
    long_draft = (
        '{"occupancy_pct": 0.9207, "in_place_monthly": 80019, '
        '"as_of": "April 20, 2021", "notes": "' + ("x" * 120) + '"}'
    )
    draft_note = _short_tool_args({"draft": long_draft})
    assert "…" not in draft_note
    assert "80019" in draft_note
    assert "April 20, 2021" in draft_note
    assert ("x" * 120) in draft_note


def test_emit_react_turn_suffix_matches_checklist_task():
    reset_turn("tester")
    create_plan(
        "1. [completed] query_units\n"
        "2. [pending] Compute income for in-place monthly rent."
    )
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        _emit_react_turn(4, ["compute_income"], "", "tool_use")
    finally:
        reset(token)
    msg = seen[0]["message"]
    assert msg.startswith("ReAct turn 4 — compute_income (MCP)")
    assert " — for 2. [pending] Compute income for in-place monthly rent." in msg
    assert "stop=tool_use" in msg


def test_emit_react_turn_suffix_prefers_in_progress_then_pending():
    reset_turn("tester")
    set_checklist(
        [
            {"text": "query_units", "status": "in_progress"},
            {"text": "Compute income for in-place monthly rent.", "status": "pending"},
        ]
    )
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        _emit_react_turn(2, ["query_units"])
        _emit_react_turn(3, ["compute_income"])
    finally:
        reset(token)
    assert " — for 1. [in_progress] query_units" in seen[0]["message"]
    assert " — for 2. [pending] Compute income for in-place monthly rent." in seen[1]["message"]


def test_emit_react_turn_suffix_omits_meta_and_unmatched():
    reset_turn("tester")
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        _emit_react_turn(1, ["create_plan"])
        set_checklist(
            [
                {"text": "query_units", "status": "pending"},
                {"text": "Compute income for in-place monthly rent.", "status": "pending"},
            ]
        )
        _emit_react_turn(2, ["score_gcr"])
        _emit_react_turn(3, ["reflection"])
        _emit_react_turn(4, ["create_plan"])
        _emit_react_turn(5, ["compute_noi"])
        _emit_react_turn(6, [])
    finally:
        reset(token)
    assert "for " not in seen[0]["message"]
    assert "for " not in seen[1]["message"]
    assert "for " not in seen[2]["message"]
    assert "for " not in seen[3]["message"]
    assert "for (no matching task)" in seen[4]["message"]
    assert "for " not in seen[5]["message"]
    assert "Invented" not in " ".join(r["message"] for r in seen)


def test_draft_from_strands_ignores_structured_output():
    so = _roll_abstract()
    class EmptyStr:
        structured_output = so
        message = {"role": "assistant", "content": "Hill has 3 units."}
        stop_reason = "end_turn"

        def __str__(self) -> str:
            return ""

    draft = draft_from_strands(EmptyStr())
    assert draft == "Hill has 3 units."
    assert "unit_count" not in draft
    assert str(EmptyStr()) == ""


def test_draft_from_strands_openai_string_content():
    class EmptyStr:
        structured_output = None
        message = {"role": "assistant", "content": "Occupancy is 92.07% with in-place monthly $1,500."}
        stop_reason = "end_turn"

        def __str__(self) -> str:
            return ""

    seen: list[dict] = []
    token = bind(seen.append)
    try:
        draft = draft_from_strands(EmptyStr())
    finally:
        reset(token)
    assert "92.07%" in draft
    assert "$1,500" in draft
    assert not any("structured_output missing" in (row.get("message") or "") for row in seen)
    assert not any(row["step"] == "reflect" for row in seen)


def test_draft_from_strands_uses_result_str_when_message_empty():
    class ResultStr:
        structured_output = _roll_abstract()
        message = {"role": "assistant", "content": []}
        stop_reason = "end_turn"

        def __str__(self) -> str:
            return "Occupancy is 92.07% with in-place monthly 1500."

    draft = draft_from_strands(ResultStr())
    assert draft == "Occupancy is 92.07% with in-place monthly 1500."
    assert "unit_count" not in draft


def test_draft_from_strands_empty_result_is_empty_string():
    class EmptyStr:
        structured_output = None
        message = {"role": "assistant", "content": []}
        stop_reason = "limit_turns"

        def __str__(self) -> str:
            return ""

    seen: list[dict] = []
    token = bind(seen.append)
    try:
        assert draft_from_strands(EmptyStr()) == ""
        assert draft_from_strands(None) == ""
    finally:
        reset(token)
    assert not any(row["step"] == "reflect" for row in seen)
    assert any(row["step"] == "error" for row in seen)
    assert any("stop_reason=limit_turns" in (row.get("message") or "") for row in seen)


def test_draft_from_strands_limit_turns_ignores_structured_output():
    so = _roll_abstract()

    class Limited:
        structured_output = so
        message = {"role": "assistant", "content": []}
        stop_reason = "limit_turns"

        def __str__(self) -> str:
            return ""

    seen: list[dict] = []
    token = bind(seen.append)
    try:
        draft = draft_from_strands(Limited())
    finally:
        reset(token)
    assert draft == ""
    assert "unit_count" not in draft
    assert not any(row["step"] == "reflect" for row in seen)
    assert any("no assistant message text" in (row.get("message") or "") for row in seen)


def test_draft_from_strands_limit_turns_uses_message_json():
    payload = (
        '{"property_name":"Hill","unit_count":3,"occupied_count":2,'
        '"vacant_count":1,"occupancy_pct":0.6667,"in_place_monthly":3100.0}'
    )

    class Limited:
        structured_output = None
        message = {"role": "assistant", "content": []}
        stop_reason = "limit_turns"

        def __str__(self) -> str:
            return ""

    messages = [{"role": "assistant", "content": [{"text": payload}]}]
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        draft = draft_from_strands(Limited(), messages=messages)
    finally:
        reset(token)
    assert "unit_count" in draft
    assert "3100" in draft
    assert not any(row["step"] == "reflect" for row in seen)
    assert not any("structured_output missing" in (row.get("message") or "") for row in seen)


def _install_fake_strands(monkeypatch, agent_cls, model_cls=None):
    class FakeModel:
        def __init__(self, **_kw):
            pass

    def _fake_tool(*args, **kwargs):
        def wrap(fn):
            return fn
        if args and callable(args[0]) and not kwargs:
            return args[0]
        return wrap

    strands_mod = ModuleType("strands")
    strands_mod.Agent = agent_cls  # type: ignore[attr-defined]
    strands_mod.tool = _fake_tool  # type: ignore[attr-defined]
    models_mod = ModuleType("strands.models")
    openai_mod = ModuleType("strands.models.openai")
    openai_mod.OpenAIModel = model_cls or FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "strands", strands_mod)
    monkeypatch.setitem(sys.modules, "strands.models", models_mod)
    monkeypatch.setitem(sys.modules, "strands.models.openai", openai_mod)
    monkeypatch.setattr("cre_rentroll.agents._strands_tools", lambda _names: [])
    monkeypatch.setattr("cre_rentroll.agents.openai_client_args", lambda: {})


class _EmptyReact:
    structured_output = None
    message = {"role": "assistant", "content": "tools done"}
    stop_reason = "end_turn"

    def __str__(self) -> str:
        return ""


class _ProseReact:
    structured_output = None
    message = {"role": "assistant", "content": "Occupancy is 92.07% with in-place monthly 3100."}
    stop_reason = "end_turn"

    def __str__(self) -> str:
        return ""


def test_run_strands_single_react_invoke_no_schema(monkeypatch):
    calls: list[dict] = []

    class FakeAgent:
        def __init__(self, **_kw):
            self.messages = [{"role": "assistant", "content": "Occupancy is 92.07% with in-place monthly 3100."}]

        def __call__(self, *_a, **kwargs):
            calls.append(dict(kwargs))
            return _ProseReact()

    _install_fake_strands(monkeypatch, FakeAgent)
    reset_turn("tester")
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        draft = run_strands("Abstract the roll", BOOK, "")
    finally:
        reset(token)
    assert "92.07%" in draft
    assert "3100" in draft
    assert llm_call_count() == 1
    assert len(calls) == 1
    assert "structured_output_model" not in calls[0]
    assert calls[0].get("limits") == {"turns": 12}
    assert not any("structured output" in (row.get("message") or "") for row in seen)
    assert not any(row["step"] == "reflect" for row in seen)


def test_run_strands_does_not_invoke_schema_after_tools(monkeypatch):
    calls: list[dict] = []

    class FakeAgent:
        def __init__(self, **_kw):
            self.messages = []

        def __call__(self, *_a, **kwargs):
            calls.append(dict(kwargs))
            create_plan("1. [in_progress] compute_income")
            record("compute_income", {}, {"in_place_monthly": 3100.0})
            return _ProseReact()

    _install_fake_strands(monkeypatch, FakeAgent)
    reset_turn("tester")
    draft = run_strands("Abstract the roll", BOOK, "")
    assert len(calls) == 1
    assert "structured_output_model" not in calls[0]
    assert calls[0].get("limits") == {"turns": 12}
    assert "92.07%" in draft
    assert "3100" in draft


def test_run_strands_copy_context_records_from_worker(monkeypatch):
    class FakeAgent:
        def __init__(self, **_kw):
            self.messages = []

        def __call__(self, *_a, **kwargs):
            create_plan("1. [completed] query_units")
            return _EmptyReact()

    _install_fake_strands(monkeypatch, FakeAgent)
    reset_turn("tester")
    run_strands("Abstract the roll", BOOK, "")
    names = [row["name"] for row in traces()]
    assert "create_plan" in names


def test_run_strands_score_gcr_no_schema_invoke(monkeypatch):
    calls: list[dict] = []
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    from cre_rentroll.turn_ctx import set_eval_query

    class FakeAgent:
        def __init__(self, **_kw):
            self.messages = [{"role": "assistant", "content": "Occupancy is 92.07%."}]

        def __call__(self, *_a, **kwargs):
            calls.append(dict(kwargs))
            score_gcr("Occupancy is 92.07% with in-place monthly 1500.")
            return _ProseReact()

    _install_fake_strands(monkeypatch, FakeAgent)
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: roll)
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(R=5, G=4, request_C=5, vault_calc_ok=True, judge="llm"),
    )
    reset_turn("tester")
    set_eval_query("What is occupancy?")
    draft = run_strands("Abstract the roll", BOOK, "")
    assert len(calls) == 1
    assert all("structured_output_model" not in c for c in calls)
    assert "score_gcr" in [row["name"] for row in traces()]
    stored = eval_scores()
    assert stored is not None
    assert stored.G == 4 and stored.request_C == 5
    assert "92.07%" in draft


def _react_msgs(seen):
    return [row for row in seen if row.get("step") == "react"]


def test_run_strands_callback_emits_react_turns(monkeypatch):
    calls: list[dict] = []

    class FakeAgent:
        def __init__(self, callback_handler=None, **_kw):
            self._cb = callback_handler
            self.messages = []

        def __call__(self, *_a, **kwargs):
            calls.append(dict(kwargs))
            cb = self._cb
            if cb:
                cb(data="Hi")
                cb(start_event_loop=True)
                cb(
                    message={
                        "role": "assistant",
                        "content": [
                            {
                                "toolUse": {
                                    "name": "create_plan",
                                    "input": {"tasks": "1. [in_progress] query_units"},
                                }
                            }
                        ],
                    },
                    stop_reason="tool_use",
                )
                cb(start_event_loop=True)
                cb(current_tool_use={"name": "query_units", "input": {"vacant_only": True}})
                cb(
                    message={
                        "role": "assistant",
                        "content": [
                            {
                                "toolUse": {
                                    "name": "query_units",
                                    "input": {
                                        "vacant_only": True,
                                        "units": [{"tenant": "Secret LLC", "unit_id": "1"}] * 40,
                                    },
                                }
                            }
                        ],
                    },
                    stop_reason="tool_use",
                )
                cb(start_event_loop=True)
                cb(
                    message={
                        "role": "assistant",
                        "content": [
                            {
                                "toolUse": {
                                    "name": "score_gcr",
                                    "input": {"draft": "Occupancy is 92%."},
                                }
                            }
                        ],
                    },
                    stop_reason="tool_use",
                )
                cb(result=_EmptyReact())
            return _EmptyReact()

    _install_fake_strands(monkeypatch, FakeAgent)
    reset_turn("tester")
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        run_strands("Abstract the roll", BOOK, "")
    finally:
        reset(token)
    react = _react_msgs(seen)
    assert len(react) == 3
    assert "ReAct turn 1" in react[0]["message"] and "create_plan" in react[0]["message"]
    assert "stop=tool_use" in react[0]["message"]
    assert "ReAct turn 2" in react[1]["message"] and "query_units" in react[1]["message"]
    assert "ReAct turn 3" in react[2]["message"] and "score_gcr" in react[2]["message"]
    assert "stop=tool_use" in react[2]["message"]
    blob = " ".join(row["message"] for row in react)
    assert "Secret LLC" not in blob
    assert "unit_id" not in blob
    assert not any("structured output" in (row.get("message") or "") for row in seen)
    assert len(calls) == 1
    assert all("structured_output_model" not in c for c in calls)
    assert llm_call_count() == 3


def test_run_strands_wrapped_create_emits_react_turns(monkeypatch):
    calls: list[dict] = []

    class Fn:
        def __init__(self, name, arguments="{}"):
            self.name = name
            self.arguments = arguments

    class TC:
        def __init__(self, name, arguments="{}"):
            self.function = Fn(name, arguments)

    class Msg:
        def __init__(self, content=None, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls

    class Choice:
        def __init__(self, msg, finish_reason):
            self.message = msg
            self.finish_reason = finish_reason

    class Resp:
        def __init__(self, msg, finish_reason):
            self.choices = [Choice(msg, finish_reason)]
            self.usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 2})()

    class Completions:
        def __init__(self):
            self.n = 0

        def create(self, **_kwargs):
            self.n += 1
            if self.n == 1:
                return Resp(
                    Msg(tool_calls=[TC("create_plan", '{"tasks":"1. [in_progress] query_units"}')]),
                    "tool_calls",
                )
            if self.n == 2:
                return Resp(Msg(tool_calls=[TC("query_units", '{"vacant_only":true}')]), "tool_calls")
            return Resp(Msg(tool_calls=[TC("score_gcr", '{"draft":"Occupancy is 92%."}')]), "tool_calls")

    completions = Completions()

    class FakeModel:
        def __init__(self, **_kw):
            self.client = type("C", (), {"chat": type("H", (), {"completions": completions})()})()

    class FakeAgent:
        def __init__(self, model=None, **_kw):
            self.model = model
            self.messages = []

        def __call__(self, *_a, **kwargs):
            calls.append(dict(kwargs))
            create = self.model.client.chat.completions.create
            create(model="gpt-4o-mini", messages=[])
            create(model="gpt-4o-mini", messages=[])
            create(model="gpt-4o-mini", messages=[])
            return _EmptyReact()

    _install_fake_strands(monkeypatch, FakeAgent, model_cls=FakeModel)
    reset_turn("tester")
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        run_strands("Abstract the roll", BOOK, "")
    finally:
        reset(token)
    react = _react_msgs(seen)
    assert len(react) == 3
    assert "create_plan" in react[0]["message"]
    assert "query_units" in react[1]["message"]
    assert "score_gcr" in react[2]["message"]
    assert not any("structured output" in (row.get("message") or "") for row in seen)
    assert len(calls) == 1
    assert all("structured_output_model" not in c for c in calls)
    assert llm_call_count() == 3
    assert llm_usage() == [(10, 2), (10, 2), (10, 2)]
    assert completions.n == 3


def test_run_openai_tools_no_json_mode_after_tool_loop(monkeypatch):
    class Fn:
        def __init__(self, name, arguments="{}"):
            self.name = name
            self.arguments = arguments

    class TC:
        def __init__(self, name="score_gcr", arguments='{"draft":"Occupancy is 92.07%."}'):
            self.id = "1"
            self.function = Fn(name, arguments)

        def model_dump(self):
            return {
                "id": self.id,
                "type": "function",
                "function": {"name": self.function.name, "arguments": self.function.arguments},
            }

    class Msg:
        def __init__(self, content=None, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls

    class Resp:
        def __init__(self, msg, finish_reason="stop"):
            self.choices = [type("C", (), {"message": msg, "finish_reason": finish_reason})()]

    class Completions:
        def __init__(self):
            self.calls: list[dict] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return Resp(Msg(content="", tool_calls=[TC()]), "tool_calls")
            return Resp(Msg(content="Occupancy is 92.07%."), "stop")

    completions = Completions()

    class FakeOpenAI:
        def __init__(self, **_kw):
            self.chat = type("Chat", (), {"completions": completions})()

    openai_mod = ModuleType("openai")
    openai_mod.OpenAI = FakeOpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", openai_mod)
    monkeypatch.setattr("cre_rentroll.agents.openai_client_args", lambda: {})
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(R=5, G=4, request_C=5, vault_calc_ok=True, judge="llm"),
    )
    reset_turn("tester")
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        draft = run_openai_tools("Abstract the roll", BOOK, "")
    finally:
        reset(token)
    assert "92.07%" in draft
    assert not any(c.get("response_format") for c in completions.calls)
    assert not any("structured output" in (row.get("message") or "") for row in seen)
    assert "score_gcr" in [row["name"] for row in traces()]
    react = [row for row in seen if row.get("step") == "react"]
    assert len(react) == 2
    assert "score_gcr" in react[0]["message"]
    assert "text-only" in react[1]["message"]


def test_reflection_only_draft_still_runs_score_gcr(monkeypatch):
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    seen: list[dict] = []
    token = bind(seen.append)
    judged = {"n": 0}

    def fake_judge(*_a, **_k):
        judged["n"] += 1
        return EvalScores(R=4, G=4, request_C=4, vault_calc_ok=True, judge="llm")

    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")

    def fake_agent(*_a, **_k):
        reflection("self-check ok")
        return "Reflection stored: self-check ok"

    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: roll)
    monkeypatch.setattr("cre_rentroll.eval_service.live_judge", fake_judge)
    try:
        outcome = run_task("tester", "What is occupancy and in-place income?", skip_scope=True)
    finally:
        reset(token)
    reflect_msgs = [row["message"] for row in seen if row["step"] == "reflect"]
    assert any("self-check ok" in m for m in reflect_msgs)
    assert judged["n"] == 1
    assert "score_gcr" in [row["name"] for row in traces()]
    assert any(
        "required last tool after text-only stop" in (row.get("message") or "")
        for row in seen
    )
    eval_msgs = [row["message"] for row in seen if row["step"] == "eval"]
    assert outcome.eval is not None
    assert outcome.eval.judge == "llm"
    assert outcome.eval.G == 4
    assert outcome.stop_reason != "eval_error" or outcome.eval.judge != "not_run"
    assert not any("judge=not_run" in m for m in eval_msgs)
    assert "Reflection stored: self-check ok" not in (outcome.text or "")
    assert "could not finish that answer this turn" in (outcome.text or "").lower()


def test_empty_run_agent_goes_to_eval_not_vault_inject(monkeypatch):
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    seen: list[dict] = []
    token = bind(seen.append)
    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    n = {"n": 0}

    def fake_agent(*_a, **_k):
        n["n"] += 1
        return ""

    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    try:
        outcome = run_task("tester", "What is occupancy and in-place income?", skip_scope=True)
    finally:
        reset(token)
    assert n["n"] == 1
    drafts = [row["message"] for row in seen if row["step"] == "draft"]
    assert any("agent draft is empty" in m for m in drafts)
    assert not any("second agent draft" in m for m in drafts)
    assert not any("second ReAct pass" in (row.get("message") or "") for row in seen)
    assert not any(m.startswith("Harness vault fill:") for m in drafts)
    eval_msgs = [row["message"] for row in seen if row["step"] == "eval"]
    assert any("judge=not_run" in m and "→ error" in m for m in eval_msgs)
    assert not any("G=5" in m and "judge=llm" in m for m in eval_msgs)
    assert any("Draft before eval is empty" in m for m in drafts)
    assert outcome.kind == "error"
    assert outcome.stop_reason == "eval_error"
    assert outcome.eval is not None
    assert outcome.eval.judge == "not_run"
    assert outcome.eval.G is None
    assert outcome.eval.vault_calc_ok is None
    assert any("G=N/A C=N/A R=N/A vault_calc=N/A" in m for m in eval_msgs)
    assert not any("G=1" in m for m in eval_msgs)


def test_score_gcr_stores_scores(monkeypatch):
    from cre_rentroll.tools import score_gcr
    from cre_rentroll.turn_ctx import set_eval_query

    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    reset_turn("tester")
    set_eval_query("What is occupancy?")
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: roll)
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(R=5, G=4, request_C=5, vault_calc_ok=True, judge="llm"),
    )
    out = json.loads(score_gcr("Occupancy is 92.07% with in-place monthly 1500."))
    stored = eval_scores()
    assert stored is not None
    assert stored.G == 4 and stored.request_C == 5 and stored.R == 5
    assert stored.vault_calc_ok is True
    assert out["decision"] == "ok"
    assert out["G"] == 4
    names = [row["name"] for row in traces()]
    assert "score_gcr" in names


def test_harness_uses_score_gcr_and_skips_live_judge(monkeypatch):
    from cre_rentroll.turn_ctx import set_eval_scores

    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    seen: list[dict] = []
    token = bind(seen.append)
    judged = {"n": 0}

    def boom(*_a, **_k):
        judged["n"] += 1
        raise AssertionError("harness must not call live_judge")

    def fake_agent(*_a, **_k):
        set_eval_scores(EvalScores(R=5, G=5, request_C=5, vault_calc_ok=True, judge="llm"))
        return "Occupancy is 92.07% with in-place monthly 1500."

    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.eval_service.live_judge", boom)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    try:
        outcome = run_task("tester", "What is occupancy and in-place income?", skip_scope=True)
    finally:
        reset(token)
    assert judged["n"] == 0
    assert outcome.eval is not None
    assert outcome.eval.G == 5
    assert outcome.eval.judge == "llm"
    assert outcome.kind == "answer"
    eval_msgs = [row["message"] for row in seen if row["step"] == "eval"]
    assert any("G=5" in m and "judge=llm" in m for m in eval_msgs)


def test_text_only_stop_invokes_required_score_gcr(monkeypatch):
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    judged = {"n": 0}

    def fake_judge(*_a, **_k):
        judged["n"] += 1
        return EvalScores(R=5, G=5, request_C=5, vault_calc_ok=True, judge="llm")

    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr(
        "cre_rentroll.harness.run_agent",
        lambda *a, **k: "Occupancy is 92.07% with in-place monthly 1500.",
    )
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.eval_service.live_judge", fake_judge)
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: roll)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        outcome = run_task("tester", "What is occupancy?", skip_scope=True)
    finally:
        reset(token)
    assert judged["n"] == 1
    assert "92.07" in (outcome.text or "")
    assert outcome.eval is not None
    assert outcome.eval.judge == "llm"
    assert outcome.eval.G == 5 and outcome.eval.R == 5 and outcome.eval.request_C == 5
    assert outcome.eval.vault_calc_ok is True
    assert outcome.stop_reason != "eval_error"
    assert outcome.kind == "answer"
    assert "score_gcr" in [row["name"] for row in traces()]
    assert "score_gcr" in (outcome.tools_called or [])
    assert any(
        "required last tool after text-only stop" in (row.get("message") or "")
        and "not a silent Python judge" in (row.get("message") or "")
        for row in seen
    )
    eval_msgs = [row["message"] for row in seen if row["step"] == "eval"]
    assert any("G=5" in m and "judge=llm" in m for m in eval_msgs)
    assert not any("judge=not_run" in m for m in eval_msgs)


def test_low_scores_do_not_python_retry_run_task(monkeypatch):
    from cre_rentroll.turn_ctx import set_eval_scores

    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    n = {"n": 0}

    def fake_agent(*_a, **_k):
        n["n"] += 1
        set_eval_scores(EvalScores(R=5, G=1, request_C=1, vault_calc_ok=True, judge="llm"))
        return "Occupancy is 92.07% with in-place monthly 1500."

    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    outcome = run_task("tester", "What is occupancy?", skip_scope=True)
    assert n["n"] == 1
    assert outcome.stop_reason == "eval_error"
    assert outcome.kind in {"error", "warning"}


def test_unit_count_question_goes_through_react(monkeypatch):
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    seen: list[dict] = []
    token = bind(seen.append)
    judged = {"n": 0}
    reacted = {"n": 0}

    def fake_judge(*_a, **_k):
        judged["n"] += 1
        return EvalScores(R=4, G=4, request_C=5, vault_calc_ok=True, judge="llm")

    def fake_agent(*_a, **_k):
        from cre_rentroll.tools import query_units, score_gcr

        reacted["n"] += 1
        query_units()
        text = "Hill has 1 units."
        score_gcr(text)
        return text

    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: roll)
    monkeypatch.setattr("cre_rentroll.eval_service.live_judge", fake_judge)
    try:
        outcome = run_task("tester", "How many units?", skip_scope=True)
    finally:
        reset(token)
    assert reacted["n"] == 1
    assert judged["n"] == 1
    assert outcome.text == "Hill has 1 units."
    assert outcome.eval is not None
    assert outcome.eval.judge == "llm"
    assert outcome.eval.G == 4
    assert "score_gcr" in [row["name"] for row in traces()]
    eval_msgs = [row["message"] for row in seen if row["step"] == "eval"]
    assert any("G=4" in m and "judge=llm" in m for m in eval_msgs)
    assert not any("judge=vault" in m for m in eval_msgs)
    assert not any("G=5" in m for m in eval_msgs)
