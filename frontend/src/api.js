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

// Settings > General: app-wide preferences, currently just the timezone
// used to convert broker CSV import timestamps to exchange (Eastern) time.
export const appSettingsApi = {
  get: () => api.get('/api/app-settings'),
  put: (data) => api.put('/api/app-settings', data),
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
