// Thin wrapper over fetch. Every call returns parsed JSON or throws an ApiError
// carrying the server's own message, so views never invent their own wording for
// a failure the backend already explained.

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

async function request(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(path, options);
  } catch (cause) {
    throw new ApiError('Cannot reach the server. Is it still running?', 0, cause);
  }

  if (response.status === 204) return null;

  const text = await response.text();
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = text; }
  }

  if (!response.ok) {
    throw new ApiError(describe(payload, response.status), response.status, payload);
  }
  return payload;
}

// FastAPI's `detail` is a string, a list of strings, or a list of validation
// objects depending on how the error was raised. Flatten all three.
function describe(payload, status) {
  const detail = payload && payload.detail !== undefined ? payload.detail : payload;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (typeof d === 'string' ? d : d.msg || JSON.stringify(d)))
      .join('; ');
  }
  return `Request failed (${status})`;
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, body),
  put: (path, body) => request('PUT', path, body),
  del: (path) => request('DELETE', path),

  health: () => request('GET', '/api/health'),

  projects: {
    list: () => request('GET', '/api/projects'),
    create: (body) => request('POST', '/api/projects', body),
    read: (id) => request('GET', `/api/projects/${id}`),
    update: (id, body) => request('PUT', `/api/projects/${id}`, body),
    remove: (id) => request('DELETE', `/api/projects/${id}`),
    archived: (id) => request('GET', `/api/projects/${id}/archived`),

    addBuilding: (id, body) => request('POST', `/api/projects/${id}/buildings`, body),
    updateBuilding: (id, bid, body) =>
      request('PUT', `/api/projects/${id}/buildings/${bid}`, body),
    deleteBuilding: (id, bid) => request('DELETE', `/api/projects/${id}/buildings/${bid}`),
    cloneCandidates: (id, bid) =>
      request('GET', `/api/projects/${id}/buildings/${bid}/clone-candidates`),
    cloneBuilding: (id, bid, body) =>
      request('POST', `/api/projects/${id}/buildings/${bid}/clone`, body),
    renameBuilding: (id, bid, body) =>
      request('POST', `/api/projects/${id}/buildings/${bid}/rename`, body),

    addSchedule: (id, bid, code) =>
      request('POST', `/api/projects/${id}/buildings/${bid}/schedules`, { code }),
    archiveSchedule: (id, sid) => request('DELETE', `/api/projects/${id}/schedules/${sid}`),
    restoreSchedule: (id, sid) =>
      request('POST', `/api/projects/${id}/schedules/${sid}/restore`),

    renumber: (id, bid, body) =>
      request('POST', `/api/projects/${id}/buildings/${bid}/renumber`, body),
    audit: (id, bid) => request('GET', `/api/projects/${id}/buildings/${bid}/audit`),

    bulkRevision: (id, body) => request('POST', `/api/projects/${id}/revisions/bulk`, body),
    branding: (id) => request('GET', `/api/projects/${id}/branding`),
    setBranding: (id, body) => request('PUT', `/api/projects/${id}/branding`, body),
    columns: (id, code) => request('GET', `/api/projects/${id}/columns/${code}`),
    setColumns: (id, body) => request('PUT', `/api/projects/${id}/columns`, body),
  },

  schedules: {
    grid: (id) => request('GET', `/api/schedules/${id}`),
    addRow: (id, values) => request('POST', `/api/schedules/${id}/rows`, { values }),
    addRows: (id, count) => request('POST', `/api/schedules/${id}/rows/many`, { count }),
    updateRow: (id, rowId, values, overrides) =>
      request('PUT', `/api/schedules/${id}/rows/${rowId}`, { values, overrides }),
    deleteRow: (id, rowId) => request('DELETE', `/api/schedules/${id}/rows/${rowId}`),
    deleteRows: (id, rowIds) =>
      request('POST', `/api/schedules/${id}/rows/delete`, { row_ids: rowIds }),
    duplicateRow: (id, rowId) =>
      request('POST', `/api/schedules/${id}/rows/${rowId}/duplicate`),
    editCells: (id, edits, action = 'cells') =>
      request('POST', `/api/schedules/${id}/rows/cells`, { edits, action }),
    paste: (id, body) => request('POST', `/api/schedules/${id}/rows/paste`, body),
    pastePreview: (id, body) =>
      request('POST', `/api/schedules/${id}/rows/paste/preview`, body),
    fill: (id, body) => request('POST', `/api/schedules/${id}/rows/fill`, body),
    columns: (id) => request('GET', `/api/schedules/${id}/columns`),

    // The working file: the typed columns only, out and back.
    rowsUrl: (id, filled = true) =>
      `/api/schedules/${id}/rows.xlsx${filled ? '' : '?filled=false'}`,
    importRows: async (id, file, { mode = 'append', apply = false, confirm = false } = {}) => {
      const form = new FormData();
      form.append('file', file);
      form.append('mode', mode);
      form.append('apply', apply ? 'true' : 'false');
      form.append('confirm', confirm ? 'true' : 'false');
      const response = await fetch(`/api/schedules/${id}/rows/workbook`, {
        method: 'POST', body: form,
      });
      const text = await response.text();
      let payload = null;
      if (text) { try { payload = JSON.parse(text); } catch { payload = text; } }
      if (!response.ok) {
        throw new ApiError(describe(payload, response.status), response.status, payload);
      }
      return payload;
    },
    setColumns: (id, columns) =>
      request('PUT', `/api/schedules/${id}/columns`, { columns }),
    notes: (id) => request('GET', `/api/schedules/${id}/notes`),
    setNotes: (id, notes) => request('PUT', `/api/schedules/${id}/notes`, { notes }),
    customiseNotes: (id) => request('POST', `/api/schedules/${id}/notes/customise`),
    undo: (id) => request('POST', `/api/schedules/${id}/undo`),
    redo: (id) => request('POST', `/api/schedules/${id}/redo`),

    revisions: (id) => request('GET', `/api/schedules/${id}/revisions`),
    nextRevision: (id, published) =>
      request('GET', `/api/schedules/${id}/revisions/next?published=${published ? 'true' : 'false'}`),
    addRevision: (id, body) => request('POST', `/api/schedules/${id}/revisions`, body),
    updateRevision: (id, rid, body) =>
      request('PUT', `/api/schedules/${id}/revisions/${rid}`, body),
    deleteRevision: (id, rid) => request('DELETE', `/api/schedules/${id}/revisions/${rid}`),
    issueRevision: (id, rid) =>
      request('POST', `/api/schedules/${id}/revisions/${rid}/issue`),
    snapshot: (id, rid) => request('GET', `/api/schedules/${id}/revisions/${rid}/snapshot`),
    diff: (id, rid, against) =>
      request('GET', `/api/schedules/${id}/revisions/${rid}/diff${against ? `?against=${against}` : ''}`),
  },

  catalogue: {
    list: () => request('GET', '/api/catalogue'),
    meta: () => request('GET', '/api/catalogue/meta'),
    read: (id) => request('GET', `/api/catalogue/${id}`),
    create: (body) => request('POST', '/api/catalogue', body),
    update: (id, body) => request('PUT', `/api/catalogue/${id}`, body),
    validate: (body) => request('POST', '/api/catalogue/validate', body),
    usage: (id) => request('GET', `/api/catalogue/${id}/usage`),
    impact: (id, body) => request('POST', `/api/catalogue/${id}/impact`, body),
    archive: (id) => request('DELETE', `/api/catalogue/${id}`),
  },

  library: {
    list: (code, q = '') =>
      request('GET', `/api/library/${encodeURIComponent(code)}?q=${encodeURIComponent(q)}`),
    save: (body) => request('POST', '/api/library', body),
    saveMany: (code, rows) =>
      request('POST', '/api/library/bulk', { type_code: code, rows }),
    importColumns: (code) =>
      request('GET', `/api/library/${encodeURIComponent(code)}/import/columns`),
    // The same endpoint plans and applies; only `apply` differs, so the preview
    // and the import can never disagree about what would happen.
    importPreview: (body) => request('POST', '/api/library/import', body),
    importApply: (body) => request('POST', '/api/library/import', { ...body, apply: true }),
    inspect: (body) => request('POST', '/api/library/inspect', body),
    update: (id, body) => request('PUT', `/api/library/${id}`, body),
    queue: () => request('GET', '/api/library/review/queue'),
    setState: (id, state) => request('POST', `/api/library/review/${id}/${state}`),
    resolveFlag: (id) => request('POST', `/api/library/review/flags/${id}/resolve`),
    remove: (id) => request('DELETE', `/api/library/${id}`),

    // The workbook round trip. `workbookUrl` is a download rather than a fetch,
    // so it is a URL rather than a call; the import posts the file back.
    workbookUrl: (code = '', data = true) => {
      const params = new URLSearchParams();
      if (code) params.set('code', code);
      if (!data) params.set('data', 'false');
      const query = params.toString();
      return `/api/library/workbook.xlsx${query ? `?${query}` : ''}`;
    },
    importWorkbook: async (file, { apply = false, updateExisting = true } = {}) => {
      const form = new FormData();
      form.append('file', file);
      form.append('apply', apply ? 'true' : 'false');
      form.append('update_existing', updateExisting ? 'true' : 'false');
      const response = await fetch('/api/library/workbook/import', {
        method: 'POST', body: form,
      });
      const text = await response.text();
      let payload = null;
      if (text) { try { payload = JSON.parse(text); } catch { payload = text; } }
      if (!response.ok) {
        throw new ApiError(describe(payload, response.status), response.status, payload);
      }
      return payload;
    },
  },

  impact: (area = '') => request('GET', `/api/impact${area ? `?area=${area}` : ''}`),

  register: (projectId) =>
    request('GET', `/api/register${projectId ? `?project_id=${projectId}` : ''}`),

  settings: {
    read: () => request('GET', '/api/settings'),
    update: (body) => request('PUT', '/api/settings', body),
    storage: () => request('GET', '/api/settings/storage'),
    branding: () => request('GET', '/api/settings/branding'),
    previewBranding: (branding) =>
      request('POST', '/api/settings/branding/preview', { branding }),
  },
};
