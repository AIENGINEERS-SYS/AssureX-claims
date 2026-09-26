import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {BrowserRouter, Link, Route, Routes, useNavigate, useParams} from 'react-router-dom';
import {useForm} from 'react-hook-form';
import {api, allPages, errorMessage, onExpired, setSession} from './api';
import {ReviewStep} from './components';
import Wizard from './Wizard';
import './styles.css';

function Login({signedIn, expectedUser}) {
  const {register, handleSubmit, formState: {isSubmitting}} = useForm();
  const [error, setError] = useState('');
  async function login(values) {
    setError('');
    try {
      const {data} = await api.post('/auth/login', values);
      if (data.user.role !== 'customer') {setError('Sign in with a customer account to manage your claims.'); return;}
      if (expectedUser && data.user.id !== expectedUser.id) {setError('Sign in with the same account to continue editing this draft.'); return;}
      setSession(data); signedIn(data.user);
    } catch (err) {setError(errorMessage(err));}
  }
  return <section className="panel login"><p className="eyebrow">WELCOME TO ASSUREX</p><h1>Sign in to your claims</h1>
    <p>Submit a claim or pick up where you left off.</p>{error && <p className="error" role="alert">{error}</p>}
    <form onSubmit={handleSubmit(login)}><label htmlFor="email">Email address</label><input id="email" type="email" autoComplete="username" required {...register('email')}/>
      <label htmlFor="password">Password</label><input id="password" type="password" autoComplete="current-password" required {...register('password')}/>
      <button className="primary" disabled={isSubmitting}>{isSubmitting ? 'Signing in…' : 'Sign in'}</button></form>
    <p>Need an account or a product? <a href="/products">Visit your products</a>.</p></section>;
}

function ClaimList() {
  const [claims, setClaims] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  useEffect(() => {let active = true; allPages('/claims/my').then(data => {if (active) setClaims(data);}).catch(err => {if (active) setError(errorMessage(err));}); return () => {active = false;};}, []);
  async function start() {
    setBusy(true); setError('');
    try {const {data} = await api.post('/claims/draft', {}); navigate(`/draft/${data.claim.id}`);}
    catch (err) {setError(errorMessage(err)); setBusy(false);}
  }
  return <><div className="title-row"><div><p className="eyebrow">HERE WHEN YOU NEED US</p><h1>My claims</h1><p>Manage your drafts and follow your submitted claims.</p></div>
    <button className="primary" onClick={start} disabled={busy}>{busy ? 'Starting…' : 'New claim'}</button></div>
    {error && <p className="error" role="alert">{error}</p>}
    {claims === null ? <p role="status">Loading claims…</p> : !claims.length ? <div className="panel empty"><h2>No claims yet</h2><p>Start a claim when you need support with a registered product.</p></div> :
      <div className="claim-list">{claims.map(claim => <article className="panel claim-card" key={claim.id}><div><span className={`badge ${claim.status === 'DRAFT' ? 'draft' : ''}`}>{claim.status.replaceAll('_', ' ')}</span>
        <h2>{claim.product?.name || 'New claim'}</h2><p>{claim.claim_id || `Draft · Step ${claim.current_step} of 4`}</p><small>Last saved {new Date(claim.updated_at).toLocaleString()}</small></div>
        <Link className="button" to={claim.status === 'DRAFT' ? `/draft/${claim.id}` : `/${claim.claim_id}`}>{claim.status === 'DRAFT' ? 'Resume draft' : 'View claim'}</Link></article>)}</div>}
  </>;
}

function ClaimDetails() {
  const {claimId} = useParams();
  const [claim, setClaim] = useState(null), [error, setError] = useState('');
  useEffect(() => {let active = true; api.get(`/claims/${encodeURIComponent(claimId)}`).then(({data}) => {if (active) setClaim(data.claim);}).catch(err => {if (active) setError(errorMessage(err));}); return () => {active = false;};}, [claimId]);
  return <>{error && <p role="alert" className="error">{error}</p>}{claim ? <><div className="confirmation"><p className="eyebrow">WE HAVE YOUR CLAIM</p><h1>{claim.claim_id}</h1>
    <p>Status: <strong>{claim.status.replaceAll('_', ' ')}</strong></p><p>Submitted {claim.submitted_at ? new Date(claim.submitted_at).toLocaleString() : claim.submission_date || 'Previously'}</p>
    <Link to="/">Back to my claims</Link></div><div className="panel"><ReviewStep claim={claim} product={claim.product} values={claim}/></div></> : !error && <p role="status">Loading claim…</p>}</>;
}

function App() {
  const [user, setUser] = useState(null);
  const [needsLogin, setNeedsLogin] = useState(false);
  useEffect(() => {onExpired(() => setNeedsLogin(true)); return () => onExpired(() => {});}, []);
  async function signOut() {
    const detail = {waitFor: []};
    window.dispatchEvent(new CustomEvent('assurex:before-signout', {detail}));
    try {await Promise.all(detail.waitFor);} catch {return;}
    try {await api.post('/auth/logout');} catch { /* Local session still ends. */ }
    finally {setSession(null); setUser(null); setNeedsLogin(false);}
  }
  return <BrowserRouter basename="/claims"><a className="skip-link" href="#main">Skip to content</a><header><a className="brand" href="/products">Assure<span>X</span></a>
    <nav aria-label="Main navigation"><a href="/products">Products</a><Link to="/" aria-current="page">Claims</Link></nav>
    {user && <div className="account"><span>{user.full_name}</span><button onClick={signOut}>Sign out</button></div>}</header>
    {needsLogin && user && <div className="reauth" role="dialog" aria-modal="true" aria-label="Sign in again"><Login expectedUser={user} signedIn={() => setNeedsLogin(false)}/></div>}
    <main id="main" inert={needsLogin && user ? true : undefined}>{user ? <Routes><Route path="/" element={<ClaimList/>}/><Route path="/draft/:id" element={<Wizard/>}/>
      <Route path="/:claimId" element={<ClaimDetails/>}/><Route path="*" element={<p>Page not found. <Link to="/">My claims</Link></p>}/></Routes> : <Login signedIn={setUser}/>}</main>
    <footer>AssureX · Product protection, made simple</footer></BrowserRouter>;
}
createRoot(document.getElementById('root')).render(<App/>);
