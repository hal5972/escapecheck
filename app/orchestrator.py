"""エージェントの呼び出し順序の制御。

呼び出し順序はコードで決める（LLM に任せない）。各エージェントは Agent SDK の別セッションで動かす:
聞き取りエージェント（claude-sonnet-5-5）、レッドチームエージェント（claude-opus-5-5）、検証エージェント（claude-sonnet-5-5、別セッション）。
計画は端末が保持し、リクエストごとに送られる。サーバーは処理中だけ保持する。

API キー: 環境変数 ESCAPECHECK_ANTHROPIC_API_KEY があればそれを、無ければ ANTHROPIC_API_KEY を使い、
Agent SDK には options の env で渡す。キーをログ・画面・例外のメッセージに出さない。

処理の順序（コードで固定）:
- interview: 発言の電話番号などを伏せる → 出典リストに登録 → 聞き取りエージェント → 変わった欄をコードで求める →
  次に聞く欄と質問の文をコードで決める（質問は、確認済みの決まった文 QUESTION_TEMPLATES だけを使う）
- check: レッドチームエージェント（前提条件の抽出 → シナリオの計算 → 問題点 → 改善案）。数値は sim が出す
- approve: 承認を記録 → 承認の確認（hooks.check_apply_fix）→ 反映 → sim で計算し直す
- verify: コードの点検（hooks.code_issues）→ 検証エージェント（別セッション・読み取りだけ）→ 人の確認の印を引き継いで合わせる
- finalize: コードの点検（hooks.check_finalize）で1つでも欠ければ止める
"""

from __future__ import annotations

import copy
import hashlib
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, NotRequired, TypedDict

from app import agents, config, hooks, mcp_inprocess, privacy
from app.audit import AUDIT, now_iso
from app.backend import default_backend
from app.session import PlanSession
from tools import plan_model as pm
from tools.plan_model import Finding, Issue, Plan

log = logging.getLogger("escapecheck.orchestrator")


class InterviewResult(TypedDict):
    plan: Plan
    next_question: str | None  # 次の追加の質問（音声読み上げする文）。すべてそろえば None
    changed_fields: list[str]
    next_field: NotRequired[str | None]  # 次の質問で聞く欄（画面は、次の発言の asked_field に返す）


class JobStatus(TypedDict):
    job_id: str
    state: Literal["running", "done", "failed"]
    events: list[str]  # 画面に出す進捗（「冬の深夜のシナリオで点検しています」など）
    plan: Plan | None  # done のとき、findings と fixes が入った計画（running の間は途中までの計画）
    error: str | None  # failed のとき、利用者に見せてよい文（キーや内部情報を含めない）


class FinalizeRejected(Exception):
    """完成のチェックで止まった。reasons は画面に出してよい文。"""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("完成にできません")
        self.reasons = reasons


class Busy(RuntimeError):
    """点検が同時に多く動いている。"""


#: 追加の質問の順序（F-05: 点検に影響する欄から）
QUESTION_ORDER = [
    "walk_speed", "sleep_place", "time_to_leave_night", "time_to_leave_day", "supporters", "mobility", "household",
    "hearing_and_sight", "shelter", "route", "support_method", "contact", "health_for_evacuation", "welfare_services",
    "notes",
]

#: 追加の質問の文（読み上げる文）。確認済みの決まった文だけを使い、聞き取りエージェントが作った文は使わない
QUESTION_TEMPLATES = {
    "walk_speed": "100メートル歩くのに、何分くらいかかりますか。",
    "sleep_place": "夜は、何階で寝ていますか。",
    "time_to_leave_night": "夜中に大きく揺れたら、家を出るまで何分くらいかかりそうですか。",
    "time_to_leave_day": "昼間に大きく揺れたら、家を出るまで何分くらいかかりそうですか。",
    "supporters": "避難のときに、声をかけてくれる人はいますか。",
    "mobility": "歩くときに、杖や車いすを使いますか。",
    "household": "どなたと暮らしていますか。",
    "hearing_and_sight": "市の防災の放送や、人の呼びかけは聞こえますか。",
    "shelter": "津波のときは、どこへ避難しますか。",
    "route": "避難先へは、どの道を通りますか。",
    "support_method": "声をかけてくれる人は、どのように手伝ってくれますか。",
    "contact": "連絡は、どの電話でとれますか。番号は言わなくてかまいません。",
    "health_for_evacuation": "避難のときに気をつけることはありますか。飲んでいる薬で眠りが深くなることはありますか。",
    "welfare_services": "デイサービスなど、利用している福祉サービスはありますか。",
    "notes": "ほかに、避難のときに心配なことはありますか。",
}

MAX_CONCURRENT_JOBS = 2
JOB_KEEP_SEC = 30 * 60


# ---------------------------------------------------------------------------
# 差し替え（テスト用）
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {"backend": None, "run_agent": None}


def backend() -> Any:
    if _state["backend"] is None:
        _state["backend"] = default_backend()
    return _state["backend"]


def _run_agent(session: PlanSession, role: str, prompt: str) -> agents.AgentRun:
    runner = _state.get("run_agent") or agents.run_agent_sync
    return runner(session, role, prompt)


def override(**values: Any) -> Callable[[], None]:
    """計算（backend）とエージェントの実行（run_agent）を差し替え、元に戻す関数を返す（テスト用）。"""
    previous = {key: _state.get(key) for key in values}
    _state.update(values)

    def restore() -> None:
        _state.update(previous)

    return restore


# ---------------------------------------------------------------------------
# 聞き取り
# ---------------------------------------------------------------------------


def _fields_snapshot(plan: Plan) -> dict[str, Any]:
    return copy.deepcopy(plan["fields"])


def asked_fields(plan: Plan) -> set[str]:
    """すでに質問した欄（発言の出典の answer_to）。答えが得られなかった欄を、同じ質問で繰り返さない（E2）。"""
    return {s["answer_to"] for s in plan["sources"].values() if s.get("kind") == "utterance" and s.get("answer_to")}


def missing_fields(plan: Plan, exclude: set[str] | None = None) -> list[str]:
    exclude = exclude or set()
    return [k for k in QUESTION_ORDER if plan["fields"].get(k, {}).get("status") != "filled" and k not in exclude]


def interview(
    plan: Plan,
    utterance: str,
    speaker: Literal["minsei", "person", "family"] = "minsei",
    *,
    asked_field: str | None = None,
) -> InterviewResult:
    """文字起こし（仮名化済み）を聞き取りエージェントに渡して項目に入れ、次の追加の質問を返す。

    asked_field は、この発言が答えている質問の欄（前の応答の next_field）。省略してよい。
    """
    if not isinstance(utterance, str) or not utterance.strip():
        raise ValueError("発言が空です")
    if len(utterance) > config.MAX_UTTERANCE_CHARS:
        raise ValueError(f"発言が長すぎます（{config.MAX_UTTERANCE_CHARS}字まで）")
    if speaker not in agents.SPEAKER_LABELS:
        raise ValueError("話した人の種類が正しくありません")
    privacy.sanitize_incoming_plan(plan)
    text, _ = privacy.mask_pii(utterance.strip())
    before = _fields_snapshot(plan)
    stamp = time.strftime("%m/%d %H:%M")
    source = pm.Source(kind="utterance", label=f"{agents.SPEAKER_LABELS[speaker]}の発言（{stamp}）", text=text,
                       speaker=speaker, at=now_iso())
    source_id = pm.register_source(plan, source)
    if asked_field in pm.FIELDS:
        plan["sources"][source_id]["answer_to"] = asked_field  # type: ignore[typeddict-unknown-key]

    session = PlanSession(plan=plan, role="interview", backend=backend())
    prompt = agents.interview_prompt(plan, source_id, text, speaker, missing_fields(plan), asked_field)
    _run_agent(session, "interview", prompt)

    changed = [k for k in pm.FIELDS if plan["fields"].get(k) != before.get(k)]
    remaining = missing_fields(plan, exclude=asked_fields(plan))
    next_field = remaining[0] if remaining else None
    question = QUESTION_TEMPLATES[next_field] if next_field is not None else None
    return InterviewResult(plan=plan, next_question=question, changed_fields=changed, next_field=next_field)


# ---------------------------------------------------------------------------
# 点検（レッドチームエージェント。ジョブで動かす）
# ---------------------------------------------------------------------------


@dataclass
class Job:
    job_id: str
    session: PlanSession
    state: Literal["running", "done", "failed"] = "running"
    error: str | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None
    cost_usd: float | None = None


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _cleanup_jobs() -> None:
    now = time.time()
    for job_id in [j for j, job in _jobs.items() if job.finished and now - job.finished > JOB_KEEP_SEC]:
        del _jobs[job_id]


def prepare_for_check(plan: Plan) -> Plan:
    """点検の前の計画。前回の問題点と承認していない改善案を外す（承認した改善案は効き目を残すために保つ）。"""
    plan = copy.deepcopy(plan)
    approved = {a["fix_id"] for a in plan.get("approvals", [])}
    plan["findings"] = []
    plan["fixes"] = [f for f in plan.get("fixes", []) if f["fix_id"] in approved]
    plan["status"] = "draft"
    return plan


#: 計算できない理由の表示（tools/sim.py の REASON_LABELS が使えないときの控え）
_REASON_FALLBACK = {
    "NO_HOME": "自宅の場所が未確認のため、計算できません",
    "NO_ARRIVAL_TIME": "津波到達時間の表に値が無いため、計算できません",
    "SHELTER_NOT_TSUNAMI": "避難先が、津波の指定緊急避難場所ではありません",
    "SHELTER_UNKNOWN": "避難先が、指定緊急避難場所のデータに見つかりません",
    "NO_SHELTER_AVAILABLE": "この人が利用できる津波の避難場所が見つかりません",
    "NO_SUPPORTER": "避難支援者が登録されていないため、計算できません",
}


def reason_label(code: str | None) -> str:
    try:
        from tools.sim import REASON_LABELS  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        REASON_LABELS = {}
    return REASON_LABELS.get(code or "") or _REASON_FALLBACK.get(code or "", "計算に必要な情報が足りません")


def run_redteam(session: PlanSession) -> agents.AgentRun:
    """レッドチームエージェントを1回動かす（start_check と評価で共通）。"""
    session.add_event("計画を読んでいます")
    run = _run_agent(session, "redteam", agents.redteam_prompt(session.plan))
    if "tools_not_ready" in session.flags:
        raise agents.AgentFailed("計算のモジュールがまだ準備されていません。準備ができてから、もう一度点検してください")
    with session.lock:
        findings = session.plan["findings"]
        session.plan["status"] = "checked"
        short = [f for f in findings if f["result"].get("verdict") == "short"]
        results = dict(session.last_results)
    computed = {s: r for s, r in results.items() if r.get("verdict") != "cannot_compute"}
    if results and not computed:
        # どのシナリオも計算できなかった。「問題なし」と誤解させない
        reasons = sorted({reason_label(r.get("reason_code")) for r in results.values()})
        session.add_event("点検の計算ができませんでした。" + " ".join(reasons))
    elif not findings:
        session.add_event("点検が終わりました。間に合わないシナリオは見つかりませんでした")
    else:
        session.add_event(f"点検が終わりました。問題点は{len(findings)}件です")
    if computed and short and all(r.get("verdict") == "short" for r in computed.values()):
        session.add_event("この人は、現在の条件では間に合いません。市や家族と話し合うための資料を印刷できます")
    return run


def _run_check_job(job: Job) -> None:
    try:
        run = run_redteam(job.session)
        job.cost_usd = run.cost_usd
        job.state = "done"
    except (agents.AgentUnavailable, agents.AgentFailed) as exc:
        if job.session.plan["findings"]:
            job.session.add_event(f"点検を途中で止めました（{exc}）。見つかった問題点だけを表示します")
            job.state = "done"
        else:
            job.error = str(exc)
            job.state = "failed"
    except Exception:  # noqa: BLE001  利用者には内部の情報を見せない
        log.error("check job failed\n%s", privacy.redacted_traceback())
        job.error = "点検の途中でエラーが起きました。もう一度お試しください"
        job.state = "failed"
    finally:
        job.finished = time.time()


def start_check(plan: Plan) -> str:
    """レッドチームエージェントで点検を開始し、ジョブ ID を返す（時間がかかるため、画面は get_job で進捗を確認する）。"""
    privacy.sanitize_incoming_plan(plan)
    with _jobs_lock:
        _cleanup_jobs()
        if sum(1 for j in _jobs.values() if j.state == "running") >= MAX_CONCURRENT_JOBS:
            raise Busy("ほかの点検が動いています。終わってから、もう一度お試しください")
        session = PlanSession(plan=prepare_for_check(plan), role="redteam", backend=backend())
        job = Job(job_id=pm.new_id("job"), session=session)
        _jobs[job.job_id] = job
    threading.Thread(target=_run_check_job, args=(job,), name=f"check-{job.job_id}", daemon=True).start()
    return job.job_id


def get_job(job_id: str) -> JobStatus:
    """点検の進捗と結果を返す。無いジョブは KeyError。"""
    with _jobs_lock:
        job = _jobs[job_id]
    with job.session.lock:
        plan_copy = copy.deepcopy(job.session.plan)
        events = list(job.session.events)
    return JobStatus(job_id=job.job_id, state=job.state, events=events,
                     plan=plan_copy if job.state != "failed" else None, error=job.error)


def redteam_for_eval(plan: Plan) -> list[Finding]:
    """評価用（eval/run_eval.py の --runner claude）: start_check と同じ処理を同期的に実行し、findings を返す。

    渡された plan も、点検の後の計画（premises・findings・fixes・sources）に置き換える。
    評価は、危険な改善案が出なかったかを plan["fixes"] で確認するため（AC-07）。
    """
    session = PlanSession(plan=prepare_for_check(plan), role="redteam", backend=backend())
    try:
        run_redteam(session)
    finally:
        with session.lock:
            checked = copy.deepcopy(session.plan)
        plan.clear()
        plan.update(checked)
    return plan["findings"]


# ---------------------------------------------------------------------------
# 承認・計算し直し
# ---------------------------------------------------------------------------


def approve(plan: Plan, fix_id: str, by: Literal["minsei", "person"] = "minsei") -> Plan:
    """人の承認を記録し、改善案を計画に反映して返す（承認の無い apply_fix はフックが拒否する）。"""
    if by not in ("minsei", "person"):
        raise ValueError("承認した人の種類が正しくありません")
    privacy.sanitize_incoming_plan(plan)
    if hooks.find_fix(plan, fix_id) is None:
        raise ValueError("計画に無い改善案です")
    if not any(a.get("fix_id") == fix_id for a in plan["approvals"]):
        plan["approvals"].append(mcp_inprocess.approval_record(fix_id, by))
    try:
        reasons = mcp_inprocess.apply_fix(plan, fix_id, backend())
    except mcp_inprocess.ToolsNotReady:
        raise agents.AgentFailed("計算のモジュールがまだ準備されていません") from None
    if reasons:
        raise ValueError("\n".join(reasons))
    AUDIT.record(plan_id=plan["plan_id"], agent="server", tool="plan.apply_fix", decision="info",
                 args={"fix_id": fix_id, "by": by})
    return plan


def recheck(plan: Plan) -> dict[str, pm.ScenarioResult]:
    """問題点のシナリオを、今の計画で計算し直す（Claude を通さない。承認の直後に画面の判定を更新するため）。"""
    results: dict[str, pm.ScenarioResult] = {}
    for scenario_id in dict.fromkeys(f["scenario_id"] for f in plan.get("findings", [])):
        try:
            results[scenario_id] = backend().run_scenario(plan, scenario_id, None)  # 登録済みの前提条件をすべて使う
        except (NotImplementedError, ValueError):
            continue
    return results


def facts(plan: Plan, target: str = "home") -> dict[str, Any]:
    """地図の表示用の事実（Claude を通さない）。"""
    if target != "home" and target not in {s.get("supporter_id") for s in plan.get("supporters", [])}:
        raise ValueError("target は home か、計画の避難支援者の supporter_id にしてください")
    be = backend()
    try:
        return {"facts": be.get_facts(plan, target), "shelters": be.find_shelters(plan, target, 3), "plan": plan}
    except NotImplementedError:
        raise agents.AgentFailed("地図のデータの準備がまだできていません") from None


# ---------------------------------------------------------------------------
# 検証と完成
# ---------------------------------------------------------------------------

_LLM_ISSUE_TEMPLATES = {
    "quote_mismatch": "この欄の引用が、発言の原文と合っていない可能性があります。出典を確認してください",
    "value_mismatch": "この欄の値が、出典の内容と合っていない可能性があります。出典を確認してください",
    "unsupported": "この欄に、発言に無い内容が含まれている可能性があります。本人に確認してください",
    "inconsistent": "ほかの欄と食い違っている可能性があります。本人に確認してください",
    "other": "この欄を確認してください",
}


def _llm_issue(raw: Any) -> Issue | None:
    if not isinstance(raw, dict):
        return None
    message = str(raw.get("message") or "").strip()[:200]
    if not message:
        return None
    kind = raw.get("kind") if raw.get("kind") in _LLM_ISSUE_TEMPLATES else "other"
    if re.search(r"\d", unicodedata.normalize("NFKC", message)):
        message = _LLM_ISSUE_TEMPLATES[kind]  # 検証エージェントの文にも数値を出さない（S-01）
    field_key = raw.get("field") if raw.get("field") in pm.FIELDS else None
    severity = raw.get("severity") if raw.get("severity") in ("要確認", "誤り") else "要確認"
    digest = hashlib.sha1(f"{field_key}|{kind}|{message}".encode("utf-8")).hexdigest()[:10]
    issue = Issue(issue_id=f"llm_{digest}", message=message, severity=severity, acknowledged=False)
    if field_key:
        issue["field"] = field_key
    return issue


def merge_issues(previous: list[Issue], current: list[Issue]) -> list[Issue]:
    """人が付けた確認の印（acknowledged）を、同じ ID の項目に引き継ぐ。解消した項目は外す。"""
    acked = {i.get("issue_id") for i in previous if i.get("acknowledged")}
    merged, seen = [], set()
    for issue in current:
        if issue["issue_id"] in seen:
            continue
        seen.add(issue["issue_id"])
        issue = dict(issue)  # type: ignore[assignment]
        issue["acknowledged"] = issue["issue_id"] in acked
        merged.append(issue)  # type: ignore[arg-type]
    return merged


def verify(plan: Plan) -> list[Issue]:
    """検証エージェント（別セッション）で、出典と原文・データの値の食い違いを確認する。完成の判定はコードのチェックで行い、これは補助。

    戻り値は、コードの点検の項目と検証エージェントの項目を合わせたもの。plan["issues"] も同じ内容に更新する。
    """
    privacy.sanitize_incoming_plan(plan)
    code = hooks.code_issues(plan, backend())
    llm: list[Issue] = []
    session = PlanSession(plan=copy.deepcopy(plan), role="verify", backend=backend())
    try:
        run = _run_agent(session, "verify", agents.verify_prompt(session.plan))
        raw = run.structured.get("issues", []) if isinstance(run.structured, dict) else []
        llm = [i for i in (_llm_issue(r) for r in raw) if i is not None]
    except (agents.AgentUnavailable, agents.AgentFailed) as exc:
        llm = [Issue(issue_id="llm_unavailable", severity="要確認", acknowledged=False,
                     message=f"検証エージェントの点検を実行できませんでした（{exc}）。出典を目で確認してください")]
    merged = merge_issues(plan.get("issues", []), code + llm)
    plan["issues"] = merged
    return merged


def finalize(plan: Plan) -> Plan:
    """完成の印を付ける。コードによる点検（hooks.check_finalize）で1つでも欠ければ FinalizeRejected（F-12・AC-03）。"""
    privacy.sanitize_incoming_plan(plan)
    reasons = hooks.check_finalize(plan, backend())
    if reasons:
        AUDIT.record(plan_id=plan["plan_id"], agent="server", tool="plan.finalize", decision="deny", reason="\n".join(reasons))
        raise FinalizeRejected(reasons)
    plan["status"] = "final"
    AUDIT.record(plan_id=plan["plan_id"], agent="server", tool="plan.finalize", decision="info", result={"status": "final"})
    return plan
