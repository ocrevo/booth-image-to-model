// The words of the studio page, in one place. `main` is what a person sees on the main path: plain English, no engineering terms
// (a test scans it for BANNED). `advanced` is the Advanced view drawer and the folded "More ways" area, where the terms belong.

export const COPY = {
  main: {
    appName: 'Booth studio',
    stepNames: ['Choose picture', 'Make model', 'Adjust', 'Render'],
    drop: 'Drop a booth picture here, or click to choose',
    dropSmall: 'PNG or JPEG, up to 25 MB and 5 megapixels',
    recent: 'Recent jobs',
    newJob: 'Use another picture',
    emptyTitle: 'Start with a booth picture',
    emptyBody: 'Once you choose a picture, the large model looks at it and builds a 3D model you can edit.',
    tryDemo: 'Try the demo booth',
    tryDemoHint: 'Loads a synthetic booth picture and its model. No key needed. You can adjust it, compare it and render it right away.',
    demoPicture: 'Demo booth (synthetic data)',
    imageInfo: '{w} × {h} pixels',

    consent: 'I agree to send this picture to the configured large-model service ({model})',
    needKey: 'No large-model key is set yet. A pasted key is kept in this computer\'s memory only and is never written to a file.',
    keyPlaceholder: 'Paste API key',
    saveKey: 'Save key',
    generate: 'Generate',
    generateAll: 'Generate and render',
    generateHint: 'The large model reads the picture and builds the model, usually in 1–2 minutes.',
    retry: 'Try again',

    reading: 'Reading the picture…',
    building: 'Building the model…',
    checking: 'Running the quick check…',
    rendering: 'Rendering…',
    exporting: 'Exporting the model file…',
    waitingBlender: 'Waiting: another job is using Blender',
    progress: '{elapsed} elapsed, usually {expected}',
    slow: 'Taking longer than usual, still running',
    secondsFew: 'a few seconds',
    secondsAbout: 'about {n} sec',
    minutesAbout: 'about {n} min',
    minutesRange: 'about {lo}–{hi} min',

    fitGood: 'Model ready, {n} parts',
    fitDone: 'Model ready, {n} parts. Compare it with the picture to check.',
    fitOff: 'The model may not line up with the picture (worst spot is off by {px} pixels) · see where',
    sizeUnknown: 'Some sizes cannot be told from the picture. Please check them · view',
    gateWhyChecks: 'The corners marked on the picture do not agree with each other well enough, so the model may be off · see where',
    gateWhyOutlier: 'One or more corners marked on the picture may be in the wrong place · see where',
    gateWhyScale: 'A table, counter or chair in the picture comes out at an unusual height, so the overall size may be off · see why',
    gateWhyNoCheck: 'Too few corners could be marked on the picture to double-check the model · see why',
    gateWhyTopologyType: 'The booth type does not match the open sides, so one of them was probably read wrongly · see why',
    gateWhyWallOpen: 'A wall was placed on an open side and left out of the model · see why',
    gateWhyMirrored: 'The picture seems to be read back to front or left to right, so the model may be mirrored · see why',
    gateWhyGeneric: 'The automatic check could not confirm that the model matches the picture · see why',
    gateHeldTitle: 'Rendering is paused',
    gateHeld: 'The notes above say the model may not match the picture well, so a render could come out wrong. You can render anyway, or start again with another picture or your own file.',
    renderAnyway: 'Render anyway',
    startAgain: 'Start again',
    dropped: '{n} parts could not be added to the model · see why',
    assumed: '{n} sizes are assumed values · view',
    modelReady: 'You can now click, drag and recolour parts right in the view.',
    nextRender: 'Happy with it? Render it.',

    render: 'Render (1–3 minutes)',
    renderAgain: 'Render again',
    quickCheck: 'Quick check first (about 15 sec)',
    hd: 'High definition (slower)',
    stale: 'The model was changed after the last render',
    noBlender: 'Blender was not found on this computer: you can export the model file, but not render.',
    exportGlb: 'Export model file',

    downloads: 'Downloads',
    dlClient: 'Client picture (PNG)',
    dlClientHd: 'Client picture (HD)',
    dlOther: 'Another angle',
    dlGlb: 'Model file',
    dlBlend: 'Blender file (.blend)',
    staleTag: 'Out of date',
    openBlender: 'Open in Blender',
    openedBlender: 'Asked Blender to open this file. The window may take a few seconds to appear.',

    compareName: ['Picture', 'Compare', 'Overlay', 'Model'],
    afterName: ['Live model', 'Quick check', 'Final render'],
    liveNote: 'Live view: materials are approximate, lighting follows the final render',
    renderNote: 'Viewing a render: go back to "Live model" to keep editing',
    holdSpace: 'Hold Space to peek at the picture',
    toLive: 'Back to live model',

    savedChip: 'Saved ✓',
    savingChip: 'Saving…',
    waitChip: 'Saved after the render finishes',
    failChip: 'Not saved',
    saveFailed: 'This change did not pass the check: {msg}',
    undoStep: 'Undo this step',

    offline: 'Cannot reach the local server. Start it again with python web/server/run.py and this page reconnects by itself.',
    pro: 'Advanced view',
    tools: { move: 'Move', rotate: 'Rotate', snap: 'Snap', dup: 'Duplicate', del: 'Delete', undo: 'Undo', redo: 'Redo' },
    parts: 'Parts',
    filter: 'Search',

    errReadTitle: 'The large model could not read this picture',
    errBuildModelTitle: 'Could not build the model',
    errCheckTitle: 'The quick check did not finish',
    errRenderTitle: 'The render did not finish',
    errExportTitle: 'The model file was not exported',
    useUpload: 'Upload a file instead',
    changeKeyOrUpload: 'Change the key, or upload a file instead',
    seeLog: 'See the detailed log',
    rawLog: 'Detailed log',
    errKey: 'The large-model service does not accept this key. Try another key, or upload a file instead.',
    errBusy: 'The large-model service is too busy, or the quota is used up. Wait a moment and try again.',
    errSlow: 'The large model took too long to answer and was not retried automatically (a retry would be billed again). You can try again.',
    errCut: 'The large model\'s answer was cut off. Try again, or use a model that can write longer answers.',
    errRefused: 'The large model refused this request. Try again, or upload a file instead.',
    errUnusable: 'The large model revised its answer twice and the result is still unusable. Try again, or upload a file instead.',
    errNet: 'Cannot connect to the large-model service. Check the network or the service address and try again.',
    errReadWhy: 'The large model did not give a usable result. Try again, or upload a file instead.',
    errBuildWhy: 'What was read from the picture cannot be made into a sensible model. Try again, or upload a file instead.',
    errCheckWhy: 'The quick check render did not succeed. Try again.',
    errRenderWhy: 'The render did not succeed. Try again; the detailed log has the reason.',
    errExportWhy: 'The model file was not exported. Try again.',
    discardEdits: 'Some changes are not saved yet and will be lost if you leave now. Leave anyway?',
    offlineChip: 'Cannot reach the server, will save when it is back',
    needConsent: 'Tick the agreement first to try again',
    modelLoaded: 'Model loaded, {n} parts',
    jobNotFound: 'Job {id} was not found',
    imageTooBig: 'The picture is over {mb} MB. Please make it smaller first.',
  },
  advanced: {
    moreWays: 'More ways',
    changeKey: 'Change the large-model key (kept in this computer\'s memory only)',
    uploadScene: 'Upload a scene description (scene.json)',
    uploadSpec: 'Upload an existing model (spec.json, must be made from this picture)',
    uploaded: 'Loaded {name} ({n} parts)',
    uploadedSpec: 'Loaded {name} ({n} parts); reading and compiling skipped',
    uploadRefused: 'Did not load {name}: {msg}',
    proTabs: ['Alignment', 'Data and log'],
    overlayTitle: 'Fitted booth outline over the picture (0.5 m grid)',
    fitLine: 'Fit {status}, residual {rms} px (max {max} px), {n} parts',
    gateTitle: 'Quality check of the corner fit and the booth layout',
    gateLine: 'Verdict: {status}',
    topologyLine: 'Booth layout read from the picture: type {type}, open sides {open}, cutaway sides {cut}',
    topologyNotStated: 'Booth layout: the reading did not state a type or the open sides, so the layout checks did not run.',
    topologyNoSides: 'Booth layout: the reading said type {type} but did not state the open sides, so the layout checks did not run.',
    topologyChecks: 'Layout checks: {list}',
    topologyCheckNote: 'Layout check {id} ({status}): {message}',
    topologySidesNone: 'none',
    topologySidesUnknown: 'not stated',
    gateNone: 'The engine reported no quality check for this model.',
    checkLine: '{n} held-out corners (projected, not used in the fit): median error {median} px, max {max} px',
    looLine: 'Leave-one-out error (each corner predicted without it): median {median} px, max {max} px',
    looUnreliableLine: 'Leave-one-out was not used: with this few corners it cannot tell reliably which click is wrong, so no corner is named.',
    noCheckLine: 'No independent check was available (no held-out corners, too few corners to leave one out reliably).',
    outliersLine: 'Corners the others disagree with: {ids}',
    scaleLine: 'Scale check {id}: measured {measured} m, expected {expected} m (ratio {ratio}), {verdict}',
    scaleOk: 'within tolerance',
    scaleOff: 'OUTSIDE tolerance: the assumed dimension may be wrong',
    scaleErr: 'could not be measured',
    pointsTitle: 'Corner error (px)',
    pointsHead: ['Corner', 'Error', 'Error when predicted from the others'],
    checkTag: 'held out',
    droppedTitle: 'Parts that could not be added to the model',
    assumedTitle: 'Assumed values',
    adjustTitle: 'Automatic adjustments',
    warnTitle: 'Warnings',
    designTitle: 'Design check notes (advisory)',
    logTitle: 'Step log',
    sheetTitle: 'Four-panel check sheet (picture / render / overlay / outline and corners)',
    files: 'Files',
    noDetail: 'No data yet',
  },
};

/** Engineering words that must not appear on the main path of the studio. Matched as whole words, ignoring case. */
export const BANNED = [
  'spec', 'JSON', 'GLB', 'scene', 'scene description', 'compile', 'compiling', 'schema', 'sha256',
  'fit', 'fitted', 'fitting', 'camera fit', 'residual', 'RMS', 'px', 'reprojection', 'Workbench', 'Cycles',
];

const escapeRe = (word) => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const BANNED_RE = BANNED.map((word) => new RegExp(`(?<![a-z0-9])${escapeRe(word)}(?:s|es)?(?![a-z0-9])`, 'i'));

/** The banned words found in a text (whole words, any case; {placeholders} are not text): [] when it is plain enough for the main path. */
export function findBanned(text) {
  const plain = String(text).replace(/\{\w+\}/g, ' ');
  return BANNED.filter((_, i) => BANNED_RE[i].test(plain));
}

export function fill(text, params = {}) {
  return String(text).replace(/\{(\w+)\}/g, (_, name) => (name in params ? params[name] : `{${name}}`)).replace(/\b1 parts\b/g, '1 part');
}

/** Every string under `main`, for the banned-word test. */
export function* mainStrings(node = COPY.main) {
  if (typeof node === 'string') yield node;
  else if (Array.isArray(node)) for (const v of node) yield* mainStrings(v);
  else if (node && typeof node === 'object') for (const v of Object.values(node)) yield* mainStrings(v);
}
