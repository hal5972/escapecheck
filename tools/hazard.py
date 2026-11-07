"""地理の事実（結合の約束。中身は A のセッションが作る）。

浸水の深さ（国土数値情報 A40 を土佐市分に切った物）、津波の到達時間（土佐市・高知県の予測の表）、
津波の対象の指定緊急避難場所（国土地理院）、標高（国土地理院の標高 API。手元に残す）、距離（直線 × 1.3 の概算）。
全ての値に出典（source_id）を付け、計画の登録簿に登録する。

道具は座標を引数に取らない。target（"home" や支援者の supporter_id）を受け取り、計画の中の座標を引く。
設計は docs/03-design.md の 4 節と 6.3 節。
"""

from __future__ import annotations

from tools.plan_model import Facts, Plan, Shelter

#: 道なりの距離の概算の係数（仮定。画面に「概算」と出す）
ROUTE_FACTOR = 1.3


def get_facts(plan: Plan, target: str = "home") -> Facts:
    """target の場所の、浸水の深さの階級・到達時間・標高を返す。出典を plan["sources"] に登録する。

    取れない値は None にし、notes に理由を書く（例: 標高 API が止まっている）。例外を外に出さない。
    """
    raise NotImplementedError("A のセッションが作る")


def find_shelters(plan: Plan, target: str = "home", limit: int = 3) -> list[Shelter]:
    """target から近い、津波の対象の指定緊急避難場所を、近い順に limit 件返す。

    距離は直線距離 × ROUTE_FACTOR の概算（distance_method に書く）。地元の知恵（前提の quote）を local_notes に入れる。
    """
    raise NotImplementedError("A のセッションが作る")
