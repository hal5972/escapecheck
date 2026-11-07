/*
 * 画面に出す表示名の一覧。
 * tools/plan_model.py の FIELDS・SCENARIOS・PREMISE_CODES・HOLE_TYPES・FIX_TYPES・VERDICT_LABELS と同じ内容にする。
 * plan_model.py を変更したときは、このファイルも合わせる（インターフェース仕様）。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});

  EC.L = {
    // 計画の項目。表示順に並べる
    FIELDS: {
      household: '世帯構成（一人暮らし・夫婦など）',
      mobility: '移動の状況（杖・車いす・介助の要否）',
      walk_speed: '歩行速度（例: 100mを3分）',
      sleep_place: '就寝場所（階数・玄関からの距離）',
      time_to_leave_day: '避難開始時間（日中）',
      time_to_leave_night: '避難開始時間（夜間）',
      hearing_and_sight: '聴覚・視覚（避難に関わること）',
      health_for_evacuation: '避難に関わる心身の状況（服薬、医療機器など）',
      supporters: '避難支援者',
      support_method: '避難支援の方法',
      shelter: '避難先（指定緊急避難場所）',
      route: '避難経路',
      contact: '連絡手段',
      welfare_services: '利用している福祉サービス',
      notes: '留意事項',
    },

    // 専門用語に添える短い説明（U-04）。キーは FIELDS のキー
    FIELD_HELP: {
      supporters: '避難のとき、助けに来る人',
      shelter: '市が指定した避難先',
    },

    SCENARIOS: {
      S1: '冬の深夜（午前3時）',
      S2: '平日の日中（家族・避難支援者が不在）',
      S3: '避難経路の閉塞',
      S4: '避難支援者が来られない',
      S5: '避難支援者の安全確保',
      S6: '津波の早期到達（短い方の到達時間）',
      S7: '避難先に上がれない',
      S8: '聞き取りの中だけにある前提条件',
    },

    PREMISE_CODES: {
      SLEEP_UPSTAIRS: '2階で就寝している',
      CANNOT_HEAR: '耳が遠く、防災行政無線や呼びかけに気づけない',
      DEEP_SLEEP: '服薬などで眠りが深い',
      CANNOT_CLIMB: '避難先の階段を上がれない',
      NEEDS_AID: '一人では立ち上がれない・歩けない',
      ROAD_RISK: '避難経路にブロック塀などがあり、閉塞のおそれがある',
    },

    HOLE_TYPES: {
      NIGHT_PREP: '夜間の避難開始時間を考慮していない',
      SUPPORTER_ABSENT: 'その時間帯に避難支援者がいない',
      SHELTER_NOT_TSUNAMI: '避難先が津波の指定緊急避難場所ではない',
      TOO_FAR: '歩行速度では津波到達時間に間に合わない',
      SUPPORTER_AT_RISK: '避難支援者自身の避難が間に合わない',
      CANNOT_CLIMB: '避難先に上がれない',
      SLEEP_UPSTAIRS: '2階で就寝しており、下りるのに時間がかかる',
      CANNOT_HEAR: '呼びかけに気づけない',
      DEEP_SLEEP: '眠りが深く、気づくのが遅れる',
      NEEDS_AID: '一人では避難できないが、避難支援者がいない',
      SINGLE_ELDERLY_SUPPORTER: '避難支援者が1人だけで、その人も高齢',
      ROAD_BLOCK: '避難経路が閉塞すると間に合わない',
      VERTICAL_HOME: '津波浸水想定区域内の自宅2階などへの垂直避難を計画している',
      CAR_EVAC: '車での避難を計画している',
    },

    // 画面に出す改善案の種類。ここに無い種類は表示しない（S-03 の二重の防止）
    FIX_TYPES: {
      CHANGE_SHELTER: '避難先を変更する',
      ADD_CALLER: '声かけをする人を追加する',
      BEDSIDE_PREP: '枕元に靴・ライト・上着を用意する',
      SLEEP_NEAR_EXIT: '就寝場所を玄関の近くにする',
      CHANGE_ROUTE: '避難経路を変更する',
    },

    VERDICT_LABELS: {
      ok: '間に合う',
      tight: '余裕が少ない',
      short: '間に合わない',
      cannot_compute: '計算できない',
    },

    // 所要時間の内訳（タイムラインの区間）の表示名
    BREAKDOWN_LABELS: {
      leave: '避難を始めるまで',
      walk: '避難先まで歩く',
      climb: '避難先で上がる',
      help: '声をかけて、一緒に出る',
      supporter_travel: '避難支援者が駆けつける',
    },

    // 出典の種類ごとの表示名（画面のラベル）
    SOURCE_LABELS: {
      person: '本人の話',
      minsei: '民生委員の話',
      family: '家族の話',
      data: '国・県のデータ',
      tool: '計算ツール',
      assumption: '仮定',
      map_tap: '地図で指定',
      unknown: '出典なし',
    },
  };
})();
