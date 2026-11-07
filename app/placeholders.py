"""説明文のプレースホルダー（{margin_min} など）を、計算の結果の値で置き換える（S-01）。

Claude（レッドチームエージェント）は説明文に数値を直接記載せず、プレースホルダーで記載する。
値はコード（sim.run_scenario・sim.try_fix の結果）から取り、ここで整数の分に丸めて埋める。
丸めは安全側に寄せる: 所要時間と不足する時間は切り上げ、到達時間と余裕時間と短縮できる時間は切り捨てる。
"""

from __future__ import annotations

import math
import re
from typing import Any

from tools import plan_model as pm

#: 利用できるプレースホルダーと意味（Skill の red-team-scenarios にも同じ一覧を記載する）
PLACEHOLDERS: dict[str, str] = {
    "need_min": "避難所要時間（分）",
    "avail_min": "津波到達時間（分）",
    "margin_min": "余裕時間（分）。間に合うときだけ使う",
    "shortage_min": "不足する時間（分）。間に合わないときだけ使う",
    "leave_min": "避難開始までの時間（分）",
    "walk_min": "移動の時間（分）",
    "climb_min": "避難先で上がる時間（分）",
    "help_min": "避難支援者が声をかけ、一緒に避難を始めるまでの時間（分）",
    "supporter_margin_min": "避難支援者の余裕時間（分）。避難支援者が間に合うときだけ使う",
    "supporter_shortage_min": "避難支援者の不足する時間（分）。避難支援者が間に合わないときだけ使う",
    "saved_min": "改善案で短縮できる時間（分）。改善案の説明だけで使う",
    "shelter_name": "避難先の名前",
}

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _ceil(x: float) -> int:
    # 浮動小数点の誤差（12.000000001 など）で1分増えないように、小数第6位で丸めてから切り上げる
    return int(math.ceil(round(x, 6)))


def _floor(x: float) -> int:
    return int(math.floor(round(x, 6)))


def _sum_breakdown(result: pm.ScenarioResult, key: str) -> float | None:
    rows = [row for row in result.get("breakdown", []) if row.get("key") == key]
    if not rows:
        return None
    return float(sum(row.get("minutes", 0) for row in rows))


def shelter_text(plan: pm.Plan) -> str | None:
    """計画の避難先の欄の名前（{"text": ...} の形にも、文字列にも対応する）。"""
    field = plan.get("fields", {}).get("shelter") or {}
    value = field.get("value")
    if isinstance(value, dict):
        value = value.get("text")
    return value if isinstance(value, str) and value.strip() else None


def placeholder_values(
    result: pm.ScenarioResult | None,
    plan: pm.Plan | None = None,
    *,
    saved_min: float | None = None,
    shelter_name: str | None = None,
) -> dict[str, str | None]:
    """プレースホルダーごとの表示用の値。使えない値は None（その説明文はフックが拒否する）。"""
    values: dict[str, str | None] = {name: None for name in PLACEHOLDERS}
    if result is not None:
        need = result.get("need_min")
        avail = result.get("avail_min")
        margin = result.get("margin_min")
        if need is not None:
            values["need_min"] = str(_ceil(need))
        if avail is not None:
            values["avail_min"] = str(_floor(avail))
        if margin is not None:
            if margin >= 0:
                values["margin_min"] = str(_floor(margin))
            else:
                values["shortage_min"] = str(_ceil(-margin))
        for key in ("leave", "walk", "climb", "help"):
            total = _sum_breakdown(result, key)
            if total is not None:
                values[f"{key}_min"] = str(int(math.floor(total + 0.5)))
        sup_margin = result.get("supporter_margin_min")
        if sup_margin is not None:
            if sup_margin >= 0:
                values["supporter_margin_min"] = str(_floor(sup_margin))
            else:
                values["supporter_shortage_min"] = str(_ceil(-sup_margin))
    if saved_min is not None and saved_min > 0:
        values["saved_min"] = str(_floor(saved_min))
    name = shelter_name or (shelter_text(plan) if plan is not None else None)
    if name:
        values["shelter_name"] = name
    return values


def used_placeholders(template: str) -> list[str]:
    return PLACEHOLDER_RE.findall(template or "")


def unresolved(template: str, values: dict[str, str | None]) -> list[str]:
    """テンプレートが使っているのに、値が無いプレースホルダー。"""
    return [name for name in used_placeholders(template) if values.get(name) is None]


def fill(template: str, values: dict[str, str | None]) -> str:
    """プレースホルダーを値で置き換える。値の無いものは「（不明）」にする（フックが先に拒否するので、通常は起きない）。"""

    def replace(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        return value if value is not None else "（不明）"

    return PLACEHOLDER_RE.sub(replace, template or "")
