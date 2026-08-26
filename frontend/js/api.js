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
  },

  schedules: {
    grid: (id) => request('GET', `/api/schedules/${id}`),
    addRow: (id, values) => request('POST', `/api/schedules/${id}/rows`, { values }),
    updateRow: (id, rowId, values) =>
      request('PUT', `/api/schedules/${id}/rows/${rowId}`, { values }),
    deleteRow: (id, rowId) => request('DELETE', `/api/schedules/${id}/rows/${rowId}`),
    replaceRows: (id, rows) => request('POST', `/api/schedules/${id}/rows/bulk`, rows),

    revisions: (id) => request('GET', `/api/schedules/${id}/revisions`),
    nextRevision: (id, published) =>
      request('GET', `/api/schedules/${id}/revisions/next?published=${published ? 'true' : 'false'}`),
    addRevision: (id, body) => request('POST', `/api/schedules/${id}/revisions`, body),
    updateRevision: (id, rid, body) =>
      request('PUT', `/api/schedules/${id}/revisions/${rid}`, body),
    deleteRevision: (id, rid) => request('DELETE', `/api/schedules/${id}/revisions/${rid}`),
  },

  catalogue: {
    list: () => request('GET', '/api/catalogue'),
    meta: () => request('GET', '/api/catalogue/meta'),
    read: (id) => request('GET', `/api/catalogue/${id}`),
    create: (body) => request('POST', '/api/catalogue', body),
    update: (id, body) => request('PUT', `/api/catalogue/${id}`, body),
    validate: (body) => request('POST', '/api/catalogue/validate', body),
    usage: (id) => request('GET', `/api/catalogue/${id}/usage`),
    archive: (id) => request('DELETE', `/api/catalogue/${id}`),
  },

  library: {
    list: (code, q = '') =>
      request('GET', `/api/library/${encodeURIComponent(code)}?q=${encodeURIComponent(q)}`),
    save: (body) => request('POST', '/api/library', body),
    inspect: (body) => request('POST', '/api/library/inspect', body),
    update: (id, body) => request('PUT', `/api/library/${id}`, body),
    queue: () => request('GET', '/api/library/review/queue'),
    setState: (id, state) => request('POST', `/api/library/review/${id}/${state}`),
    resolveFlag: (id) => request('POST', `/api/library/review/flags/${id}/resolve`),
    remove: (id) => request('DELETE', `/api/library/${id}`),
  },

  register: (projectId) =>
    request('GET', `/api/register${projectId ? `?project_id=${projectId}` : ''}`),

  settings: {
    read: () => request('GET', '/api/settings'),
    update: (body) => request('PUT', '/api/settings', body),
  },
};
