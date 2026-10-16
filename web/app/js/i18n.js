// UI strings (English only). Spec field names are never translated.

const STRINGS = {
  en: {
    title: 'Booth Spec Editor',
    subtitle: 'Edit the spec directly: move, recolour, resize, duplicate. Everything runs in this browser; no file is uploaded.',
    open: 'Open spec', demo: 'Load demo', save: 'Save spec', glb: 'Export GLB', undo: 'Undo', redo: 'Redo',
    move: 'Move (W)', rotate: 'Rotate (E)', snap: 'Snap', match: 'Match source view', frame: 'Frame all', dup: 'Duplicate (Ctrl+D)',
    del: 'Delete (Del)', guide: 'Test guide', close: 'Close',
    outline: 'Parts', filter: 'Search id or role', ungrouped: 'Ungrouped', parts: '{n} parts',
    nothing: 'Click the model or the list. Shift/Ctrl+click adds, Alt+click picks a single part, empty space clears.',
    selected: '{n} parts selected', size3: 'Overall {w} × {d} × {h} m',
    id: 'id', kind: 'kind', role: 'role', scope: 'scope', group: 'group', selectGroup: 'Select whole group', ungroup: 'Remove from group',
    evObserved: 'observed', evInferred: 'inferred', edited: 'Edited in the web editor',
    location: 'Position (m)', rotation: 'Rotation (°)', params: 'Size and parameters', material: 'Material', color: 'Colour', alpha: 'Opacity',
    roughness: 'Roughness', metallic: 'Metallic', emission: 'Emission', optional: 'optional',
    groupMove: 'Move by (m)', groupYaw: 'Rotate about vertical axis (°)', groupScale: 'Scale all ×', apply: 'Apply', allColor: 'Colour for all', groupName: 'Group name',
    jsonParam: 'Coordinate list (JSON)',
    issues: 'Problems', noIssues: 'No problems', issueCount: '{n} problems',
    loaded: 'Loaded {name}: {n} parts', demoName: 'demo booth (synthetic)',
    saved: 'Saved {name}', exported: 'Exported {name} ({kb} KB)', refused: 'Refused: {msg}', footprint: 'Floor size changed; footprint is now {w} × {d} m',
    deleted: 'Deleted {n} parts', deletedExtra: '; also removed {r} requirements, {l} landmarks, {s} empty scopes', duplicated: 'Duplicated {n} parts (offset +0.6 m, drag to place)',
    confirmCritical: 'The selection includes critical parts ({ids}). Their requirements will be removed too. Delete anyway?',
    nothingToDo: 'Select some parts first', badFile: 'Cannot read: {msg}', serveHint: 'Cannot load the demo. Start the local server with python web/tools/serve.py instead of opening the html file directly.',
    matchFailed: 'Cannot match the source view: {msg}', matchOn: 'Matching the source view (drag the view to leave)', dirty: 'There are unsaved changes.',
    drop: 'Drop a spec.json here', groupInvalid: 'Group names use lowercase letters, digits and underscores, starting with a letter',
    guideTitle: 'Test guide',
    guideBody: [
      ['Start', 'The page should open on a synthetic demo booth (6 × 5 m, 79 parts). The status bar says "No problems". No red errors.'],
      ['Select', 'Click a chair: the whole chair (seat, back, four legs) highlights; Alt+click picks one part; click empty space to clear.'],
      ['Move', 'Select a table and drag an arrow: top, stem and base move together; with Snap on it steps 5 cm. Rotate (E) turns about the vertical axis.'],
      ['Recolour', 'Select one part and change its colour on the right; with several selected the colour applies to all.'],
      ['Resize', 'Edit sizes of a single part on the right; with a whole group use "Scale all ×", e.g. 1.2. Resizing the floor updates the footprint.'],
      ['Duplicate / delete', 'Ctrl+D copies a whole group under a new group name; Del deletes. Deleting a critical part (e.g. the back wall) asks first; the floor cannot be deleted.'],
      ['Undo', 'Ctrl+Z / Ctrl+Y step through drags, recolours, copies and deletions.'],
      ['Source view', '"Match source view" places the camera as the spec says; the dashed frame is the source picture. Dragging the view leaves it.'],
      ['Save', '"Save spec" downloads the edited spec.json; edited parts are marked inferred and say they were edited in the web editor.'],
      ['Check', 'Run validate_spec.py on the saved file, and boothctl.py glb (or "Export GLB" here) for a 3D file.'],
      ['Known limits', 'Grid patterns (LED seams) and text are shown schematically; there is no furniture library yet (copy existing pieces); orthographic cameras are not supported.'],
    ],
  },
};

export function t(key, params = {}) {
  const value = STRINGS.en[key] ?? key;
  if (typeof value !== 'string') return value;
  return value.replace(/\{(\w+)\}/g, (_, name) => (name in params ? params[name] : `{${name}}`)).replace(/\b1 parts\b/g, '1 part');
}
