// What the studio shows, decided from the job status the server reports. Pure functions (no DOM), so node can test them.

import { COPY, fill } from './studio_copy.js';

const M = COPY.main;

/** Seconds a step usually takes on this kind of machine; replaced by what this browser measured last time. */
export const DEFAULT_SECONDS = { scene: 90, compile: 6, preview: 15, build: 90, glb: 5 };
export const HD_MAX_PIXELS = 1_250_000;        // 2x of this is the 5 MP cap the engine enforces

const WORKING_TEXT = { scene: M.reading, compile: M.building, preview: M.checking, build: M.rendering, glb: M.exporting };
const STEP_ORDER = ['scene', 'compile', 'preview', 'build', 'glb'];

export const HD_FACTOR = 3;                      // a high-definition render takes about this many times longer

export function estimate(step, durations = {}, { hd = false } = {}) {
  const measured = durations?.[step];
  const base = Number.isFinite(measured) && measured > 0 ? measured : DEFAULT_SECONDS[step] ?? 30;
  return step === 'build' && hd ? base * HD_FACTOR : base;
}

export function formatElapsed(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** "a few seconds", "about 40 sec", "about 1–2 min" for an expected duration in seconds. */
export function expectedText(seconds) {
  if (seconds < 10) return M.secondsFew;
  if (seconds < 60) return fill(M.secondsAbout, { n: Math.round(seconds / 5) * 5 });
  const lo = Math.max(1, Math.round((seconds * 0.7) / 60));
  const hi = Math.max(lo, Math.round((seconds * 1.2) / 60));
  return lo === hi ? fill(M.minutesAbout, { n: lo }) : fill(M.minutesRange, { lo, hi });
}

/** The compile result in plain words, most important line first. Each line: { tone, text, look } where `look` names the
 *  Advanced view page that explains it. */
export function plainFit(detail) {
  if (!detail || !detail.fit) return [];
  const fit = detail.fit;
  const n = detail.components ?? 0;
  const lines = [];
  const off = fit.status === 'high_residual' || (typeof fit.rms_px === 'number' && fit.rms_px > 4);
  if (fit.status === 'ambiguous') lines.push({ tone: 'warn', text: M.sizeUnknown, look: 'data' });
  if (off) {                                              // both can be true: say both
    const worst = fit.worst_points?.[0];
    const px = Math.round(worst?.err_px ?? fit.max_px ?? fit.rms_px ?? 0);
    lines.push({ tone: 'warn', text: fill(M.fitOff, { px }), look: 'align' });
  } else if (fit.status === 'fitted') {
    lines.push({ tone: 'good', text: fill(M.fitGood, { n }) });
  } else if (fit.status !== 'ambiguous') {
    lines.push({ tone: 'info', text: fill(M.fitDone, { n }), look: 'align' });
  }
  if (detail.dropped?.length) lines.push({ tone: 'info', text: fill(M.dropped, { n: detail.dropped.length }), look: 'data' });
  if (detail.assumed_total) lines.push({ tone: 'info', text: fill(M.assumed, { n: detail.assumed_total }), look: 'data' });
  return lines;
}

const ERROR_WHY = { scene: M.errReadWhy, compile: M.errBuildWhy, preview: M.errCheckWhy, build: M.errRenderWhy, glb: M.errExportWhy };
const ERROR_TITLE = { scene: M.errReadTitle, compile: M.errBuildModelTitle, preview: M.errCheckTitle, build: M.errRenderTitle, glb: M.errExportTitle };

/** One failed step as a person should meet it: a title, the reason in plain words (the server's own text is kept apart as `raw`),
 *  one main action and optional second ones. Actions are names the page maps to behaviour:
 *  generate | compileAgain | quickCheck | render | exportGlb | moreWays | proData. */
export function describeError(step, entry) {
  const raw = String(entry?.message || '').trim();
  const d = entry?.detail || {};
  let reason = ERROR_WHY[step] || M.errRenderWhy;
  if (step === 'scene') {                                  // what the large model's service said, in plain words
    if (d.http === 401 || d.http === 403) reason = M.errKey;
    else if (d.http === 429) reason = M.errBusy;
    else if (d.timeout) reason = M.errSlow;
    else if (d.incomplete) reason = M.errCut;
    else if (d.refusal) reason = M.errRefused;
    else if (Array.isArray(d.errors) && d.errors.length) reason = M.errUnusable;
    else if (raw.startsWith('Cannot connect to')) reason = M.errNet;
  }
  const out = { step, title: ERROR_TITLE[step] || M.errRenderTitle, reason, raw: raw === reason ? '' : raw, primary: null, secondary: [] };
  if (step === 'scene') {
    out.primary = { action: 'generate', label: M.retry };
    out.secondary = [{ action: 'moreWays', label: reason === M.errKey ? M.changeKeyOrUpload : M.useUpload }];
  } else if (step === 'compile') {
    out.primary = { action: 'compileAgain', label: M.retry };                  // the picture is already read: no new reading, no consent needed
    out.secondary = [{ action: 'moreWays', label: M.useUpload }];
  } else if (step === 'preview') {
    out.primary = { action: 'quickCheck', label: M.retry };
  } else if (step === 'build') {
    out.primary = { action: 'render', label: M.retry };
    out.secondary = [{ action: 'proData', label: M.seeLog }];
  } else {
    out.primary = { action: 'exportGlb', label: M.retry };
    out.secondary = [{ action: 'proData', label: M.seeLog }];
  }
  return out;
}

const state = (job, step) => job.steps?.[step]?.state ?? 'idle';
const entry = (job, step) => job.steps?.[step] ?? {};
const isWorking = (s) => s === 'running' || s === 'queued';

/**
 * ctx: { health: {blender, openai: {configured, model}}, consent: boolean, durations: {step: seconds}, now: seconds since epoch }
 * Returns everything the page needs to draw: phase, the four step marks, the working/error/model card, downloads, compare sources.
 */
export function deriveView(job, ctx = {}) {
  const health = ctx.health || {};
  const blender = Boolean(health.blender);
  const keyConfigured = Boolean(health.openai?.configured);
  if (!job) {
    return { phase: 'empty', current: 1, bar: bar(['current', 'todo', 'todo', 'todo']), card: { kind: 'empty' }, blender, keyConfigured,
             compare: { live: false, check: null, final: null } };
  }
  const hasSpec = Boolean(job.files?.spec || job.spec?.origin);
  const steps = STEP_ORDER.map((name) => ({ name, state: state(job, name), entry: entry(job, name) }));
  const working = steps.find((s) => s.state === 'running');
  const queued = steps.filter((s) => s.state === 'queued');
  // Without a model a failed reading or build of the model is the card. With a model every failure is a notice on the model card:
  // the render button, the quick check and the downloads must stay reachable (a retry is the button itself).
  const failed = hasSpec ? null : steps.find((s) => s.state === 'error' && (s.name === 'scene' || s.name === 'compile'));
  const sideError = hasSpec ? steps.find((s) => s.state === 'error' && ['preview', 'build', 'glb'].includes(s.name)) : null;

  const readCompile = ['scene', 'compile'].map((n) => state(job, n));
  const markModel = isWorking(readCompile[0]) || isWorking(readCompile[1]) ? 'running'
    : hasSpec ? 'done'
    : readCompile.includes('error') ? 'error' : 'current';
  const buildState = state(job, 'build');
  const glbState = state(job, 'glb');
  const markRender = isWorking(buildState) || isWorking(glbState) ? 'running'
    : buildState === 'error' || glbState === 'error' ? 'error'
    : buildState === 'stale' ? 'stale'
    : buildState === 'done' ? 'done' : 'todo';
  const markAdjust = !hasSpec ? 'todo' : ['done', 'stale', 'running', 'queued'].includes(buildState) ? 'done' : 'current';
  const marks = ['done', markModel, markAdjust, markRender];
  const open = marks.findIndex((m) => ['current', 'running', 'error', 'stale'].includes(m));
  const current = open === -1 ? 4 : open + 1;

  const files = entry(job, 'build').detail?.files || {};
  const compileDetail = entry(job, 'compile').detail;
  const previewDetail = entry(job, 'preview').detail;
  const glbDetail = entry(job, 'glb').detail;
  const pixels = (job.image?.width ?? 0) * (job.image?.height ?? 0);

  let card;
  if (working || queued.length) {
    const s = working || queued[0];
    const expected = estimate(s.name, ctx.durations, { hd: Boolean(ctx.hd) });
    const elapsed = s.entry.started && ctx.now ? ctx.now - s.entry.started : 0;
    const waitingBlender = Boolean(working && String(working.entry.message || '').startsWith('Waiting for Blender'));
    card = {
      kind: 'working', step: s.name, text: waitingBlender ? M.waitingBlender : WORKING_TEXT[s.name] || M.rendering,
      elapsed: working ? elapsed : 0, expected, queued: !working || waitingBlender,
      slow: Boolean(working && !waitingBlender && elapsed > Math.max(20, expected * 1.6)),
      progress: working && !waitingBlender ? fill(M.progress, { elapsed: formatElapsed(elapsed), expected: expectedText(expected) }) : '',
    };
  } else if (failed) {
    card = { kind: 'error', ...describeError(failed.name, failed.entry), hasSpec, needKey: !keyConfigured, consent: Boolean(ctx.consent),
             canRetry: failed.name === 'compile' || (keyConfigured && Boolean(ctx.consent)) };
  } else if (!hasSpec) {
    card = { kind: 'generate', needKey: !keyConfigured, consent: Boolean(ctx.consent), canGenerate: keyConfigured && Boolean(ctx.consent),
             canGenerateAll: keyConfigured && Boolean(ctx.consent) && blender };
  } else {
    card = { kind: 'model', fit: plainFit(compileDetail), notice: sideError ? describeError(sideError.name, sideError.entry) : null };
  }

  const downloads = [];
  // A render of an earlier model is still a real file: offered, marked stale, never hidden (a render that finished while the person was
  // editing would otherwise be impossible to download).
  const built = ['done', 'stale'].includes(buildState);
  const staleBuild = buildState === 'stale';
  if (files.client && built) downloads.push({ key: 'client', label: M.dlClient, rel: files.client, primary: true, stale: staleBuild });
  if (files.client_hd && built) downloads.push({ key: 'client_hd', label: M.dlClientHd, rel: files.client_hd, stale: staleBuild });
  if (files.inspection && built) downloads.push({ key: 'inspection', label: M.dlOther, rel: files.inspection, stale: staleBuild });
  if (glbDetail?.glb && ['done', 'stale'].includes(glbState)) downloads.push({ key: 'glb', label: M.dlGlb, rel: glbDetail.glb, stale: glbState === 'stale' });
  if (files.blend && ['done', 'stale'].includes(buildState)) downloads.push({ key: 'blend', label: M.dlBlend, rel: files.blend, stale: buildState === 'stale' });

  return {
    phase: hasSpec ? 'model' : 'photo', current, bar: bar(marks), card, blender, keyConfigured,
    render: {
      can: blender && hasSpec, hdAllowed: blender && pixels > 0 && pixels <= HD_MAX_PIXELS,
      state: buildState, stale: buildState === 'stale',
      built: buildState === 'done', checkDone: state(job, 'preview') === 'done', checkStale: state(job, 'preview') === 'stale',
    },
    downloads, canExportGlb: hasSpec && !isWorking(glbState),
    compare: {
      live: hasSpec,
      check: state(job, 'preview') === 'done' && previewDetail?.render ? previewDetail.render : null,
      final: buildState === 'done' && files.client ? { wipe: files.client, overlay: files.reference || null } : null,
    },
    working: Boolean(working || queued.length),
  };
}

function bar(marks) {
  return marks.map((state, i) => ({ n: i + 1, label: M.stepNames[i], state }));
}
