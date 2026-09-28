import axios from 'axios';

export const api = axios.create({baseURL: '/api', timeout: 60000});
let tokens = null, refreshing = null, expired = () => {};
export function setSession(value) { tokens = value; }
export function onExpired(callback) { expired = callback; }
api.interceptors.request.use(config => {
  if (tokens) config.headers.Authorization = `Bearer ${tokens.access_token}`;
  return config;
});
api.interceptors.response.use(response => response, async error => {
  const config = error.config;
  if (error.response?.status === 401 && tokens && !config._retried && !config.url.startsWith('/auth/')) {
    config._retried = true;
    try {
      if (!refreshing) refreshing = axios.post('/api/auth/refresh', {}, {
        headers: {Authorization: `Bearer ${tokens.refresh_token}`}, timeout: 15000,
      }).then(({data}) => {tokens = data;}).finally(() => {refreshing = null;});
      await refreshing;
      return api(config);
    } catch {
      tokens = null;
      expired();
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
