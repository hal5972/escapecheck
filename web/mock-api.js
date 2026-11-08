/*
 * サーバーに接続できないときの代わり。web/mock/ のサンプルデータ（架空の人物）の JSON で、
 * サーバーの API と同じ形で答える。数値はすべて JSON のサンプルの値で、この中では計算しない。
 *
 * - EC.mock.samples: サンプルデータの一覧と、計画の作成（話す手順から、途中から）。サーバーがあるときも、サンプルの計画の作成に使う
 * - EC.mock.api: サーバーの代わりの API（interview・check・jobs・approve・verify・finalize・facts）
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});
  const { sleep, uid, pseudonymize, formatClock } = EC.u;
  const L = EC.L;

  const clone = (x) => JSON.parse(JSON.stringify(x));
  const cache = {};

  async function loadJson(path) {
    if (!cache[path]) {
      const res = await fetch(path, { cache: 'no-store' });
      if (!res.ok) throw new Error(`サンプルデータを読み込めません: ${path}`);
      cache[path] = await res.json();
    }
    return clone(cache[path]);
  }

  // ---------------------------------------------------------------------------
  // 計画の作成
  // ---------------------------------------------------------------------------

  function emptyFields() {
    const fields = {};
    for (const key of Object.keys(L.FIELDS)) fields[key] = { value: null, status: 'unconfirmed' };
    return fields;
  }

  function newPlan(alias, areaLabel, home, planId) {
    return {
      schema_version: '1',
      plan_id: planId || uid('plan'),
      alias,
      area_label: areaLabel,
      home: home ? { ...home } : undefined,
      fields: emptyFields(),
      supporters: [],
      premises: [],
      findings: [],
      fixes: [],
      approvals: [],
      issues: [],
      sources: {},
      status: 'draft',
    };
  }

  function sampleIdOf(plan) {
    const m = /^plan_sample_([a-z]+)_/.exec((plan && plan.plan_id) || '');
    return m ? m[1] : 'yamada'; // 「新しい人」は、山田さんの台本で動かす
  }

  /** 新しい人のときは、サンプルの自宅からの差の分だけ、避難先などの位置をずらす。 */
  function shiftFor(plan, sample) {
    if (!plan.home || !sample.home) return { dlat: 0, dlon: 0 };
    return { dlat: plan.home.lat - sample.home.lat, dlon: plan.home.lon - sample.home.lon };
  }
  const shiftPoint = (p, s) => (p ? { lat: p.lat + s.dlat, lon: p.lon + s.dlon } : p);

  /** 発言を 1 つ、台本の順に計画へ入れる。 */
  function applyStep(plan, sample, index, utterance, speaker) {
    const step = sample.interview[index];
    const pseudo = pseudonymize(utterance, sample.names);
    const sid = `src_u${index + 1}`;
    plan.sources[sid] = {
      kind: 'utterance',
      label: `${speaker === 'person' ? '本人' : speaker === 'family' ? '家族' : '民生委員'}の発言（${formatClock()}）`,
      text: pseudo,
      speaker,
      at: new Date().toISOString(),
    };
    const changed = [];
    if (!step) {
      // 台本の外の発言は、留意事項に控えるだけにする
      const prev = plan.fields.notes.status === 'filled' ? `${plan.fields.notes.value}。` : '';
      plan.fields.notes = { value: `${prev}${pseudo.slice(0, 60)}`, status: 'filled', source_id: sid, quote: pseudo.slice(0, 60) };
      changed.push('notes');
      return { changed, next: null };
    }
    for (const [key, f] of Object.entries(step.fields)) {
      const value = clone(f.value);
      const entry = { value, status: 'filled', source_id: f.source_id || sid };
      if (f.quote !== undefined) entry.quote = pseudo.includes(f.quote) ? f.quote : pseudo.slice(0, 60);
      plan.fields[key] = entry;
      if (f.source_id && sample.sources[f.source_id]) plan.sources[f.source_id] = clone(sample.sources[f.source_id]);
      changed.push(key);
    }
    if (step.with_supporters) {
      const s = shiftFor(plan, sample);
      plan.supporters = clone(sample.supporters).map((sup) => ({ ...sup, location: shiftPoint(sup.location, s) }));
    }
    return { changed, next: step.next_question === undefined ? null : step.next_question };
  }

  function mergeStaticSources(plan, sample) {
    for (const [id, src] of Object.entries(sample.sources)) if (!plan.sources[id]) plan.sources[id] = clone(src);
  }

  function applyCheck(plan, sample) {
    mergeStaticSources(plan, sample);
    plan.premises = clone(sample.check.premises);
    plan.findings = clone(sample.check.findings);
    plan.fixes = clone(sample.check.fixes);
    plan.status = 'checked';
  }

  /** 承認済みの改善案の結果を、点検の結果に反映する（再点検の代わり）。 */
  function applyApprovals(plan, sample) {
    const approved = new Set(plan.approvals.map((a) => a.fix_id));
    const originals = clone(sample.check.findings);
    plan.findings = originals.map((finding) => {
      let result = finding.result;
      for (const fix of plan.fixes) {
        if (!approved.has(fix.fix_id)) continue;
        if (fix.finding_id === finding.finding_id) result = clone(fix.result);
        else if (fix.mock_also && fix.mock_also[finding.scenario_id]) result = clone(fix.mock_also[finding.scenario_id]);
      }
      const next = { ...finding, result };
      // 結果が変わったら、説明文は現在の結果の言い方に合わせて短くする（サンプルの文）
      if (result !== finding.result) {
        const v = result.verdict;
        next.explanation = v === 'ok'
          ? `改善案を反映したので、間に合います。${result.margin_min}分の余裕があります。`
          : `改善案を反映しましたが、${v === 'short' ? `まだ${-result.margin_min}分足りません。` : `余裕は${result.margin_min}分です。`}`;
        next.explanation_template = next.explanation;
      }
      return next;
    });
  }

  function applyFixToPlan(plan, sample, fix) {
    const sid = `src_fix_${fix.fix_id}`;
    plan.sources[sid] = {
      kind: 'tool',
      label: `改善案（${L.FIX_TYPES[fix.fix_type] || fix.fix_type}）を、民生委員が承認した`,
      value: L.FIX_TYPES[fix.fix_type] || fix.fix_type,
      basis: '承認の前に、計画は変更しない',
    };
    if (fix.fix_type === 'CHANGE_SHELTER') {
      const sh = sample.shelters.find((s) => s.shelter_id === fix.target_id);
      if (sh) {
        plan.fields.shelter = {
          value: { shelter_id: sh.shelter_id, name: sh.name, kind: sh.kind, distance_m: sh.distance_m, distance_method: sh.distance_method },
          status: 'filled',
          source_id: sh.source_id,
        };
        plan.fields.route = { value: `自宅から${sh.name}まで（直線距離×1.3の概算で約${Math.round(sh.distance_m / 10) * 10}m）`, status: 'filled', source_id: sid };
        if (sample.sources[sh.source_id]) plan.sources[sh.source_id] = clone(sample.sources[sh.source_id]);
      }
    } else if (fix.fix_type === 'BEDSIDE_PREP' || fix.fix_type === 'SLEEP_NEAR_EXIT') {
      const add = fix.fix_type === 'BEDSIDE_PREP' ? '枕元に靴・ライト・上着を用意する' : '寝る場所を玄関の近くにする';
      const prev = plan.fields.notes.status === 'filled' ? `${plan.fields.notes.value}。` : '';
      plan.fields.notes = { value: `${prev}${add}`, status: 'filled', source_id: sid };
    } else if (fix.fix_type === 'ADD_CALLER' && fix.mock_supporter) {
      plan.supporters.push({ ...clone(fix.mock_supporter), source_id: sid });
    }
  }

  // ---------------------------------------------------------------------------
  // サーバーのサンプルデータ（data/samples/*.json。架空の人物）から、計画を作る
  // 形: { case_id, title, alias, area_label, home, talk: [{ id, speaker, text }],
  //       form: { 欄: { value, from, quote } }, supporters: [{ ..., from }] }
  // ---------------------------------------------------------------------------

  // 名前ではなく、続柄・関係を表す言葉（「息子さん」「隣のさん」など）。仮名に置き換えない
  const KINSHIP = new Set(['息子', '娘', '孫', '主人', '奥', '妻', '夫', '隣', '近所', '兄', '姉', '弟', '妹', '母', '父', '嫁', '婿', '長男', '長女', '次男', '次女', '友達', '先生']);

  function deriveNames(c) {
    const names = [];
    const talk = c.talk || [];
    const first = talk[0] ? String(talk[0].text) : '';
    const head = /^([一-龥]{2,3})さん/.exec(first);
    const display = c.person && c.person.display_name ? String(c.person.display_name) : '';
    const personReal = (head && head[1]) || (display ? display.slice(0, 2) : '');
    if (personReal) names.push({ alias: c.alias || 'Aさん', real: personReal, call: `${personReal}さん`, full: display || personReal, role: 'person' });
    for (const s of c.supporters || []) {
      const t = talk.find((x) => x.id === s.from);
      if (!t) continue;
      const re = /([一-龥]{2,3})さん/g;
      let m;
      while ((m = re.exec(String(t.text)))) {
        if (KINSHIP.has(m[1]) || m[1] === personReal) continue;
        names.push({ alias: s.alias || 'Bさん', real: m[1], call: `${m[1]}さん`, full: m[1], role: 'supporter' });
        break;
      }
    }
    return names;
  }

  function caseTitle(c) {
    const m = /^([^（(:：]+)/.exec(String(c.title || c.case_id || ''));
    return (m ? m[1] : String(c.title || c.case_id || '')).trim();
  }

  function caseSummary(c) {
    const m = /^[^（(:：]+(?:[（(]([^）)]*)[）)])?\s*[:：]?\s*(.*)$/.exec(String(c.title || ''));
    const rest = m ? m[2].trim() : '';
    const paren = m && m[1] ? m[1].replace(/架空[、,]?\s*/, '').trim() : '';
    const text = `${rest}${paren ? `（${paren}）` : ''}`;
    return text || (c.person && c.person.description) || '';
  }

  function pseudoValue(value, names) {
    // 「隣の西村さん（Bさん）」のように、仮名がすでに付いているときは、仮名を重ねない
    if (typeof value === 'string') return pseudonymize(value, names).replace(/([A-Z]さん)（\1）/g, '$1');
    if (Array.isArray(value)) return value.map((v) => pseudoValue(v, names));
    if (value && typeof value === 'object') {
      const out = {};
      for (const [k, v] of Object.entries(value)) out[k] = pseudoValue(v, names);
      return out;
    }
    return value;
  }

  function fromCase(c, stage) {
    const id = String(c.case_id || 'sample').replace(/[^A-Za-z0-9_-]/g, '') || 'sample';
    const plan = newPlan(c.alias || 'Aさん', c.area_label || '', c.home, `plan_sample_${id}_${uid('p').slice(2)}`);
    plan.sources.src_home = { kind: 'map_tap', label: '自宅の場所' };
    const names = deriveNames(c);
    const talk = c.talk || [];
    const person = names.find((n) => n.role === 'person');
    const ask = `${(person && person.call) || caseTitle(c)}について、思いつくままに話してください。`;
    const record = {
      id: uid('rec'), sample: id, title: caseTitle(c), summary: caseSummary(c), names,
      initialQuestion: ask, nextQuestion: ask, examples: talk.map((t) => t.text), plan, versions: [], stage: 'start',
    };
    if (stage === 'start') return record;

    // 途中から: 聞き取りが終わった計画。発言は出典として登録し、欄の値は、発言の引用つきで入れる
    const at = new Date().toISOString();
    for (const t of talk) {
      const who = t.speaker === 'person' ? '本人' : t.speaker === 'family' ? '家族' : '民生委員';
      plan.sources[`src_${t.id}`] = { kind: 'utterance', label: `${who}の発言（${formatClock()}）`, text: pseudonymize(t.text, names), speaker: t.speaker || 'minsei', at };
    }
    for (const [key, f] of Object.entries(c.form || {})) {
      if (!(key in plan.fields)) continue;
      const sid = f.source_id || (f.from ? `src_${f.from}` : null);
      if (!sid || !plan.sources[sid]) continue;
      const entry = { value: pseudoValue(f.value, names), status: 'filled', source_id: sid };
      if (f.quote) entry.quote = pseudonymize(f.quote, names);
      plan.fields[key] = entry;
    }
    plan.supporters = (c.supporters || []).map((s) => {
      const { from, ...rest } = s;
      return { ...rest, source_id: from ? `src_${from}` : undefined };
    });
    record.stage = 'talked';
    record.nextQuestion = null;
    return record;
  }

  // ---------------------------------------------------------------------------
  // サンプルデータ
  // ---------------------------------------------------------------------------

  const samples = {
    /** サーバーにサンプルデータがあれば、それを出す。無ければ（サーバーが無いときを含む）、web/mock/ の JSON を出す。 */
    async list() {
      if (EC.api.getMode() === 'server') {
        const ids = await EC.api.listSamples();
        const items = [];
        for (const entry of ids) {
          const c = await EC.api.getSample(entry.id);
          if (c && c.talk && c.form) items.push({ id: entry.id, title: caseTitle(c), summary: caseSummary(c) });
        }
        if (items.length) return items;
      }
      const index = await loadJson('mock/index.json');
      return index.samples;
    },

    /** stage: start（話す手順から）／talked（聞き取りのあと）／checked（点検のあと）／improved（改善のあと）／verified */
    async build(id, stage) {
      if (EC.api.getMode() === 'server') {
        // サーバーのサンプル（点検は、サーバーが実際に行うので、途中の状態は「聞き取りのあと」まで）
        const c = await EC.api.getSample(id);
        if (c && c.talk && c.form) return fromCase(c, stage === 'start' ? 'start' : 'talked');
      }
      const sample = await loadJson(`mock/${id}.json`);
      const plan = newPlan('Aさん', sample.area_label, sample.home, `plan_sample_${id}_${uid('p').slice(2)}`);
      const record = {
        id: uid('rec'),
        sample: id,
        title: sample.title,
        summary: sample.summary,
        names: clone(sample.names),
        initialQuestion: sample.initial_question,
        examples: sample.interview.map((s) => s.utterance),
        plan,
        versions: [],
        stage: 'start',
      };
      plan.sources.src_home = { kind: 'map_tap', label: '自宅の場所' };
      if (stage === 'start') {
        record.nextQuestion = sample.initial_question;
        return record;
      }

      sample.interview.forEach((step, i) => applyStep(plan, sample, i, step.utterance, 'minsei'));
      record.stage = 'talked';
      record.nextQuestion = null;
      if (stage === 'talked') return record;

      applyCheck(plan, sample);
      record.versions.push({ label: '点検', at: new Date().toISOString(), plan: clone(plan) });
      record.stage = 'checked';
      if (stage === 'checked') return record;

      // 主な改善案（mock_primary）。無ければ、先頭の案だけ
      const primary = plan.fixes.filter((f) => f.mock_primary);
      const chosen = primary.length ? primary : plan.fixes.slice(0, 1);
      if (!chosen.length) return record; // 改善案が無い計画（竹村さん）は、点検のままにする
      for (const fix of chosen) {
        plan.approvals.push({ fix_id: fix.fix_id, by: 'minsei', at: new Date().toISOString() });
        applyFixToPlan(plan, sample, fix);
      }
      applyApprovals(plan, sample);
      record.versions.push({ label: '改善後', at: new Date().toISOString(), plan: clone(plan) });
      record.stage = 'improved';
      return record;
    },
  };

  // ---------------------------------------------------------------------------
  // サーバーの代わりの API
  // ---------------------------------------------------------------------------

  const jobs = new Map();
  const STEP_MS = 900;

  async function sampleOf(plan) {
    return loadJson(`mock/${sampleIdOf(plan)}.json`);
  }

  const api = {
    async login(passphrase) {
      await sleep(250);
      if (!passphrase) {
        const err = new Error('合言葉を入力してください');
        err.status = 401;
        throw err;
      }
      return { token: 'mock-token' };
    },

    async interview(plan, utterance, speaker) {
      await sleep(1100);
      const sample = await sampleOf(plan);
      const next = clone(plan);
      const index = Object.keys(next.sources).filter((k) => /^src_u\d+$/.test(k)).length;
      const { changed, next: question } = applyStep(next, sample, index, utterance, speaker || 'minsei');
      return { plan: next, next_question: question, changed_fields: changed };
    },

    async startCheck(plan) {
      await sleep(300);
      const sample = await sampleOf(plan);
      const recheck = plan.findings.length > 0 && plan.approvals.length > 0;
      const id = uid('job');
      jobs.set(id, { sample, plan: clone(plan), start: Date.now(), events: recheck ? sample.check.recheck_events : sample.check.events, recheck });
      return id;
    },

    async getJob(id) {
      await sleep(120);
      const job = jobs.get(id);
      if (!job) throw Object.assign(new Error('点検の記録が見つかりません'), { status: 404 });
      const elapsed = Date.now() - job.start;
      const total = job.events.length * STEP_MS;
      const shown = Math.min(job.events.length, Math.floor(elapsed / STEP_MS) + 1);
      const status = { job_id: id, state: elapsed >= total ? 'done' : 'running', events: job.events.slice(0, shown), plan: null, error: null };
      if (status.state === 'done') {
        const plan = clone(job.plan);
        if (job.recheck) {
          applyApprovals(plan, job.sample);
        } else {
          applyCheck(plan, job.sample);
        }
        status.plan = plan;
      }
      return status;
    },

    async approve(plan, fixId, by) {
      await sleep(500);
      const sample = await sampleOf(plan);
      const next = clone(plan);
      const fix = next.fixes.find((f) => f.fix_id === fixId);
      if (!fix) throw Object.assign(new Error('改善案が見つかりません'), { status: 404 });
      if (!next.approvals.some((a) => a.fix_id === fixId)) next.approvals.push({ fix_id: fixId, by: by || 'minsei', at: new Date().toISOString() });
      applyFixToPlan(next, sample, fix);
      // 同じ種類・同じ対象の案は、反映したので、ほかの問題点の案からも外す（本物は、再点検で案を作り直す）
      next.fixes = next.fixes.filter((f) => f.fix_id === fixId || !(f.fix_type === fix.fix_type && f.target_id === fix.target_id));
      // 承認の直後に、コードが計算し直した結果（サンプルの値）。サーバーの approve と同じく、シナリオの ID → 結果
      const after = clone(next);
      applyApprovals(after, sample);
      const results = {};
      for (const f of after.findings) results[f.scenario_id] = f.result;
      return { plan: next, results };
    },

    async verify(plan) {
      await sleep(900);
      const sample = await sampleOf(plan);
      const known = new Map((plan.issues || []).map((i) => [i.issue_id, i]));
      return sample.verify.issues
        .filter((i) => !i.field || (plan.fields[i.field] && plan.fields[i.field].status === 'unconfirmed') || known.has(i.issue_id))
        .map((i) => ({ ...i, acknowledged: known.has(i.issue_id) ? known.get(i.issue_id).acknowledged : i.acknowledged }));
    },

    async finalize(plan) {
      await sleep(300);
      const open = (plan.issues || []).filter((i) => !i.acknowledged);
      if (open.length) return { ok: false, reasons: open.map((i) => i.message), plan };
      return { ok: true, reasons: [], plan: { ...clone(plan), status: 'final' } };
    },

    /** 記録（サンプルの値）。サーバーの /api/audit と同じ形。 */
    async audit() {
      await sleep(300);
      const data = await loadJson('mock/audit.json');
      const now = Date.now();
      return data.entries.map((e) => {
        const { ago_sec: ago, ...rest } = e;
        // サーバーと同じ形: args は短くした JSON の文字
        return { ...rest, args: typeof rest.args === 'string' ? rest.args : JSON.stringify(rest.args || {}), at: new Date(now - ago * 1000).toISOString() };
      });
    },

    async facts(plan) {
      await sleep(400);
      const sample = await sampleOf(plan);
      const s = shiftFor(plan, sample);
      return {
        facts: sample.facts,
        shelters: sample.shelters.map((sh) => ({ ...sh, point: shiftPoint(sh.point, s) })),
        sources: sample.sources,
      };
    },
  };

  EC.mock = { samples, api, newPlan };
})();
