"""エージェントの呼び出し順序の制御（インターフェース仕様。実装は B のセッションが行う）。

呼び出し順序はコードで決める（LLM に任せない）。各エージェントは Agent SDK の別セッションで動かす:
聞き取りエージェント（claude-sonnet-5-5）、レッドチームエージェント（claude-opus-5-5）、検証エージェント（claude-sonnet-5-5、別セッション）。
計画は端末が保持し、リクエストごとに送られる。サーバーは処理中だけ保持する。
設計は docs/03-design.md の 6 節と 8 節。用語は docs/glossary.md に従う。

API キー: 環境変数 ESCAPECHECK_ANTHROPIC_API_KEY があればそれを、無ければ ANTHROPIC_API_KEY を使い、
Agent SDK には options の env で渡す。キーをログ・画面・例外のメッセージに出さない。
"""

from __future__ import annotations

from typing import Literal, TypedDict

from tools.plan_model import Finding, Issue, Plan


class InterviewResult(TypedDict):
    plan: Plan
    next_question: str | None  # 次の追加の質問（音声読み上げする文）。すべてそろえば None
    changed_fields: list[str]


class JobStatus(TypedDict):
    job_id: str
    state: Literal["running", "done", "failed"]
    events: list[str]  # 画面に出す進捗（「冬の深夜のシナリオで点検しています」など）
    plan: Plan | None  # done のとき、findings と fixes が入った計画
    error: str | None  # failed のとき、利用者に見せてよい文（キーや内部情報を含めない）


def interview(plan: Plan, utterance: str, speaker: Literal["minsei", "person", "family"] = "minsei") -> InterviewResult:
    """文字起こし（仮名化済み）を聞き取りエージェントに渡して項目に入れ、次の追加の質問を返す。"""
    raise NotImplementedError("B のセッションが実装する")


def start_check(plan: Plan) -> str:
    """レッドチームエージェントで点検を開始し、ジョブ ID を返す（時間がかかるため、画面は get_job で進捗を確認する）。"""
    raise NotImplementedError("B のセッションが実装する")


def get_job(job_id: str) -> JobStatus:
    """点検の進捗と結果を返す。"""
    raise NotImplementedError("B のセッションが実装する")


def approve(plan: Plan, fix_id: str, by: Literal["minsei", "person"] = "minsei") -> Plan:
    """人の承認を記録し、改善案を計画に反映して返す（承認の無い apply_fix はフックが拒否する）。"""
    raise NotImplementedError("B のセッションが実装する")


def verify(plan: Plan) -> list[Issue]:
    """検証エージェント（別セッション）で、出典と原文・データの値の食い違いを確認する。完成の判定はコードのチェックで行い、これは補助。"""
    raise NotImplementedError("B のセッションが実装する")


def redteam_for_eval(plan: Plan) -> list[Finding]:
    """評価用（eval/run_eval.py の --runner claude）: start_check と同じ処理を同期的に実行し、findings を返す。"""
    raise NotImplementedError("B のセッションが実装する")
