"""地理情報の取得（インターフェース仕様。実装は A のセッションが行う）。

浸水深（国土数値情報 A40 を土佐市の範囲で切り出したもの）、津波到達時間（土佐市・高知県の予測の表）、
津波の指定緊急避難場所（国土地理院）、標高（国土地理院の標高 API。結果を保存して再利用する）、距離（直線距離 × 1.3 の概算）。
すべての値に出典 ID を付け、計画の出典リストに登録する。

MCP ツールは座標を引数に取らない。target（"home" や避難支援者の supporter_id）を受け取り、計画の中の座標を参照する。
設計は docs/03-design.md の 4 節と 6.3 節。
"""

from __future__ import annotations

from tools.plan_model import Facts, Plan, Shelter

#: 道なりの距離の概算に使う係数（仮定。画面に「概算」と表示する）
ROUTE_FACTOR = 1.3


def get_facts(plan: Plan, target: str = "home") -> Facts:
    """target の地点の浸水深の階級・津波到達時間・標高を返す。出典を plan["sources"] に登録する。

    取得できない値は None にし、notes に理由を記載する（例: 標高 API が停止している）。例外は外に出さない。
    """
    raise NotImplementedError("A のセッションが実装する")


def find_shelters(plan: Plan, target: str = "home", limit: int = 3) -> list[Shelter]:
    """target から近い、津波の指定緊急避難場所を、近い順に limit 件返す。

    距離は直線距離 × ROUTE_FACTOR の概算（distance_method に記載）。地域の情報（前提条件の引用）を local_notes に入れる。
    """
    raise NotImplementedError("A のセッションが実装する")
