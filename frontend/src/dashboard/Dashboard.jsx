import React, {Suspense, useEffect, useState} from 'react';
import {BrowserRouter, Link, Navigate, Route, Routes, useLocation, useNavigate, useSearchParams} from 'react-router-dom';
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query';
import {Bell, Box, ChevronRight, ClipboardList, FileText, LayoutDashboard, LogOut,
  Plus, Search, Shield, ShieldAlert, Users} from 'lucide-react';
import {api, errorMessage, getSession, onExpired, setSession} from '../claims/api';

const Chart = React.lazy(() => import('./Charts'));
const label = value => String(value ?? '').replaceAll('_', ' ').replace(/\b\w/g, char => char.toUpperCase());
const date = value => value ? new Date(value).toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: 'numeric'}) : '—';
const pct = value => value == null ? 'No data' : `${(Number(value) * 100).toFixed(1)}%`;
const query = (key, path, params) => useQuery({queryKey: [key, params], queryFn: async () => (await api.get(path, {params})).data});

function Badge({children, tone = ''}) {return <span className={`badge ${tone}`}>{label(children)}</span>;}
function Tone({status}) {
  const tone = ['approved', 'active', 'extended warranty', 'confirmed'].includes(String(status).toLowerCase()) ? 'green' :
    ['rejected', 'expired', 'high', 'failed'].includes(String(status).toLowerCase()) ? 'red' :
    ['manual_review', 'submitted', 'under_evaluation'].includes(String(status).toLowerCase()) ? 'blue' : 'amber';
  return <Badge tone={tone}>{status}</Badge>;
}
function Metric({icon: Icon, title, value, note}) {return <div className="card metric"><span className="metric-icon"><Icon aria-hidden="true"/></span>
  <div className="metric-value">{value ?? '—'}</div><div className="metric-label">{title}</div><p className="metric-note">{note}</p></div>;}
function Panel({title, caption, action, children, className = ''}) {return <section className={`card ${className}`}><div className="section-head">
  <div><h2>{title}</h2>{caption && <p>{caption}</p>}</div>{action}</div>{children}</section>;}
function Empty({message = 'There is no data to display yet.'}) {return <div className="empty">{message}</div>;}
function Pager({meta, onPage}) {if (!meta || meta.pages < 2) return null;return <div className="pagination"><button disabled={meta.page <= 1} onClick={() => onPage(meta.page - 1)}>Previous</button>
  Page {meta.page} of {meta.pages}<button disabled={meta.page >= meta.pages} onClick={() => onPage(meta.page + 1)}>Next</button></div>;}
function QueryState({result, children}) {if (result.isPending) return <div className="card loading" role="status">Loading dashboard…</div>;
  if (result.isError) return <div className="error-box" role="alert">{errorMessage(result.error)} <button onClick={() => result.refetch()}>Retry</button></div>;
  return children(result.data);}
function Distribution({values, type = 'pie'}) {return <Suspense fallback={<div className="loading">Loading chart…</div>}><Chart variant="distribution" values={values} type={type}/></Suspense>;}
function Timeline({series, keys, type = 'line'}) {return <Suspense fallback={<div className="loading">Loading chart…</div>}><Chart variant="timeline" series={series} keys={keys} type={type}/></Suspense>;}

function NoticeList({compact = false}) {
  const client = useQueryClient(), [page, setPage] = useState(1);
  const result = query('notifications', '/dashboard/notifications', {page, per_page: compact ? 4 : 15});
  const mark = useMutation({mutationFn: id => api.patch(`/dashboard/notifications/${id}/read`),
    onSuccess: () => {client.invalidateQueries({queryKey: ['notifications']});client.invalidateQueries({queryKey: ['customer']});}});
  return <QueryState result={result}>{data => <>{data.items.length ? data.items.map(item => <div className="list-row" key={item.id}>
    <div className="row-main"><strong>{!item.is_read && <Badge tone="blue">New</Badge>} {item.title}</strong>
      <span className="muted">{item.message}</span><small>{date(item.created_at)}</small></div>
    <div className="row-actions">{item.href && <a href={item.href} className="text-button">Open</a>}
      {!item.is_read && <button className="text-button" disabled={mark.isPending} onClick={() => mark.mutate(item.id)}>Mark read</button>}</div>
  </div>) : <Empty message="No notifications yet."/>}<Pager meta={data} onPage={setPage}/></>}</QueryState>;
}

function Customer({adminView = false}) {
  const [searchParams] = useSearchParams(), userId = adminView ? Number(searchParams.get('user_id')) : undefined;
  const [page, setPage] = useState(1), result = query('customer', '/dashboard/customer', {page, per_page: 8, user_id: userId});
  if (adminView && !userId) return <Navigate to="/admin" replace/>;
  return <QueryState result={result}>{data => <><div className="page-head"><div><span className="eyebrow">Your overview</span>
    <h1>{adminView ? `Customer #${userId} overview` : 'Everything protected. All in one place.'}</h1><p>Track warranties and claims with clarity.</p></div>
    {!adminView && <div className="head-actions"><a className="lime-button" href="/claims"><Plus size={16}/> New claim</a><a className="outline-button" href="/products/new"><Box size={16}/> Register product</a></div>}</div>
    <div className="grid-cards"><Metric icon={Box} title="Registered products" value={data.products.total_products} note={`${data.products.expired} expired warranties`}/>
      <Metric icon={Shield} title="Active warranties" value={data.products.active_warranties} note={`${data.products.coverage_percentage}% of products covered`}/>
      <Metric icon={ShieldAlert} title="Expiring within 30 days" value={data.products.expiring_count} note="Take action before coverage ends"/>
      <Metric icon={ClipboardList} title="Open claims" value={data.claims.submitted + data.claims.under_review} note={`${data.claims.total} total · ${data.claims.approved} approved · ${data.claims.rejected} rejected`}/></div>
    <div className="two-col"><Panel title="Your protection at a glance" caption="Current warranty coverage, updated from your records"><div className="mini-list">
      <span><strong>{data.products.covered}</strong> products covered</span><span><strong>{data.products.nearest_expiry_days ?? '—'}</strong> days to nearest expiry</span>
      <span><strong>{data.claims.draft}</strong> claim drafts</span></div></Panel>
      <div className="card attention"><Badge tone="amber">Needs your attention</Badge><h2>{data.actions[0]?.text || 'Your protection is up to date'}</h2>
        <p>{data.actions.length ? `${data.actions.length} actions waiting in your account.` : 'We will flag anything that needs your attention here.'}</p>
        {!adminView && data.actions[0]?.href && <a className="lime-button" href={data.actions[0].href}>Continue <ChevronRight size={16}/></a>}</div></div>
    <div className="two-col"><Panel title="Recent claims" caption="Follow each claim from submission to decision" action={!adminView && <a href="/claims" className="text-button">View all →</a>}>
      {data.recent_claims.length ? data.recent_claims.map(item => <div className="list-row" key={item.id}><div className="row-main"><strong>{item.claim_id || 'Draft claim'} · {item.product || 'Select a product'}</strong>
        <small>{item.submitted_at ? `Submitted ${date(item.submitted_at)}` : 'Not submitted'} · Updated {date(item.updated_at)}</small></div><div className="row-actions"><Tone status={item.status}/>{!adminView && <a href={item.href} className="text-button">Open</a>}</div></div>) : <Empty message="No claims yet. Start a claim whenever you need help."/>}
      <Pager meta={data.pagination} onPage={setPage}/></Panel>
      <Panel title="Actions & reminders" caption="Open the item to continue">{data.actions.length ? data.actions.map((item, i) => adminView ? <div key={i} className="list-row">{item.text}</div> :
        <a key={i} className="list-row row-link" href={item.href}><span>{item.text}</span><ChevronRight size={16}/></a>) : <Empty message="Nothing needs your attention."/>}</Panel></div>
    <div className="two-col"><Panel title="Warranty status" caption="Across your registered products"><Distribution values={data.charts.warranty_status}/></Panel>
      <Panel title="Claims over time" caption="Submissions and approvals in the last 12 months"><Timeline series={data.charts.claims_over_time} keys={['submitted', 'approved']}/></Panel></div>
    <div className="two-col"><Panel title="Expiring warranties" caption="Coverage ending within 30 days">{data.products.expiring.length ? data.products.expiring.map(item => <div className="list-row" key={item.product_id}><span><strong>{item.product_name}</strong><br/><small>Expires {item.expiry_date}</small></span><Badge tone="amber">{item.days_remaining} days</Badge></div>) : <Empty message="No warranties expire in the next 30 days."/>}</Panel>
      {!adminView && <Panel title="Notifications" caption={`${data.notifications_unread} unread`}><NoticeList compact/></Panel>}</div></>}</QueryState>;
}

function Reviewer({role}) {
  const [page, setPage] = useState(1), [search, setSearch] = useState(''), [term, setTerm] = useState(''), [selected, setSelected] = useState(null);
  useEffect(() => {const timer = setTimeout(() => {setPage(1);setTerm(search.trim());}, 350);return () => clearTimeout(timer);}, [search]);
  const result = query('reviewer', '/dashboard/reviewer', {page, per_page: 10, search: term});
  return <QueryState result={result}>{data => <><div className="page-head"><div><span className="eyebrow">Reviewer workspace</span>
    <h1>Decisions backed by evidence.</h1><p>Investigate flagged claims and keep every case moving.</p></div><a className="outline-button" href="#queue">Open review queue</a></div>
    <div className="grid-cards"><Metric icon={ShieldAlert} title="Manual review claims" value={data.summary.total_manual_review} note="In your visible queue"/>
      <Metric icon={ClipboardList} title="New today" value={data.summary.new_today} note="In your visible queue"/>
      <Metric icon={FileText} title="Pending reviews" value={data.summary.pending} note="Unassigned or assigned to you"/>
      <Metric icon={Shield} title="Approved reviews" value={data.summary.approved_reviews} note={`${data.summary.rejected_reviews} rejected`}/></div>
    <Panel title="Manual review queue" caption="Risk is shown only when model output exists" className="queue" action={<input aria-label="Search review queue" value={search} onChange={e => setSearch(e.target.value)} placeholder="Search claims"/>}>
      <div id="queue" className="table-wrap"><table><thead><tr><th>Claim</th><th>Customer / product</th><th>Risk</th><th>Submitted</th><th>Priority</th><th>Action</th></tr></thead><tbody>{data.queue.map(item =>
        <tr key={item.id}><td><strong>{item.claim_id}</strong></td><td>{item.customer}<br/><small>{item.product || 'Unknown product'}</small></td>
          <td>{item.risk_score == null ? 'Unscored' : `${item.risk_score}%`}</td><td>{date(item.submitted_at)}</td><td><Tone status={item.priority}/></td>
          <td><button className="text-button" onClick={() => setSelected(item)}>Open case →</button></td></tr>)}</tbody></table>
        {!data.queue.length && <Empty message="No matching cases on this page."/>}</div><Pager meta={data.pagination} onPage={setPage}/></Panel>
    <div className="two-col"><Panel title="Model disagreements" caption="Latest model outputs differ">{data.disagreements.length ? data.disagreements.map(item =>
      <button key={item.id} className="list-row row-link" onClick={() => setSelected(item)}><span><strong>{item.claim_id}</strong><br/><small>Python {item.python.class} {pct(item.python.confidence)} · GTM {item.gtm.class} {pct(item.gtm.confidence)}</small></span><Badge tone="amber">Inspect evidence</Badge></button>) : <Empty message="No recorded model disagreements."/>}</Panel>
      <Panel title="Missing document cases" caption="Required claim evidence">{data.missing_documents.length ? data.missing_documents.map(item =>
        <button key={item.claim_id} className="list-row row-link" onClick={() => setSelected({id: item.claim_id})}><strong>Claim #{item.claim_id}</strong><small>{item.missing.map(label).join(', ')}</small></button>) : <Empty message="No required documents are missing."/>}</Panel></div>
    <div className="two-col"><Panel title="Information requests" caption="Cases waiting for additional evidence">{data.information_requests.length ? data.information_requests.map(item =>
      <button key={item.id} className="list-row row-link" onClick={() => setSelected(item)}><strong>{item.claim_id}</strong><small>Updated {date(item.updated_at)}</small></button>) : <Empty message="No outstanding requests."/>}</Panel>
      <Panel title="Duplicate warning queue" caption="Exact SHA-256 matches across claims">{data.duplicates.length ? data.duplicates.map(item =>
        <div className="list-row" key={`${item.claim_id}-${item.matching_claim_id}-${item.file_hash}`}><span><strong>Claims #{item.claim_id} and #{item.matching_claim_id}</strong><br/><small>100% exact match · {label(item.status)}</small></span>
          <button className="text-button" onClick={() => setSelected({id: item.reviewable_claim_id, duplicate: item})}>Review</button></div>) : <Empty message="No exact duplicate warnings."/>}</Panel></div>
    <div className="two-col"><Panel title="Review outcomes" caption="Recorded decisions"><Distribution values={data.charts.outcomes} type="bar"/></Panel>
      <Panel title="Risk distribution" caption="Model invalid probability in this page"><Distribution values={data.charts.risk_distribution} type="bar"/></Panel></div>
    {selected && <ReviewModal item={selected} role={role} onClose={() => setSelected(null)}/>}</>}</QueryState>;
}

function ReviewModal({item, role, onClose}) {
  const client = useQueryClient(), [notes, setNotes] = useState(''), [kind, setKind] = useState('receipt'), [error, setError] = useState(''), [reviewerId, setReviewerId] = useState('');
  const detail = query('review-detail', `/dashboard/reviewer/claims/${item.id}`);
  const mutation = useMutation({mutationFn: ({path, body, method = 'post'}) => api[method](path, body),
    onSuccess: () => {client.invalidateQueries({queryKey: ['reviewer']});client.invalidateQueries({queryKey: ['admin']});onClose();},
    onError: err => setError(errorMessage(err))});
  function perform(path, body, method) {setError('');mutation.mutate({path, body, method});}
  return <div className="modal-backdrop" onClick={onClose}><section className="modal" role="dialog" aria-modal="true" aria-label="Review claim" onClick={e => e.stopPropagation()}>
    <div className="modal-head"><h2>{item.claim_id || `Claim #${item.id}`}</h2><button onClick={onClose}>Close</button></div>
    <QueryState result={detail}>{data => <><p className="muted">{data.claim.customer} · {data.claim.product || 'Product unavailable'} · Submitted {date(data.claim.submitted_at)}</p>
      <p>{data.claim.description || 'No description available.'}</p><h3>Evidence checklist</h3>
      <div className="mini-list">{data.claim.documents.length ? data.claim.documents.map(doc =>
        <span key={doc.id}><button className="text-button" onClick={async () => {try {const response = await api.get(`/documents/${doc.id}/content`, {responseType: 'blob'});
          const url = URL.createObjectURL(response.data);window.open(url, '_blank', 'noopener,noreferrer');setTimeout(() => URL.revokeObjectURL(url), 60000);} catch (err) {setError(errorMessage(err));}}}>Open {label(doc.type)}</button> · OCR {label(doc.ocr_status)} · {label(doc.review_status)}</span>) : <span>No documents attached.</span>}</div>
      <label htmlFor="review-notes">Decision notes</label><textarea id="review-notes" value={notes} maxLength={10000} onChange={e => setNotes(e.target.value)} placeholder="Record the evidence behind your decision"/>
      {role === 'admin' && <><label htmlFor="reviewer-id">Assign active reviewer ID</label><input id="reviewer-id" type="number" min="1" value={reviewerId} onChange={e => setReviewerId(e.target.value)}/></>}
      {data.claim.status === 'additional_information_required' ? <div className="modal-actions"><button disabled={mutation.isPending} onClick={() => perform(`/dashboard/reviewer/claims/${item.id}/remind`, {})}>Send reminder</button>
        <button className="lime-button" disabled={mutation.isPending} onClick={() => perform(`/dashboard/reviewer/claims/${item.id}/resume`, {})}>Resume review</button></div> : <>
        <div className="modal-actions"><button disabled={mutation.isPending || (role === 'admin' && !reviewerId)} onClick={() => perform(`/dashboard/reviewer/claims/${item.id}/assignment`, role === 'admin' ? {reviewer_id: Number(reviewerId)} : {}, 'patch')}>Assign reviewer</button>
        <button className="lime-button" disabled={mutation.isPending || !notes.trim()} onClick={() => perform(`/review/${item.id}/approve`, {notes})}>Approve</button>
        <button disabled={mutation.isPending || !notes.trim()} onClick={() => perform(`/review/${item.id}/reject`, {notes})}>Reject</button>
        <button disabled={mutation.isPending || !notes.trim()} onClick={() => perform(`/review/${item.id}/notes`, {notes})}>Add notes</button></div>
        <label htmlFor="request-type">Request document</label><select id="request-type" value={kind} onChange={e => setKind(e.target.value)}>
          {['receipt','warranty_card','serial_number_image','diagnostic_report','product_image','damage_evidence','invoice','repair_report'].map(t => <option value={t} key={t}>{label(t)}</option>)}</select>
        <button onClick={() => perform(`/dashboard/reviewer/claims/${item.id}/request-documents`, {document_types: [kind]})}>Request and notify customer</button></>}
      {item.duplicate && <div><h3>Exact duplicate warning</h3><p>Matching claim #{item.id === item.duplicate.claim_id ? item.duplicate.matching_claim_id : item.duplicate.claim_id}</p><div className="modal-actions">
        {['confirmed', 'false_positive'].map(status => <button key={status} disabled={mutation.isPending} onClick={() => perform('/dashboard/reviewer/duplicates/decision', {...item.duplicate, status})}>{status === 'confirmed' ? 'Confirm duplicate' : 'Reject warning'}</button>)}</div></div>}
    </>}</QueryState>{error && <p className="error-box" role="alert">{error}</p>}</section></div>;
}

function ModelHistory() {
  const [page, setPage] = useState(1), result = query('model-history', '/dashboard/analytics/models', {page, per_page: 8});
  return <QueryState result={result}>{data => <>{data.items.length ? data.items.map(item => <div className="list-row" key={`${item.model_type}-${item.model_name}-${item.version}`}>
    <span><strong>{item.model_name} · {item.version}</strong><br/><small>{item.model_type} · {date(item.created_at)} · {item.is_active ? 'Active' : 'Retired'}</small></span>
    <span className="muted">Accuracy {pct(item.metrics.accuracy)} · F1 {pct(item.metrics.f1)}<br/>Precision {pct(item.metrics.precision)} · Recall {pct(item.metrics.recall)}</span></div>) :
    <Empty message="No model evaluation metrics recorded."/>}<Pager meta={data} onPage={setPage}/></>}</QueryState>;
}

function Admin() {
  const result = query('admin', '/dashboard/admin'), [window, setWindow] = useState('30d'), [interval, setInterval] = useState('day');
  const period = query('trends', '/dashboard/trends', {window, interval}), [customerId, setCustomerId] = useState(''), navigate = useNavigate();
  return <QueryState result={result}>{data => <><div className="page-head"><div><span className="eyebrow">Platform intelligence</span>
    <h1>A clear view of every claim.</h1><p>Operations, warranty coverage, and model evidence in one workspace.</p></div>
    <form onSubmit={e => {e.preventDefault();if (Number(customerId) > 0) navigate(`/customer?user_id=${Number(customerId)}`);}} className="head-actions">
      <label className="sr-only" htmlFor="customer-id">Customer user ID</label><input id="customer-id" type="number" min="1" value={customerId} onChange={e => setCustomerId(e.target.value)} placeholder="Customer ID"/>
      <button disabled={!customerId}>View customer</button><Link className="outline-button" to="/reviewer">Reviewer workspace</Link></form></div>
    <div className="grid-cards"><Metric icon={FileText} title="Lifetime claims" value={data.claims.lifetime} note={`${data.claims.submitted_today} today · ${data.claims.submitted_month} this month`}/>
      <Metric icon={Shield} title="Approved claims" value={data.outcomes.valid} note={`${data.outcomes.valid_percentage}% of decided claims`}/>
      <Metric icon={ShieldAlert} title="Model disagreement" value={`${data.disagreement.rate}%`} note={`${data.disagreement.disagreements}/${data.disagreement.processed} jointly scored · ${data.disagreement.weekly_change_percentage_points ?? '—'} pp weekly`}/>
      <Metric icon={ClipboardList} title="Average confidence" value={pct(data.model_confidence.average)} note={`${data.model_confidence.samples} model observations`}/></div>
    <div className="two-col"><Panel title="Claim volume" caption="Submissions, approvals and recorded fraud events" action={<div className="head-actions"><div className="tabs" role="group" aria-label="Trend period">{['7d','30d','90d','12m'].map(value =>
      <button key={value} aria-pressed={window === value} onClick={() => setWindow(value)}>{value}</button>)}</div><label className="sr-only" htmlFor="trend-interval">Interval</label>
      <select id="trend-interval" value={interval} onChange={e => setInterval(e.target.value)}><option value="day">Daily</option><option value="week">Weekly</option><option value="month">Monthly</option></select></div>}>
      <QueryState result={period}>{trend => <Timeline series={trend.series} keys={['submitted','approved','fraud_events']} type="area"/>}</QueryState></Panel>
      <Panel title="Claim outcomes" caption="Current claim status"><Distribution values={data.charts.outcomes}/></Panel></div>
    <div className="grid-cards"><Metric icon={ShieldAlert} title="Manual review" value={data.outcomes.manual_review} note="Awaiting human review"/>
      <Metric icon={Search} title="Active duplicate alerts" value={data.duplicate_alerts.active} note={`${data.duplicate_alerts.confirmed} confirmed · ${data.duplicate_alerts.false_positive} dismissed`}/>
      <Metric icon={ShieldAlert} title="Fraud detection events" value={data.fraud.detected_claims} note={`${data.fraud.detection_rate}% of non-draft claims`}/>
      <Metric icon={Users} title="Pending assignments" value={data.reviewer_workload.pending_unassigned} note="Manual reviews without an owner"/></div>
    <div className="two-col"><Panel title="Warranty distribution" caption="Current product coverage"><Distribution values={data.warranties} type="bar"/></Panel>
      <Panel title="Risk proxy" caption="Latest model invalid probability; not a calibrated fraud score"><Distribution values={{High: data.fraud.high, Medium: data.fraud.medium, Low: data.fraud.low}} type="bar"/></Panel></div>
    <div className="two-col"><Panel title="Reviewer workload" caption="Average hours from submission to final review">{data.reviewer_workload.reviewers.length ? data.reviewer_workload.reviewers.map(item =>
      <div className="list-row" key={item.id}><strong>{item.name}</strong><span>{item.assigned_pending} pending · {item.average_review_hours == null ? 'No completed reviews' : `${item.average_review_hours} h average`}</span></div>) : <Empty message="No active reviewers yet."/>}</Panel>
      <Panel title="AI model monitoring" caption="Current and historical evaluation metrics"><ModelHistory/></Panel></div>
    <div className="two-col"><Panel title="Model confidence distribution"><Distribution values={data.charts.confidence_distribution} type="bar"/></Panel>
      <Panel title="Customer growth"><QueryState result={period}>{trend => <Timeline series={trend.series} keys={['customers']}/>}</QueryState></Panel></div>
    <div className="two-col"><Panel title="Warranty expirations"><QueryState result={period}>{trend => <Timeline series={trend.series} keys={['warranty_expirations']}/>}</QueryState></Panel></div></>}</QueryState>;
}

function Login({onLogin}) {
  const [email, setEmail] = useState(''), [password, setPassword] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  async function submit(event) {event.preventDefault();setBusy(true);setError('');try {const {data} = await api.post('/auth/login', {email, password});
    if (!['customer','reviewer','admin'].includes(data.user.role)) {setError('This dashboard is available to customers, reviewers and administrators.');
      setSession(data);try {await api.post('/auth/logout');} catch {/* Clear locally. */}setSession(null);return;}
    setSession(data);onLogin(data.user);} catch (err) {setError(errorMessage(err));} finally {setBusy(false);}}
  return <div className="login-screen"><div className="login-art"><div className="brand"><span className="brand-mark">AX</span>AssureX</div>
    <div><span className="eyebrow">Product protection, made simple</span><h1>Everything protected. All in one place.</h1><p>Monitor warranties, manage claims, and make confident decisions from one clear workspace.</p></div>
    <small>AssureX Claims Management System</small></div><div className="login-form"><span className="eyebrow">Welcome back</span><h2>Sign in to your dashboard</h2>
    <p className="muted">Your workspace is tailored to your account role.</p><form onSubmit={submit}><label htmlFor="email">Email address</label><input id="email" type="email" required autoComplete="username" value={email} onChange={e => setEmail(e.target.value)}/>
      <label htmlFor="password">Password</label><input id="password" type="password" required autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)}/>
      <button className="lime-button" disabled={busy}>{busy ? 'Signing in…' : 'Sign in →'}</button></form>{error && <p className="error-box" role="alert">{error}</p>}</div></div>;
}

function Shell({user, signOut, children}) {
  const role = user.role, location = useLocation(), home = role === 'admin' ? '/dashboard/admin' : role === 'reviewer' ? '/dashboard/reviewer' : '/dashboard/customer';
  const links = [{href: home, title: 'Overview', icon: LayoutDashboard},
    ...(role === 'customer' ? [{href: '/products', title: 'My products', icon: Box}, {href: '/claims', title: 'Claims', icon: ClipboardList}] : []),
    ...(role === 'reviewer' ? [{href: '#queue', title: 'Review queue', icon: ClipboardList}] : []),
    ...(role === 'admin' ? [{href: '/dashboard/reviewer', title: 'Reviewer workspace', icon: ShieldAlert}] : []),
    {href: '#notifications', title: 'Notifications', icon: Bell}];
  return <div className="shell"><a className="skip-link" href="#main">Skip to content</a><aside className="sidebar" aria-label="Sidebar"><Link className="brand" to={home.slice('/dashboard'.length)}><span className="brand-mark">AX</span>AssureX</Link>
    <nav aria-label="Main navigation">{links.map(({href,title,icon: Icon}, i) => href.startsWith('/dashboard/') ?
      <Link key={`${href}-${i}`} to={href.slice('/dashboard'.length)} className="nav-link" aria-current={location.pathname === href.slice('/dashboard'.length) ? 'page' : undefined}><Icon size={18}/>{title}</Link> :
      <a key={`${href}-${i}`} href={href} className="nav-link"><Icon size={18}/>{title}</a>)}</nav>
    <button className="nav-link signout" onClick={signOut}><LogOut size={18}/>Sign out</button></aside>
    <div className="workspace"><header className="topbar"><small>ASSUREX / {label(role)} WORKSPACE</small><div className="account"><a href="#notifications" aria-label="Notifications"><Bell size={18}/></a>
      <span className="avatar" aria-hidden="true">{(user.full_name || user.email).split(' ').map(p => p[0]).join('').slice(0,2).toUpperCase()}</span><span>{user.full_name}</span></div></header>
      <main id="main" className="content">{children}<section id="notifications" className="notifications"><Panel title="Notification center" caption="Updates and requests linked to your account"><NoticeList/></Panel></section></main></div></div>;
}

function DashboardApp() {
  const [user, setUser] = useState(() => getSession()?.user || null), client = useQueryClient(), navigate = useNavigate();
  useEffect(() => {onExpired(() => {client.clear();setUser(null);navigate('/');});return () => onExpired(() => {});}, [client,navigate]);
  async function signOut() {try {await api.post('/auth/logout');} catch {/* Session still cleared locally. */}setSession(null);setUser(null);client.clear();navigate('/');}
  if (!user) return <Login onLogin={setUser}/>;
  const home = user.role === 'admin' ? '/admin' : user.role === 'reviewer' ? '/reviewer' : '/customer';
  return <Shell user={user} signOut={signOut}><Routes>
    <Route path="/customer" element={['customer','admin'].includes(user.role) ? <Customer adminView={user.role === 'admin'}/> : <Navigate to={home} replace/>}/>
    <Route path="/reviewer" element={['reviewer','admin'].includes(user.role) ? <Reviewer role={user.role}/> : <Navigate to={home} replace/>}/>
    <Route path="/admin" element={user.role === 'admin' ? <Admin/> : <Navigate to={home} replace/>}/>
    <Route path="*" element={<Navigate to={home} replace/>}/></Routes></Shell>;
}

export function App() {return <BrowserRouter basename="/dashboard"><DashboardApp/></BrowserRouter>;}
