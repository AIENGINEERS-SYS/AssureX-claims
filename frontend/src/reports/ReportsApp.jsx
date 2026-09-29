import React, {useEffect, useState} from 'react';
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query';
import {api, errorMessage, getSession, onExpired, setSession} from '../claims/api';
import './reports.css';

const title = value => String(value ?? '').replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
const display = value => value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const initial = {report_type: 'claims', dataset: 'claims', entity_id: '', search: '', status: '', date_from: '', date_to: '', expiry_days: 30};
function payload(form) {
  return Object.fromEntries(Object.entries(form).filter(([key, value]) => value !== '' &&
    (key !== 'entity_id' || ['claim', 'customer', 'product', 'reviewer'].includes(form.report_type)))
    .map(([key, value]) => [key, ['entity_id', 'expiry_days'].includes(key) ? Number(value) : value]));
}

function Login({signedIn}) {
  const [error, setError] = useState('');
  const login = useMutation({mutationFn: async values => {
    const {data} = await api.post('/auth/login', values);
    setSession(data);signedIn(data.user);
  }, onError: error => setError(errorMessage(error))});
  return <section className="report-panel report-login"><p className="report-kicker">ASSUREX REPORTS</p><h1>Sign in to your report center</h1>
    <p>Review your records and prepare a secure download.</p><form onSubmit={event => {
      event.preventDefault();setError('');
      const form = new FormData(event.currentTarget);
      login.mutate({email: form.get('email'), password: form.get('password')});
    }}>
      <label>Email address<input name="email" type="email" autoComplete="username" required/></label>
      <label>Password<input name="password" type="password" autoComplete="current-password" required/></label>
      <button disabled={login.isPending}>{login.isPending ? 'Signing in…' : 'Sign in'}</button>
    </form>{error && <p role="alert">{error}</p>}</section>;
}

function Center({user}) {
  const client = useQueryClient();
  const [form, setForm] = useState(initial), [filters, setFilters] = useState(null), [page, setPage] = useState(1);
  const [historyPage, setHistoryPage] = useState(1), [section, setSection] = useState('Claims'), [error, setError] = useState('');
  const [downloading, setDownloading] = useState(null), [downloadProgress, setDownloadProgress] = useState(null);
  const preview = useQuery({queryKey: ['reports', user.id, filters, page], enabled: !!filters,
    queryFn: async () => (await api.get('/reports', {params: {...filters, page, per_page: 25}})).data,
    refetchInterval: false, retry: false});
  const history = useQuery({queryKey: ['report-history', user.id, historyPage],
    queryFn: async () => (await api.get('/reports/history', {params: {page: historyPage, per_page: 10}})).data,
    refetchInterval: query => query.state.data?.items.some(job => ['queued', 'running'].includes(job.status)) ? 2000 : 15000});
  const generate = useMutation({mutationFn: async format => (await api.post(`/reports/export/${format}`, payload(form))).data,
    onSuccess: () => {setError('');setHistoryPage(1);client.invalidateQueries({queryKey: ['report-history']});},
    onError: err => setError(errorMessage(err))});
  async function download(job) {
    setError('');setDownloading(job.id);setDownloadProgress(null);
    try {
      const {data} = await api.get(`/reports/jobs/${job.id}/download`, {responseType: 'blob', timeout: 0,
        onDownloadProgress: event => setDownloadProgress(event.total ? Math.round(event.loaded / event.total * 100) : null)});
      const url = URL.createObjectURL(data), link = document.createElement('a');
      link.href = url;link.download = `assurex-${job.id}.${job.format === 'excel' ? 'xlsx' : job.format}`;
      document.body.appendChild(link);link.click();link.remove();setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (err) {
      if (err.response?.data instanceof Blob) {
        try {err.response.data = JSON.parse(await err.response.data.text());} catch { /* Use connection fallback. */ }
      }
      setError(errorMessage(err));
    } finally {setDownloading(null);}
  }
  const field = (key, value) => setForm(previous => ({...previous, [key]: value}));
  const types = ['claims', 'claim', 'product', ...(['customer', 'admin'].includes(user.role) ? ['customer'] : []),
    ...(['reviewer', 'admin'].includes(user.role) ? ['reviewer'] : []), ...(user.role === 'admin' ? ['administrative'] : [])];
  const data = preview.data?.sections[section];
  return <>
    <div className="report-heading"><div><p className="report-kicker">RECORDS & INSIGHTS</p><h1>Report center</h1>
      <p>Explore your records. Export the details you need.</p></div><span className="report-role">{title(user.role)} workspace</span></div>
    <section className="report-panel"><h2>Generate report</h2><form onSubmit={event => {event.preventDefault();setPage(1);setFilters(payload(form));}}>
      <div className="report-filters">
        <label>Report type<select value={form.report_type} onChange={e => {
          const kind = e.target.value;
          setForm({...initial, report_type: kind, entity_id: ['customer', 'reviewer'].includes(kind) && user.role === kind ? user.id : ''});
        }}>{types.map(type => <option key={type} value={type}>{title(type === 'claims' ? 'all_authorized_claims' : `${type}_report`)}</option>)}</select></label>
        {['claim', 'customer', 'product', 'reviewer'].includes(form.report_type) && <label>{title(form.report_type)} record ID<input type="number" min="1" step="1" required
          value={form.entity_id} onChange={e => field('entity_id', e.target.value)}/></label>}
        <label>Export dataset<select value={form.dataset} onChange={e => setForm({...form, dataset: e.target.value, status: ''})}>
          {['claims', 'approved', 'rejected', 'manual_review', 'expiring_warranties', ...(['admin', 'reviewer'].includes(user.role) ? ['reviewer_queue'] : [])]
            .map(value => <option key={value} value={value}>{title(value)}</option>)}</select></label>
        <label>Search<input type="search" maxLength={120} placeholder="Claim reference, product or serial" value={form.search} onChange={e => field('search', e.target.value)}/></label>
        <label>Status<select value={form.status} disabled={['approved', 'rejected', 'manual_review'].includes(form.dataset)} onChange={e => field('status', e.target.value)}>
          <option value="">All statuses</option>{['draft', 'submitted', 'under_evaluation', 'additional_information_required', 'manual_review', 'approved', 'rejected', 'closed']
            .map(value => <option key={value} value={value}>{title(value)}</option>)}</select></label>
        <label>Submitted from<input type="date" value={form.date_from} onChange={e => field('date_from', e.target.value)}/></label>
        <label>Submitted through<input type="date" min={form.date_from || undefined} value={form.date_to} onChange={e => field('date_to', e.target.value)}/></label>
        {form.dataset === 'expiring_warranties' && <label>Expiring within days<input type="number" min="0" max="365" value={form.expiry_days} required onChange={e => field('expiry_days', e.target.value)}/></label>}
      </div><div className="report-actions"><button disabled={preview.isFetching}>{preview.isFetching ? 'Loading preview…' : 'Generate report'}</button>
        {['pdf', 'csv', 'excel'].map(format => <button className="secondary" type="button" key={format} disabled={generate.isPending}
          onClick={event => {if (event.currentTarget.form.reportValidity()) generate.mutate(format);}}>Export {format.toUpperCase()}</button>)}
        <button type="button" className="secondary" onClick={() => {setForm(initial);setFilters(null);setPage(1);}}>Reset filters</button></div>
      <p className="report-hint">Exports include every matching record. Prepare a file here, then download it from report history.</p>
    </form></section>
    {(error || preview.error) && <p className="report-error" role="alert">{error || errorMessage(preview.error)}</p>}
    {preview.data && <section className="report-panel"><h2>Report preview <span className="report-count">{preview.data.analytics.total_claims.toLocaleString()} claims</span></h2>
      <p className="report-hint">{Object.entries(preview.data.analytics.statuses).map(([status, count]) => `${title(status)}: ${count}`).join(' · ') || 'No matching claims'}</p>
      <label>Report section<select value={section} onChange={e => {setSection(e.target.value);setPage(1);}}>{Object.keys(preview.data.sections).map(name => <option key={name}>{name}</option>)}</select></label>
      <div className="report-table" tabIndex="0" aria-label={`${section} preview`}>{data?.items.length ? <table><thead><tr>{Object.keys(data.items[0]).map(key => <th key={key}>{title(key)}</th>)}</tr></thead>
        <tbody>{data.items.map((item, i) => <tr key={item.id ?? i}>{Object.entries(item).map(([key, value]) => <td key={key}>{display(value)}</td>)}</tr>)}</tbody></table> : <p>No records on this page.</p>}</div>
      <div className="report-actions"><button className="secondary" disabled={page === 1 || preview.isFetching} onClick={() => setPage(page - 1)}>Previous</button>
        <span>Page {page} · {data?.total || 0} {section.toLowerCase()}</span><button className="secondary" disabled={page * 25 >= (data?.total || 0) || preview.isFetching} onClick={() => setPage(page + 1)}>Next</button></div>
    </section>}
    <section className="report-panel"><h2>Report history</h2><p className="report-hint">Files expire after the time shown. Access is checked again when you download.</p>
      {history.isPending && <p role="status">Loading history…</p>}{history.error && <p role="alert">{errorMessage(history.error)}</p>}
      {history.data?.items.length === 0 && <p>No reports yet. Choose your filters and prepare an export above.</p>}
      <div className="report-history">{history.data?.items.map(job => <article key={job.id} className="report-job">
        <div><strong>{title(job.filters.report_type)} · {job.format.toUpperCase()}</strong><p>{title(job.filters.dataset)}{job.filters.search ? ` · “${job.filters.search}”` : ''}</p>
          <small>Requested {new Date(job.created_at).toLocaleString()} · Expires {new Date(job.expires_at).toLocaleString()}</small></div>
        <div className="report-job-state"><span>{title(job.status)}</span>{['queued', 'running'].includes(job.status) && <><progress aria-label="Report generation progress" value={job.progress} max="100"/>
          <small>{job.status === 'queued' ? 'Waiting to start' : `${job.processed.toLocaleString()} of ${job.total.toLocaleString()} records · ${job.progress}%`}</small></>}
          {job.error && <small role="alert">{job.error}</small>}
          {job.status === 'completed' && <button disabled={!!downloading || new Date(job.expires_at) <= new Date()} onClick={() => download(job)}>
            {downloading === job.id ? `Downloading${downloadProgress == null ? '…' : ` ${downloadProgress}%`}` : `Download ${job.format.toUpperCase()}`}</button>}</div>
      </article>)}</div><div className="report-actions"><button className="secondary" disabled={historyPage === 1} onClick={() => setHistoryPage(historyPage - 1)}>Previous</button><span>Page {historyPage}</span>
        <button className="secondary" disabled={!history.data || historyPage * 10 >= history.data.total} onClick={() => setHistoryPage(historyPage + 1)}>Next</button></div>
    </section>
  </>;
}

export default function ReportsApp() {
  const [user, setUser] = useState(() => getSession()?.user || null), client = useQueryClient();
  useEffect(() => {onExpired(() => {client.clear();setUser(null);});return () => onExpired(() => {});}, [client]);
  async function signOut() {try {await api.post('/auth/logout');} finally {setSession(null);setUser(null);client.clear();}}
  return <div className="reports-app"><a className="report-skip" href="#report-main">Skip to content</a><header className="report-nav"><a className="report-brand" href="/products">AssureX</a>
    <nav aria-label="Main navigation"><a href="/dashboard">Dashboard</a><a href="/products">Products</a><a href="/claims">Claims</a><a href="/reports" aria-current="page">Reports</a><a href="/search">Search</a></nav>
    {user && <button className="secondary" onClick={() => signOut().catch(() => {})}>Sign out</button>}</header>
    <main id="report-main">{user ? <Center key={user.id} user={user}/> : <Login signedIn={setUser}/>}</main></div>;
}
