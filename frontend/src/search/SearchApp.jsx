import React, {useEffect, useState} from 'react';
import {useQuery, useQueryClient} from '@tanstack/react-query';
import {api, errorMessage, getSession, onExpired, setSession} from '../claims/api';
import SearchWorkspace from './SearchWorkspace';

export function GlobalSearchBar() {
  const [q, setQ] = useState('');
  return <form className="sx-global-nav" role="search" onSubmit={event => {
    event.preventDefault();window.history.pushState(null, '', `/search?q=${encodeURIComponent(q)}`);window.dispatchEvent(new PopStateEvent('popstate'));
  }}><input type="search" aria-label="Search AssureX" placeholder="Search AssureX" maxLength={120} value={q} onChange={e => setQ(e.target.value)}/><button>Search</button></form>;
}

function SearchAnalytics() {
  const result = useQuery({queryKey: ['search-analytics'], queryFn: async () => (await api.get('/search/analytics')).data, refetchInterval: false});
  return <details className="sx-admin"><summary>Search analytics · last 7 days</summary>{result.error && <p role="alert">{errorMessage(result.error)}</p>}
    {result.data && <><p>{result.data.total_searches} searches · {result.data.average_ms} ms average · {result.data.failed_searches} failed searches</p>
      <h3>Most-used filters</h3>{result.data.most_used_filters.map(item => <p key={item.name}>{item.name}: {item.count}</p>)}
      <h3>Common searches</h3>{result.data.common_searches.map(item => <p key={item.query}>{item.query}: {item.count}</p>)}</>}</details>;
}

export default function SearchApp() {
  const [user, setUser] = useState(() => getSession()?.user || null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const client = useQueryClient();
  useEffect(() => {onExpired(() => {setUser(null);client.clear();});return () => onExpired(() => {});}, [client]);
  async function login(event) {
    event.preventDefault();const form = new FormData(event.currentTarget);setBusy(true);setError('');
    try {const {data} = await api.post('/auth/login', {email: form.get('email'), password: form.get('password')});setSession(data);setUser(data.user);}
    catch (error) {setError(errorMessage(error));} finally {setBusy(false);}
  }
  async function logout() {try {await api.post('/auth/logout');} catch {/* End the local session too. */}finally {setSession(null);setUser(null);client.clear();}}
  const url = new URLSearchParams(window.location.search), candidate = url.get('scope') || 'global';
  const scope = ['global', 'claims', 'products', 'warranties', ...(['reviewer', 'admin'].includes(user?.role) ? ['review'] : [])].includes(candidate) ? candidate : 'global';
  return <div className="sx-app"><header className="sx-nav"><a className="sx-brand" href="/products">AssureX</a><nav aria-label="Main navigation">
    <a href="/dashboard">Dashboard</a><a href="/products">Products</a><a href="/claims">Claims</a><a href="/reports">Reports</a><a href="/search" aria-current="page">Search</a></nav>
    {user && <button onClick={logout}>Sign out</button>}</header><main>
      {user ? <><SearchWorkspace key={`${user.id}:${window.location.search}`} user={user} initialScope={scope} initialFilters={{q: (url.get('q') || '').slice(0,120)}}/>
        {user.role === 'admin' && <SearchAnalytics/>}</> : <section className="sx-login"><h1>Sign in to search</h1><p>Find the claims, products and warranties available to your account.</p>
        <form onSubmit={login}><label>Email<input name="email" type="email" required autoComplete="username"/></label><label>Password<input name="password" type="password" required autoComplete="current-password"/></label>
          <button disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button></form>{error && <p role="alert">{error}</p>}</section>}
    </main></div>;
}
