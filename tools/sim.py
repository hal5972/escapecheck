"""間に合うかの計算（結合の約束。中身は A のセッションが作る）。

数字はすべてここが出す。Claude（赤チーム役）は、どの筋書きを当てるかと、結果の説明だけを受け持つ。
値を外から渡す引数は作らない（数字が Claude の手に戻らないように）。
設計は docs/03-design.md の 5 節（5.1 一人で逃げる、5.2 支援者と一緒、5.3 筋書きと前提、5.4 直し案）。

判定: 余り 3分以上は ok（緑）、0〜3分は tight（黄）、マイナスは short（赤）。
"""

from __future__ import annotations

from tools.plan_model import Finding, Plan, ScenarioResult

#: 緑と黄の境目（分）
MARGIN_OK_MIN = 3.0


def run_scenario(plan: Plan, scenario_id: str, premise_ids: list[str] | None = None) -> ScenarioResult:
    """筋書き（"S1"〜"S8"）を計画に当てて、間に合うかを計算する。

    premise_ids は plan["premises"] に登録済みの前提だけ。登録の無い ID は ValueError。
    計算できないとき（到達時間が無いなど）は verdict を "cannot_compute" にし、reason_code に理由を入れる。
    """
    raise NotImplementedError("A のセッションが作る")


def try_fix(plan: Plan, scenario_id: str, fix_type: str, target_id: str | None = None) -> ScenarioResult:
    """直し案（plan_model.FIX_TYPES）を当てた場合の結果を返す。計画そのものは変えない。

    target_id は登録簿にある避難先・支援者だけ。危ない案（docs/03-design.md の 5.4「出さない案」）は ValueError で理由を返す。
    """
    raise NotImplementedError("A のセッションが作る")


def baseline_findings(plan: Plan) -> list[Finding]:
    """評価の基準線: S1〜S7 を様式の欄だけに総当たりして、見つかる穴を返す（Claude を使わない）。

    話の中だけにある前提（S8）は見ない。eval/run_eval.py の --runner baseline が使う。
    """
    raise NotImplementedError("A のセッションが作る")
