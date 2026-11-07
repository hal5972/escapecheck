"""役を呼ぶ順番（結合の約束。中身は B のセッションが作る）。

順番はコードが決める（LLM に任せない）。役は Agent SDK の別のセッションで動かす:
聞き取り役（claude-sonnet-5-5）、赤チーム役（claude-opus-5-5）、検証役（claude-sonnet-5-5、別のセッション）。
計画は端末が持ち、要求ごとに送られてくる。サーバーは処理の間だけ持つ。
設計は docs/03-design.md の 6 節と 8 節。

API キー: 環境変数 ESCAPECHECK_ANTHROPIC_API_KEY があればそれを、無ければ ANTHROPIC_API_KEY を使い、
Agent SDK には options の env で渡す。キーをログ・画面・例外のメッセージに出さない。
"""

from __future__ import annotations

from typing import Literal, TypedDict

from tools.plan_model import Finding, Issue, Plan


class InterviewResult(TypedDict):
    plan: Plan
    next_question: str | None  # 次に聞く1問（読み上げる文）。全部そろえば None
    changed_fields: list[str]


class JobStatus(TypedDict):
    job_id: str
    state: Literal["running", "done", "failed"]
    events: list[str]  # 画面に出す進み具合（「冬の真夜中の筋書きで試しています」など）
    plan: Plan | None  # done のとき、findings と fixes が入った計画
    error: str | None  # failed のとき、利用者に見せてよい文（キーや内部の情報を含めない）


def interview(plan: Plan, utterance: str, speaker: Literal["minsei", "person", "family"] = "minsei") -> InterviewResult:
    """話した文字（仮の名前に置き換え済み）を、聞き取り役に渡して欄に入れ、次の1問を返す。"""
    raise NotImplementedError("B のセッションが作る")


def start_check(plan: Plan) -> str:
    """赤チーム役で崩し始め、ジョブの ID を返す（時間がかかるので、画面は get_job で問い合わせる）。"""
    raise NotImplementedError("B のセッションが作る")


def get_job(job_id: str) -> JobStatus:
    """崩しの進み具合と結果を返す。"""
    raise NotImplementedError("B のセッションが作る")


def approve(plan: Plan, fix_id: str, by: Literal["minsei", "person"] = "minsei") -> Plan:
    """人の承認を記録し、直し案を計画に入れて返す（承認の無い apply_fix はフックが止める）。"""
    raise NotImplementedError("B のセッションが作る")


def verify(plan: Plan) -> list[Issue]:
    """検証役（別のセッション）で、出典と原文・データの値の食い違いを点検する。完成の門番はコードの点検で、これは補助。"""
    raise NotImplementedError("B のセッションが作る")


def redteam_for_eval(plan: Plan) -> list[Finding]:
    """評価（eval/run_eval.py の --runner claude）用: start_check と同じ流れを待ち合わせて実行し、findings を返す。"""
    raise NotImplementedError("B のセッションが作る")
