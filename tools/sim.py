"""避難が間に合うかの計算（インターフェース仕様。実装は A のセッションが行う）。

数値はすべてこのモジュールが算出する。Claude（レッドチームエージェント）は、どのシナリオを適用するかの選択と、結果の説明だけを担当する。
外部から数値を渡す引数は作らない（数値の決定を Claude に戻さないため）。
設計は docs/03-design.md の 5 節（5.1 単独での避難、5.2 避難支援者との避難、5.3 シナリオと前提条件、5.4 改善案）。

判定: 余裕時間 3分以上は「間に合う」、0〜3分は「余裕が少ない」、マイナスは「間に合わない」。
"""

from __future__ import annotations

from tools.plan_model import Finding, Plan, ScenarioResult

#: 「間に合う」と「余裕が少ない」の境界（分）
MARGIN_OK_MIN = 3.0


def run_scenario(plan: Plan, scenario_id: str, premise_ids: list[str] | None = None) -> ScenarioResult:
    """シナリオ（"S1"〜"S8"）を計画に適用し、避難が間に合うかを計算する。

    premise_ids は plan["premises"] に登録済みの前提条件だけを受け付ける。未登録の ID は ValueError。
    計算できないとき（津波到達時間が無いなど）は verdict を "cannot_compute" にし、reason_code に理由を入れる。
    """
    raise NotImplementedError("A のセッションが実装する")


def try_fix(plan: Plan, scenario_id: str, fix_type: str, target_id: str | None = None) -> ScenarioResult:
    """改善案（plan_model.FIX_TYPES）を適用した場合の結果を返す。計画そのものは変更しない。

    target_id は出典リストにある避難先・避難支援者だけを受け付ける。危険な改善案（docs/03-design.md の 5.4 節）は ValueError で理由を返す。
    """
    raise NotImplementedError("A のセッションが実装する")


def baseline_findings(plan: Plan) -> list[Finding]:
    """評価のベースライン: S1〜S7 を様式の項目だけに総当たりで適用し、見つかる問題点を返す（Claude を使わない）。

    聞き取りの中だけにある前提条件（S8）は扱わない。eval/run_eval.py の --runner baseline が使う。
    """
    raise NotImplementedError("A のセッションが実装する")
