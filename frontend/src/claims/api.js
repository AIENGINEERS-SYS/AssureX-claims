import axios from 'axios';

const configuredApi = import.meta.env.VITE_API_URL?.trim();
if (import.meta.env.PROD && !configuredApi) {
  throw new Error('VITE_API_URL must be configured for the standalone frontend.');
}
const apiBase = (configuredApi || 'http://127.0.0.1:8000/api').replace(/\/$/, '');
export const api = axios.create({baseURL: apiBase, timeout: 60000});
let tokens = null, refreshing = null, expired = () => {}, revision = 0;
const sessionListeners = new Set();
export function setSession(value) { tokens = value; revision += 1; sessionListeners.forEach(listener => listener(value)); }
export function onSessionChange(callback) {sessionListeners.add(callback); return () => sessionListeners.delete(callback);}
export function getSession() { return tokens; }
export function onExpired(callback) { expired = callback; }
api.interceptors.request.use(config => {
  if (config._sessionRevision !== undefined && config._sessionRevision !== revision) throw new axios.CanceledError('Session changed.');
  config._sessionRevision = revision;
  if (tokens) config.headers.Authorization = `Bearer ${tokens.access_token}`;
  return config;
}, error => {throw error;}, {synchronous: true});
api.interceptors.response.use(response => response, async error => {
  const config = error.config;
  if (error.response?.status === 401 && tokens && !config._retried && !config.url.startsWith('/auth/')) {
    config._retried = true;
    const expectedRevision = revision;
    try {
      if (!refreshing || refreshing.revision !== expectedRevision) {
        const attempt = {revision: expectedRevision};
        attempt.promise = axios.post(`${apiBase}/auth/refresh`, {}, {
          headers: {Authorization: `Bearer ${tokens.refresh_token}`}, timeout: 15000,
        }).then(({data}) => {
          if (revision !== expectedRevision) throw new axios.CanceledError('Session changed.');
          tokens = data; sessionListeners.forEach(listener => listener(data));
        }).finally(() => {if (refreshing === attempt) refreshing = null;});
        refreshing = attempt;
      }
      await refreshing.promise;
      if (revision !== expectedRevision) throw new axios.CanceledError('Session changed.');
      return api(config);
    } catch {
      if (revision === expectedRevision) {setSession(null); expired();}
    }
  }
  return Promise.reject(error);
});
export function errorMessage(error) {
  const payload = error.response?.data?.error;
  if (!payload) return 'Connection failed. Your entries are still here. Please retry.';
  return [payload.message, ...Object.entries(payload.details || {}).map(([key, value]) =>
    `${key.replaceAll('_', ' ')}: ${Array.isArray(value) ? value.join(' ') : JSON.stringify(value)}`)].join('\n');
}
export async function allPages(path) {
  const items = [];
  for (let page = 1; ; page++) {
    const {data} = await api.get(path, {params: {page, per_page: 100}});
    items.push(...data.items);
    if (items.length >= data.total || !data.items.length) return items;
  }
}
