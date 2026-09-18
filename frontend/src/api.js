import axios from 'axios';

// Defaults to the local backend. REACT_APP_API_URL can point the frontend at
// another origin (a second instance, a container, a LAN machine).
export const API_BASE = (process.env.REACT_APP_API_URL ?? 'http://localhost:8010').replace(/\/+$/, '');

const api = axios.create({ baseURL: API_BASE });

export const accountsApi = {
  list: () => api.get('/api/accounts'),
  create: (data) => api.post('/api/accounts', data),
  update: (id, data) => api.put(`/api/accounts/${id}`, data),
};

export const tradesApi = {
  list: (params) => api.get('/api/trades', { params }),
  create: (data) => api.post('/api/trades', data),
  update: (id, data) => api.put(`/api/trades/${id}`, data),
  delete: (id) => api.delete(`/api/trades/${id}`),
  getAnalysis: (group) => api.get(`/api/trades/${encodeURIComponent(group)}/analysis`),
  getAnalysisOptions: () => api.get('/api/analysis-options'),
  updateAnalysis: (group, data) => api.patch(`/api/trades/${encodeURIComponent(group)}/analysis`, data),
  addTag: (group, data) => api.post(`/api/trades/${encodeURIComponent(group)}/tags`, data),
  deleteTag: (tagId) => api.delete(`/api/trade-tags/${tagId}`),
  setSetup: (id, setup, note) => api.patch(`/api/trades/${id}/setup`, { setup, note }),
  listCustomSetups: () => api.get('/api/setups/custom'),
  createCustomSetup: (data) => api.post('/api/setups/custom', data),
  deleteCustomSetup: (id) => api.delete(`/api/setups/custom/${id}`),
  addExecution: (id, data) => api.post(`/api/trades/${id}/executions`, data),
  updateExecution: (id, idx, data) => api.put(`/api/trades/${id}/executions/${idx}`, data),
  deleteExecution: (id, idx) => api.delete(`/api/trades/${id}/executions/${idx}`),
};

export const importApi = {
  importCsv: (formData) => api.post('/api/import-csv', formData, {
    headers: { 'Content-Type': 'multipart/form-data' }
  }),
  uploadDiary: (formData) => api.post('/api/upload-diary', formData, {
    headers: { 'Content-Type': 'multipart/form-data' }
  }),
};

export const kpisApi = {
  get: (params) => api.get('/api/kpis', { params }),
};

export const diaryApi = {
  list: (params) => api.get('/api/diary', { params }),
  delete: (id) => api.delete(`/api/diary/${id}`),
  deleteByDate: (date, accountId) => api.delete(`/api/diary/by-date/${date}`, { params: accountId != null ? { account_id: accountId } : {} }),
};

export const chartApi = {
  get: (ticker, date, timeframe = '1Min', daysBack = 1) =>
    api.get(`/api/chart/${encodeURIComponent(ticker)}/${date}`, { params: { timeframe, days_back: daysBack } }),
};

export const insightsApi = {
  get: (params) => api.get('/api/insights', { params }),
};

export const calendarApi = {
  get: (params) => api.get('/api/calendar', { params }),
};

export const brainApi = {
  chat: (messages, accountId) =>
    api.post('/api/brain', { messages, account_id: accountId }),
};

export const dailySummaryApi = {
  get: (params) => api.get('/api/daily-summary', { params }),
};

export const goalsApi = {
  get: (params) => api.get('/api/goals', { params }),
  put: (data) => api.put('/api/goals', data),
};

export const reportsApi = {
  get: (params) => api.get('/api/reports', { params }),
};

// Settings > Library: strategy names, sources and tags. `item` is
// { kind: 'strategy' | 'source' | 'tag', tag_type?, name, ... }.
export const libraryApi = {
  list: () => api.get('/api/library'),
  create: (item) => api.post('/api/library', item),
  update: (item) => api.put('/api/library', item),
  merge: (item) => api.post('/api/library/merge', item),
  remove: (item) => api.post('/api/library/delete', item),
};

export const edgeReportApi = {
  get: (params) => api.get('/api/edge-report', { params }),
};

export const weeklySummaryApi = {
  get: (params) => api.get('/api/weekly-summary', { params }),
};

export const yearlyKpisApi = {
  get: (params) => api.get('/api/yearly-kpis', { params }),
};

// Settings > Lot Matching: account-wide FIFO/LIFO default, plus per-trade overrides.
export const lotMethodApi = {
  get: () => api.get('/api/settings/lot-method'),
  set: (method) => api.put('/api/settings/lot-method', { method }),
  getTradeLots: (tradeId) => api.get(`/api/trades/${tradeId}/lots`),
  setTradeMethod: (tradeId, method) => api.patch(`/api/trades/${tradeId}/lot-method`, { method }),
};

// Settings > Backup: encrypted backup destinations (scp/ftp/rsync), schedules and runs.
export const backupApi = {
  listDestinations: () => api.get('/api/backup/destinations'),
  createDestination: (data) => api.post('/api/backup/destinations', data),
  updateDestination: (id, data) => api.put(`/api/backup/destinations/${id}`, data),
  deleteDestination: (id) => api.delete(`/api/backup/destinations/${id}`),
  testDestination: (id) => api.post(`/api/backup/destinations/${id}/test`),
  setSchedule: (destId, data) => api.put(`/api/backup/schedule/${destId}`, data),
  listSchedules: () => api.get('/api/backup/schedule'),
  runNow: (destId, passphrase) => api.post(`/api/backup/run/${destId}`, { passphrase }),
  runLocal: (passphrase) => api.post('/api/backup/run-local', { passphrase }),
  listRuns: () => api.get('/api/backup/runs'),
  listLocalFiles: () => api.get('/api/backup/local-files'),
  restore: (formData) => api.post('/api/backup/restore', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }),
};

// Settings > Reconciliation: dividends + broker fee reconciliation.
export const reconciliationApi = {
  listDividends: (accountId) => api.get('/api/reconciliation/dividends', { params: accountId != null ? { account_id: accountId } : {} }),
  createDividend: (data) => api.post('/api/reconciliation/dividends', data),
  updateDividend: (id, data) => api.put(`/api/reconciliation/dividends/${id}`, data),
  deleteDividend: (id) => api.delete(`/api/reconciliation/dividends/${id}`),
  listTrades: (params) => api.get('/api/reconciliation/trades', { params }),
  reconcileTrade: (tradeId, data) => api.post(`/api/reconciliation/trades/${tradeId}`, data),
  unreconcileTrade: (tradeId) => api.delete(`/api/reconciliation/trades/${tradeId}`),
  importStatement: (formData, accountId) => api.post('/api/reconciliation/import', formData, {
    params: { account_id: accountId },
    headers: { 'Content-Type': 'multipart/form-data' },
  }),
  summary: (accountId) => api.get('/api/reconciliation/summary', { params: accountId != null ? { account_id: accountId } : {} }),
};

// Settings > Security: TLS certificate upload/status for encrypted connections.
export const tlsApi = {
  status: () => api.get('/api/settings/tls/status'),
  upload: (formData) => api.post('/api/settings/tls/certificate', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }),
  remove: () => api.delete('/api/settings/tls/certificate'),
};

