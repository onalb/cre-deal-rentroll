from __future__ import annotations

import uuid
from typing import Any

from cre_rentroll import reflect, turn_ctx, vault
from cre_rentroll.agents import BOOK, Inventory, RateLimitedError, RATE_LIMIT_TEXT, is_rate_limited, run_agent
from cre_rentroll.settings import openai_api_key
from cre_rentroll.eval_service import format_eval_log, gate
from cre_rentroll.ingest import ack_text, as_of_from_message, ingest_bytes
from cre_rentroll.memory import facts_prompt, generate_from_roll, write_trace
from cre_rentroll.schemas import CanonicalRentRoll, ChatOutcome, EvalScores
from cre_rentroll.present import answer_prose, is_operator_leak, scope_fail_chat, user_visible
from cre_rentroll.scope import SEARCH_DOWN_TEXT
from cre_rentroll.run_events import emit, emit_stop_and_summary


def inventory_for(_message: str) -> list[Inventory]:
    """Book menu: one agent, all tools. Intent is create_plan, not this picker."""
    return [BOOK]


def inventories_from_intent(_intent: str) -> list[Inventory]:
    """One book agent — never used to pick a specialist."""
    return [BOOK]


def decompose(message: str, inventories: list[Inventory] | None = None) -> list[tuple[Inventory, str]]:
    """One book agent; create_plan / update_plan / update_plan_status inside Strands is the plan."""
    inv = (inventories or [BOOK])[0]
    return [(inv, message)]


def _invoke_react(inv: Inventory, prompt: str, facts: str) -> str:
    """One Strands run_agent. Empty drafts go to eval as-is."""
    from cre_rentroll.agentcore_platform import note_harness_react

    note_harness_react()
    emit("llm", "Harness starting ReAct (LLM)")
    before_agent = turn_ctx.llm_call_kinds().count("agent")
    draft = run_agent(prompt, inv, facts)
    turn_ctx.add_llm_if_unchanged("agent", before_agent)
    emit(
        "llm",
        f"Harness finished ReAct — {len(draft or '')} characters (LLM)",
    )
    emit("draft", _draft_log("agent draft", draft), llm=True)
    return draft


def compact(text: str, limit: int = 8000) -> str:
    """Compact tool-ish payloads before summarization. Never FIFO the system prompt."""
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n…[compacted]…\n" + text[-limit // 2 :]


SESSION_PROMPT_LIMIT = 8000
_SESSION_FOLLOW = (
    "Answer the last User message. Prior User/Assistant turns are this session's "
    "chat memory — use them like a normal conversation (follow-ups, pronouns, "
    "'the same as…'). Do not ignore the last message to re-dump an earlier "
    "occupancy or count answer. In-scope CRE: first tool is always create_plan "
    "(one-item list is fine; do not skip for a lone web_search)."
)


def session_prompt(message: str, history: list[dict[str, Any]] | None = None) -> str:
    """Stuff stored SESSION user+assistant turns into the ReAct user prompt.

    Dynamo already keeps messages; this does not add a new memory product.
    Newest prior turns are kept if the transcript would exceed the limit.
    The current user line is never dropped.
    """
    current = f"User: {(message or '').strip()}\n\n{_SESSION_FOLLOW}"
    prior: list[str] = []
    for row in history or []:
        role = str(row.get("role") or "").strip().lower()
        content = str(row.get("content") or "").strip()
        if not content or role not in {"user", "assistant"}:
            continue
        label = "User" if role == "user" else "Assistant"
        prior.append(f"{label}: {content}")
    budget = SESSION_PROMPT_LIMIT - len(current) - 48
    kept: list[str] = []
    for line in reversed(prior):
        extra = 2 if kept else 0
        if budget >= 0 and sum(len(x) for x in kept) + len(line) + extra > budget:
            break
        kept.append(line)
    kept.reverse()
    omitted = len(prior) - len(kept)
    prefix = f"[earlier session turns omitted: {omitted}]\n\n" if omitted else ""
    body = "\n\n".join(kept)
    if body:
        text = f"{prefix}{body}\n\n{current}"
    else:
        text = f"{prefix}{current}" if prefix else current
    while kept and len(text) > SESSION_PROMPT_LIMIT:
        kept.pop(0)
        omitted += 1
        prefix = f"[earlier session turns omitted: {omitted}]\n\n"
        body = "\n\n".join(kept)
        text = f"{prefix}{body}\n\n{current}" if body else f"{prefix}{current}"
    return text


def measure_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def ingest_attachment(username: str, filename: str, raw: bytes) -> ChatOutcome:
    roll = ingest_bytes(filename, raw)
    vault.save_roll(username, roll)
    generate_from_roll(username, roll)
    text = ack_text(roll)
    sess = vault.load_session(username)
    messages = list(sess.get("messages") or [])
    messages += [
        {"role": "user", "content": f"[attached {filename}]"},
        {"role": "assistant", "content": text, "kind": "ingest_ack"},
    ]
    vault.save_session(username, messages, pending_hitl=sess.get("pending_hitl"))
    emit(
        "ingest",
        f"{roll.property_name or filename}, {roll.unit_count} units, parser={roll.layout}",
        unit_count=roll.unit_count,
        layout=roll.layout,
        geo_confidence=roll.geo_confidence,
    )
    return ChatOutcome(
        kind="ingest_ack",
        text=text,
        stop_reason="ingest",
        ingest={
            "property_name": roll.property_name,
            "unit_count": roll.unit_count,
            "as_of": roll.as_of,
            "geo_confidence": roll.geo_confidence,
            "layout": roll.layout,
        },
    )


def run_task(
    username: str,
    message: str,
    *,
    search_fail: bool = False,
    hitl_allow: bool = False,
    has_attachment: bool = False,
    skip_scope: bool = False,
    persist_as: str | None = None,
) -> ChatOutcome:
    from cre_rentroll.agentcore_client import invoke_run_task, should_invoke_remote

    if should_invoke_remote() and not skip_scope:
        return invoke_run_task(
            username,
            message,
            search_fail=search_fail,
            has_attachment=has_attachment,
        )
    _ = has_attachment
    run_id = str(uuid.uuid4())[:8]
    shown_user = persist_as or user_visible(message)
    traces = turn_ctx.reset_turn(
        username, hitl_allow=hitl_allow, search_fail=search_fail, keep_run=skip_scope
    )
    asked = as_of_from_message(persist_as or message)
    turn_ctx.set_asked_as_of(asked)
    turn_ctx.set_eval_query(persist_as or user_visible(message))
    sess = vault.load_session(username)
    messages = list(sess.get("messages") or [])
    retry_used = bool(sess.get("eval_retry_used"))
    roll = vault.load_roll(username)
    has_vault = roll is not None

    try:
        from cre_rentroll.agentcore_platform import begin_turn

        begin_turn(username)
    except Exception:
        pass
    inv = BOOK
    subgoals = decompose(message, [inv])
    turn_ctx.set_plan([inv.name], list(inv.skills))
    emit("inventory", "full tool menu (book)", inventories=[inv.name])
    if inv.skills:
        emit("skills", f"attached {', '.join(inv.skills)}", skills=list(inv.skills))
    facts = _facts_for_agent(username, has_vault)
    _emit_llm_memory_context(facts, messages)
    drafts: list[str] = []
    tools_called: list[str] = []

    if not openai_api_key():
        emit("stop", "OPENAI_API_KEY missing")
        text = scope_fail_chat("missing key")
        outcome = ChatOutcome(kind="error", text=text, stop_reason="missing_openai_key")
        _persist(username, messages, shown_user, outcome, run_id, traces)
        return outcome

    for book_inv, _sub in subgoals:
        prompt = session_prompt(shown_user, messages)
        try:
            draft = _invoke_react(book_inv, prompt, facts)
        except RateLimitedError as exc:
            emit_stop_and_summary(
                "OpenAI rate limited (429)",
                traces=traces,
                retry_n=turn_ctx.retry_count(),
                token_text=shown_user,
                skills=turn_ctx.plan_skills(),
                started=turn_ctx.run_started(),
            )
            outcome = ChatOutcome(
                kind="error",
                text=str(exc) or RATE_LIMIT_TEXT,
                stop_reason="openai_rate_limited",
                tools_called=list(dict.fromkeys(tools_called)),
            )
            _persist(username, messages, shown_user, outcome, run_id, traces)
            return outcome
        except Exception as exc:
            if is_rate_limited(exc):
                outcome = ChatOutcome(
                    kind="error",
                    text=RATE_LIMIT_TEXT,
                    stop_reason="openai_rate_limited",
                    tools_called=list(dict.fromkeys(tools_called)),
                )
                _persist(username, messages, shown_user, outcome, run_id, traces)
                return outcome
            note = reflect.on_tool_error("agent", str(exc))
            emit("error", note)
            if "replan" in note.lower():
                emit("replan", note)
            draft = f"{note}\n{exc}"
        drafts.append(draft)
        tools_called.extend(t["name"] for t in traces)
        _emit_if_reused_memory(draft, traces, facts, messages)

    draft_text = "\n\n".join(drafts)
    draft_text = _user_facing_draft(draft_text, traces)

    if _vault_need_file_hit(traces):
        return _scope_need_file(
            username, messages, shown_user, traces, run_id, text=draft_text
        )

    if any(t.get("name") == "publish_memo" for t in traces) and not hitl_allow:
        last = next(t for t in reversed(traces) if t.get("name") == "publish_memo")
        pending = last.get("args") or {"tool": "publish_memo"}
        pending = {"tool": "publish_memo", "title": pending.get("title", "memo"), "body": pending.get("body", draft_text)}
        vault.save_session(username, messages, pending_hitl=pending)
        emit("hitl", "publish_memo paused — approve or deny")
        return ChatOutcome(
            kind="hitl_pending",
            text="Saving a memo needs your approval. Use Approve or Deny on the right.",
            stop_reason="hitl_pause",
            pending_hitl=pending,
            tools_called=list(dict.fromkeys(tools_called)),
        )

    _ensure_score_gcr(draft_text)
    tools_called.extend(t["name"] for t in traces)

    return _eval_and_stop(
        username, message, draft_text, roll, traces, tools_called, run_id, messages, retry_used,
        persist_as=shown_user,
    )


def _user_facing_draft(draft: str, traces: list[dict[str, Any]]) -> str:
    """Do not show ReAct operator hints (Need replan / update_plan) as the answer."""
    from cre_rentroll.tools import web_search_failed

    prose = answer_prose(draft)
    if not is_operator_leak(prose):
        return prose
    stored = turn_ctx.eval_scores()
    if stored is not None and getattr(stored, "judge", None) in {"empty", "not_run"}:
        turn_ctx.set_eval_scores(None)
    if web_search_failed(traces):
        return SEARCH_DOWN_TEXT
    return (
        "I could not finish that answer this turn. "
        "Please send the question again."
    )


def _ensure_score_gcr(draft: str) -> None:
    """If ReAct stopped with prose and never called score_gcr, run that tool once."""
    from cre_rentroll.tools import score_gcr

    prose = answer_prose(draft)
    if not (prose or "").strip():
        return
    if turn_ctx.eval_scores() is not None:
        return
    emit(
        "react",
        "score_gcr — required last tool after text-only stop (not a silent Python judge)",
        tools=["score_gcr"],
    )
    score_gcr(prose)


def _eval_and_stop(
    username: str,
    message: str,
    draft: str,
    roll: CanonicalRentRoll | None,
    traces: list[dict[str, Any]],
    tools_called: list[str],
    run_id: str,
    messages: list[dict[str, Any]],
    retry_used: bool,
    *,
    persist_as: str | None = None,
) -> ChatOutcome:
    asked_as_of = as_of_from_message(persist_as or message)
    emit("draft", _draft_log("Draft before eval", draft, as_of=asked_as_of))
    stored = turn_ctx.eval_scores()
    if stored is not None:
        scores = stored
        decision = gate(scores, retry_used)
        if decision == "retry":
            decision = "error"
    else:
        scores = EvalScores(judge="not_run")
        decision = "error"
    scores.retry = retry_used
    uniq = list(dict.fromkeys(tools_called))
    judge = scores.judge or "not_run"
    eval_msg = format_eval_log(scores, decision)

    emit(
        "eval",
        eval_msg,
        llm=judge == "llm",
        G=scores.G,
        request_C=scores.request_C,
        R=scores.R,
        vault_calc_ok=scores.vault_calc_ok,
        judge=judge,
        gate=decision,
    )
    prose = answer_prose(draft)
    empty = not (prose or "").strip()
    if empty:
        show = (
            "I could not produce a rent-roll answer this turn. "
            "Please send the question again."
        )
    else:
        show = prose
    if decision == "error":
        stop = "eval_error"
        kind = "error"
    elif decision == "warn":
        kind = "warning"
        stop = "eval_warn"
    else:
        kind = "answer"
        stop = "eval_ok"

    outcome = ChatOutcome(
        kind=kind,  # type: ignore[arg-type]
        text=show,
        eval=scores,
        stop_reason=stop,
        tools_called=uniq,
    )
    emit_stop_and_summary(
        stop,
        traces=traces,
        retry_n=turn_ctx.retry_count(),
        scores=scores,
        token_text=(persist_as or message or "") + (draft or "") + (show or ""),
        skills=turn_ctx.plan_skills(),
        started=turn_ctx.run_started(),
    )
    _persist(username, messages, persist_as or user_visible(message), outcome, run_id, traces)
    return outcome


def _persist(
    username: str,
    messages: list[dict[str, Any]],
    user_text: str,
    outcome: ChatOutcome,
    run_id: str,
    traces: list[dict[str, Any]],
) -> None:
    messages = messages + [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": outcome.text[:4000], "kind": outcome.kind},
    ]
    vault.save_session(username, messages, pending_hitl=outcome.pending_hitl, eval_retry_used=False)
    try:
        write_trace(
            username,
            run_id=run_id,
            question=user_text,
            tools_called=outcome.tools_called or [],
            scores=outcome.eval.model_dump() if outcome.eval else {},
            stop_reason=outcome.stop_reason or "",
            preview=(outcome.text or "")[:400],
        )
    except Exception:
        pass
    try:
        from cre_rentroll.agentcore_platform import end_turn

        end_turn(
            username,
            user_text=user_text,
            assistant_text=outcome.text or "",
            traces=traces,
            scores=outcome.eval.model_dump() if outcome.eval else None,
        )
    except Exception:
        pass
    _ = measure_tokens(outcome.text)
    _ = traces


def _emit_llm_memory_context(facts: str, history: list[dict[str, Any]] | None) -> None:
    """Show the compact Run Log what Python stuffed in *before* Strands chose tools."""
    prior = 0
    for row in history or []:
        role = str(row.get("role") or "").strip().lower()
        if role in {"user", "assistant"} and str(row.get("content") or "").strip():
            prior += 1
    blob = " ".join((facts or "").split())
    if len(blob) > 900:
        blob = blob[:899] + "…"
    emit(
        "memory_context",
        f"LLM facts block ({prior} prior chat turns also in session_prompt): {blob or '(empty)'}",
    )


def _emit_if_reused_memory(
    draft: str,
    traces: list[dict[str, Any]],
    facts: str,
    history: list[dict[str, Any]] | None,
) -> None:
    """Heuristic: Python cannot read a hidden LLM thought. Log when vault tools were skipped."""
    names = {str(t.get("name") or "") for t in traces}
    if names & {"query_units", "compute_income", "compute_noi", "compute_cap_rate"}:
        return
    low = (draft or "").lower()
    if not any(w in low for w in ("vacant", "occupanc", "in-place", "unit")):
        return
    emit(
        "memory_reuse",
        "No vault MCP tool this turn, but the draft used roll numbers. "
        "Python always auto-retrieves AgentCore Memory + Dynamo FACT# and prior chat; "
        "it does not regex the question. The LLM skipped query_units — it already had those values in context.",
    )


def _facts_for_agent(username: str, has_vault: bool) -> str:
    facts = facts_prompt(username)
    if has_vault:
        return (
            "VAULT HAS a rent roll for this session. Use vault MCP tools for numbers.\n"
            "FACT# in_place_monthly is occupied-only (same as compute_income.in_place_monthly). "
            "It is not 100% occupancy / all-units-occupied income.\n" + facts
        )
    return (
        "VAULT EMPTY: no rent roll is ingested for this session. "
        "If the ask is CRE (including locate/map/where is this property), reply with "
        "the need-file sentence from the system prompt and do not call query_units, "
        "compute_*, osm_geocode, or web_search.\n" + facts
    )


def _vault_need_file_hit(traces: list[dict[str, Any]]) -> bool:
    from cre_rentroll.tools import VAULT_TOOL_NAMES

    for row in traces:
        if row.get("name") not in VAULT_TOOL_NAMES:
            continue
        result = row.get("result") or {}
        if isinstance(result, dict) and result.get("need_file"):
            return True
    return False


def _scope_need_file(
    username: str,
    messages: list[dict[str, Any]],
    shown_user: str,
    traces: list[dict[str, Any]],
    run_id: str,
    *,
    text: str,
) -> ChatOutcome:
    shown = (text or "").strip()
    emit("scope", "scope (agent) — need rent-roll file")
    emit_stop_and_summary(
        "need attachment — vault empty",
        traces=traces,
        retry_n=turn_ctx.retry_count(),
        token_text=shown_user + shown,
        started=turn_ctx.run_started(),
    )
    outcome = ChatOutcome(kind="warning", text=shown, stop_reason="need_attachment")
    _persist(username, messages, shown_user, outcome, run_id, traces)
    return outcome


def _draft_log(who: str, text: str, as_of: str | None = None) -> str:
    _ = as_of
    body = (text or "").strip()
    if not body:
        return f"{who} is empty (0 characters)."
    return f"{who}: {body}"
