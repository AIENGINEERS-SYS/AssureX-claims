import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {BrowserRouter, Link, Navigate, Route, Routes, useLocation, useNavigate, useParams} from 'react-router-dom';
import {Box, Check, CheckCircle2, ChevronLeft, ChevronRight, Eye, EyeOff, LockKeyhole, LogIn, LogOut, Mail, PackagePlus, Pencil, Plus, ShieldCheck, Trash2, UserRound} from 'lucide-react';
import {api, errorMessage, getSession, setSession} from '../claims/api';
import './products.css';

const productFields = ['name', 'brand', 'category', 'model_number', 'serial_number', 'purchase_date', 'purchase_price', 'retailer'];
const emptyProduct = {name: '', brand: '', category: '', model_number: '', serial_number: '', purchase_date: '',
  purchase_price: '', retailer: '', warranty_duration: 12, warranty_duration_unit: 'months', warranty_provider: '',
  warranty_start_date: '', coverage: '', exclusions: '', service_center_conditions: ''};
const canceled = error => error?.code === 'ERR_CANCELED';

function formatDate(value) {
  if (!value) return 'Not specified';
  return new Intl.DateTimeFormat('en', {day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC'})
    .format(new Date(`${value}T00:00:00Z`));
}

function nextDay(value) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString().slice(0, 10);
}

function Status({children}) {
  const kind = {'Active': 'active', 'Near Expiry': 'near', 'Expired': 'expired', 'Extended Warranty': 'extended',
    'Not Started': 'scheduled', 'No Warranty': 'none'}[children] || 'none';
  return <span className={`status status-${kind}`}>{children}</span>;
}

function ErrorBox({error}) {
  return error ? <div className="form-error" role="alert">{error}</div> : null;
}

function PasswordControl({label, name, value, onChange, autoComplete, describedBy}) {
  const [visible, setVisible] = useState(false);
  return <label className="auth-field">
    <span className="auth-label-row"><span>{label}</span></span>
    <span className="auth-input-shell">
      <LockKeyhole size={17} aria-hidden="true" />
      <input
        name={name}
        type={visible ? 'text' : 'password'}
        value={value}
        onChange={onChange}
        autoComplete={autoComplete}
        required
        aria-describedby={describedBy}
      />
      <button
        type="button"
        className="password-toggle"
        onClick={() => setVisible(current => !current)}
        aria-label={visible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
        aria-pressed={visible}
      >
        {visible ? <EyeOff size={17} /> : <Eye size={17} />}
      </button>
    </span>
  </label>;
}

function Auth({onAuth}) {
  const [mode, setMode] = useState('login');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const passwordBytes = useMemo(() => new TextEncoder().encode(password).length, [password]);
  const passwordSignals = useMemo(() => ({
    length: password.length >= 12,
    byteLimit: passwordBytes <= 72,
    mixedCase: /[a-z]/.test(password) && /[A-Z]/.test(password),
    numberOrSymbol: /[0-9]|[^A-Za-z0-9]/.test(password),
  }), [password, passwordBytes]);
  const strength = [passwordSignals.length, passwordSignals.mixedCase, passwordSignals.numberOrSymbol,
    password.length >= 16].filter(Boolean).length;
  const strengthLabel = password ? ['Weak', 'Fair', 'Good', 'Strong', 'Very strong'][strength] : 'Not set';
  const passwordsMatch = !confirmPassword || password === confirmPassword;

  function changeMode(nextMode) {
    setMode(nextMode);
    setError('');
    setPassword('');
    setConfirmPassword('');
  }

  async function submit(event) {
    event.preventDefault();
    setError('');

    const form = event.currentTarget;
    if (!form.reportValidity()) return;

    if (mode === 'register') {
      if (!passwordSignals.length || !passwordSignals.byteLimit) {
        setError('Use a password with at least 12 characters and no more than 72 UTF-8 bytes.');
        return;
      }
      if (password !== confirmPassword) {
        setError('The passwords do not match. Re-enter the confirmation password.');
        return;
      }
    }

    setBusy(true);
    const values = Object.fromEntries(new FormData(form));
    delete values.confirm_password;
    try {
      if (mode === 'register') await api.post('/auth/register', values);
      const {data} = await api.post('/auth/login', {email: values.email, password: values.password});
      if (!['customer', 'admin'].includes(data.user.role)) {
        setSession(data);
        try {await api.post('/auth/logout');} catch {/* The local session must still be cleared. */}
        finally {setSession(null);}
        throw new Error('Product management is available to customer and administrator accounts.');
      }
      setSession(data);
      onAuth(data.user);
    } catch (requestError) {
      if (requestError.response) setError(errorMessage(requestError));
      else if (requestError.isAxiosError) setError('Unable to reach the AssureX API. Check your connection and try again.');
      else setError(requestError.message || 'Something went wrong. Please try again.');
    } finally {
      setBusy(false);
    }
  }

  return <div className="products-auth">
    <div className="auth-shell">
      <aside className="auth-story" aria-label="About AssureX">
        <a className="auth-brand" href="/products" aria-label="AssureX home">
          <span className="brand-mark" aria-hidden="true">A</span>
          <span>Assure<span>X</span></span>
        </a>
        <div className="auth-story-copy">
          <p className="eyebrow">CLAIMS MADE CLEAR</p>
          <h2>Keep protection details organised before you need them.</h2>
          <p>One workspace for products, warranties, documents and claims, without hunting through receipts when something breaks.</p>
          <ul className="auth-benefits">
            <li><CheckCircle2 size={18} /><span><strong>Track products and warranty cover</strong><small>Keep purchase and protection details in one place.</small></span></li>
            <li><CheckCircle2 size={18} /><span><strong>Prepare claims with less friction</strong><small>Move from product records to claim evidence cleanly.</small></span></li>
            <li><CheckCircle2 size={18} /><span><strong>Review decisions with context</strong><small>Keep claim information and supporting analysis together.</small></span></li>
          </ul>
        </div>
        <p className="auth-story-footer"><ShieldCheck size={16} /> Your account credentials are handled by the AssureX API.</p>
      </aside>

      <section className="auth-stage">
        <div className="panel auth-panel">
          <div className="auth-heading">
            <p className="eyebrow">{mode === 'register' ? 'CREATE YOUR ASSUREX ACCOUNT' : 'WELCOME BACK'}</p>
            <h1>{mode === 'register' ? 'Create your account' : 'Sign in to AssureX'}</h1>
            <p>{mode === 'register'
              ? 'Start with your account details. You can add products and warranty information next.'
              : 'Access your products, warranties and claim workspace.'}</p>
          </div>

          <div className="auth-mode-switch" role="tablist" aria-label="Account access">
            <button type="button" role="tab" aria-selected={mode === 'login'} className={mode === 'login' ? 'active' : ''} onClick={() => changeMode('login')}>Sign in</button>
            <button type="button" role="tab" aria-selected={mode === 'register'} className={mode === 'register' ? 'active' : ''} onClick={() => changeMode('register')}>Create account</button>
          </div>

          <form id="auth-form" onSubmit={submit} noValidate>
            {mode === 'register' && <label className="auth-field">
              <span className="auth-label-row"><span>Full name</span><small>Required</small></span>
              <span className="auth-input-shell">
                <UserRound size={17} aria-hidden="true" />
                <input name="full_name" autoComplete="name" required maxLength="201" placeholder="e.g. Stephen Ordu" />
              </span>
            </label>}

            <label className="auth-field">
              <span className="auth-label-row"><span>Email address</span><small>Required</small></span>
              <span className="auth-input-shell">
                <Mail size={17} aria-hidden="true" />
                <input name="email" type="email" autoComplete="username" required maxLength="320" inputMode="email" placeholder="you@example.com" />
              </span>
            </label>

            <PasswordControl
              label="Password"
              name="password"
              value={password}
              onChange={event => setPassword(event.target.value)}
              autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
              describedBy={mode === 'register' ? 'password-guidance' : undefined}
            />

            {mode === 'register' && <>
              <div className="password-health" id="password-guidance" aria-live="polite">
                <div className="password-strength-row"><span>Password strength</span><strong>{strengthLabel}</strong></div>
                <div className="password-strength" aria-hidden="true">
                  {[1, 2, 3, 4].map(level => <span key={level} className={strength >= level ? 'active' : ''} />)}
                </div>
                <div className="password-rules">
                  <span className={passwordSignals.length ? 'met' : ''}><Check size={13} /> At least 12 characters</span>
                  <span className={passwordSignals.byteLimit && password ? 'met' : ''}><Check size={13} /> Maximum 72 UTF-8 bytes</span>
                  <span className={passwordSignals.mixedCase ? 'met' : ''}><Check size={13} /> Upper & lowercase recommended</span>
                  <span className={passwordSignals.numberOrSymbol ? 'met' : ''}><Check size={13} /> Number or symbol recommended</span>
                </div>
              </div>

              <PasswordControl
                label="Retype password"
                name="confirm_password"
                value={confirmPassword}
                onChange={event => setConfirmPassword(event.target.value)}
                autoComplete="new-password"
                describedBy="password-match-status"
              />
              <p id="password-match-status" className={`password-match ${confirmPassword ? (passwordsMatch ? 'match' : 'mismatch') : ''}`}>
                {confirmPassword ? (passwordsMatch ? 'Passwords match.' : 'Passwords do not match yet.') : 'Retype your password to confirm it.'}
              </p>
            </>}

            <ErrorBox error={error} />

            <button id="auth-submit" type="submit" className="button full-width auth-submit" disabled={busy}>
              <LogIn size={17} />
              {busy ? (mode === 'register' ? 'Creating account...' : 'Signing in...') : mode === 'register' ? 'Create account' : 'Sign in'}
            </button>
          </form>

          <div className="auth-assurance">
            <ShieldCheck size={17} aria-hidden="true" />
            <span>{mode === 'register'
              ? 'Use a unique password you do not reuse on other services.'
              : 'Only sign in on a device you trust, especially on shared networks.'}</span>
          </div>

          <p className="auth-toggle">{mode === 'register' ? 'Already have an account?' : 'New to AssureX?'}{' '}
            <button type="button" className="text-button" onClick={() => changeMode(mode === 'register' ? 'login' : 'register')}>
              {mode === 'register' ? 'Sign in instead' : 'Create an account'}
            </button>
          </p>
        </div>
      </section>
    </div>
  </div>;
}

function Layout({user, onSignOut}) {
  return <div className="products-shell"><a className="skip-link" href="#main">Skip to content</a>
    <aside className="sidebar" aria-label="Main navigation">
      <Link className="brand" to="/"><span className="brand-mark" aria-hidden="true">A</span>Assure<span>X</span></Link>
      <p className="nav-caption">YOUR WORKSPACE</p>
      <nav><Link className="nav-item current" to="/" aria-current="page"><Box size={19} /> Products & warranties</Link></nav>
      <a className="nav-item secondary-nav" href="/claims"><ShieldCheck size={19} /> Claims</a>
      <div className="sidebar-note"><span className="shield" aria-hidden="true"><Check size={20} /></span><strong>A little more peace of mind.</strong><p>Your products. Your cover.<br />Everything in one place.</p></div>
      <div className="sidebar-footer">ASSUREX <span>Claims made clear.</span></div>
    </aside>
    <div className="workspace"><header className="topbar"><span className="breadcrumb">Workspace <span>/</span> <strong>Products & warranties</strong></span>
      <div className="account"><span>{user.full_name}</span><button id="sign-out" className="button small secondary" onClick={onSignOut}><LogOut size={15} />Sign out</button></div></header>
      <main id="main" tabIndex="-1"><Routes>
        <Route path="/" element={<ProductList user={user} />} />
        <Route path="/new" element={<ProductForm />} />
        <Route path="/:productId/edit" element={<ProductForm edit />} />
        <Route path="/:productId" element={<ProductDetails />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes></main>
      <footer className="page-footer">A clearer view of the things you rely on.<span>AssureX - Product protection</span></footer>
    </div>
  </div>;
}

function ProductList({user}) {
  const [filters, setFilters] = useState({q: '', category: '', warranty_status: '', sort: 'newest', page: 1});
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true, controller;
    const refresh = () => {
      if (document.visibilityState === 'hidden') return;
      controller?.abort();
      controller = new AbortController();
      setError('');
      const params = Object.fromEntries(Object.entries({...filters, per_page: 10}).filter(([, value]) => value !== ''));
      api.get('/products', {params, signal: controller.signal})
        .then(response => {if (active) setData(response.data);})
        .catch(requestError => {if (active && !canceled(requestError)) setError(errorMessage(requestError));});
    };
    const timer = setTimeout(refresh, filters.q ? 300 : 0);
    const interval = setInterval(refresh, 60000);
    const visible = () => {if (document.visibilityState === 'visible') refresh();};
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', visible);
    return () => {active = false;clearTimeout(timer);clearInterval(interval);controller?.abort();
      window.removeEventListener('focus', refresh);document.removeEventListener('visibilitychange', visible);};
  }, [filters]);

  function change(name, value) {
    setFilters(current => ({...current, [name]: value, page: name === 'page' ? value : 1}));
  }

  const counts = data?.summary?.by_status || {};
  const active = (counts.Active || 0) + (counts['Extended Warranty'] || 0) + (counts['Near Expiry'] || 0);
  const pages = Math.max(1, Math.ceil((data?.total || 0) / (data?.per_page || 10)));
  const stats = [['Registered products', data?.summary?.total || 0, 'All in one place'], ['Currently covered', active, 'Original and extended'],
    ['Near expiry', counts['Near Expiry'] || 0, `Within ${data?.near_expiry_days || 30} days`], ['Expired warranties', counts.Expired || 0, 'Review your protection']];

  return <><div className="page-heading"><div><p className="eyebrow">YOUR PRODUCT PORTFOLIO</p><h1>Products & warranties</h1><p>A clear view of what you own. Confidence in what is covered.</p></div>
    <Link className="button" to="/new"><PackagePlus size={17} />Register product</Link></div>
    <div className="stats">{stats.map(([label, value, note]) => <div className="stat" key={label}><span className="label">{label}<span className="stat-icon"><Box size={15} /></span></span><strong>{value}</strong><small>{note}</small></div>)}</div>
    <ErrorBox error={error} />
    <section className="panel" aria-labelledby="list-title"><div className="panel-heading"><div><h2 id="list-title">{user.role === 'admin' ? 'All registered products' : 'Your products'} <span className="count-pill">{data?.total || 0}</span></h2><p>Warranty information updates automatically as time passes.</p></div></div>
      <div className="filters" id="filters"><label>Search products<input name="q" type="search" value={filters.q} onChange={event => change('q', event.target.value)} placeholder="Name, brand, model or serial number" /></label>
        <label>Category<select value={filters.category} onChange={event => change('category', event.target.value)}><option value="">All categories</option>{data?.categories?.map(category => <option key={category}>{category}</option>)}</select></label>
        <label>Warranty status<select name="status" value={filters.warranty_status} onChange={event => change('warranty_status', event.target.value)}><option value="">All statuses</option>{['Active', 'Near Expiry', 'Expired', 'Extended Warranty', 'Not Started', 'No Warranty'].map(status => <option key={status}>{status}</option>)}</select></label>
        <label>Sort by<select value={filters.sort} onChange={event => change('sort', event.target.value)}>{[['newest', 'Newest first'], ['oldest', 'Oldest first'], ['name', 'Product name'], ['purchase_date', 'Purchase date'], ['expiry_date', 'Warranty expiry']].map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label></div>
      {!data && !error ? <div className="loading" role="status">Loading products...</div> : data?.items?.length ? <div className="table-wrap"><table><thead><tr><th>Product</th><th>Serial number</th><th>Purchased</th><th>Warranty</th><th>Status</th><th>Details</th></tr></thead><tbody>
        {data.items.map(product => <tr key={product.id}><td><Link className="product-cell" to={`/${product.id}`}><span className="product-icon"><Box size={21} /></span><span><strong>{product.name}</strong><span className="subline">{product.brand} - {product.model_number}</span></span></Link></td><td className="serial">{product.serial_number}</td><td>{formatDate(product.purchase_date)}<span className="subline">{product.product_age} old</span></td><td>{formatDate(product.warranty_expiry)}<span className="subline">{product.warranty_remaining}</span></td><td><Status>{product.warranty_status}</Status></td><td><Link className="row-link" to={`/${product.id}`} aria-label={`View ${product.name}`}><ChevronRight size={18} /></Link></td></tr>)}</tbody></table></div>
        : data && <div className="empty-state"><Box className="empty-icon" /><h2>{data.summary.total ? 'No matching products' : 'Your protection starts here'}</h2><p>{data.summary.total ? 'Try another search or clear the filters.' : 'Register your first product and we will calculate its warranty dates.'}</p>{data.summary.total ? <button className="button secondary" data-action="clear-filters" onClick={() => setFilters({q: '', category: '', warranty_status: '', sort: 'newest', page: 1})}>Clear filters</button> : <Link className="button" to="/new">Register your first product</Link>}</div>}
      <div className="pagination"><span>{data?.total ? `${(data.page - 1) * data.per_page + 1}-${Math.min(data.page * data.per_page, data.total)} of ${data.total} products` : '0 products'}</span><div><button className="button small secondary" disabled={filters.page <= 1} onClick={() => change('page', filters.page - 1)}><ChevronLeft size={15} />Previous</button><span>{filters.page} / {pages}</span><button className="button small secondary" disabled={filters.page >= pages} onClick={() => change('page', filters.page + 1)}>Next<ChevronRight size={15} /></button></div></div>
    </section></>;
}

function ProductForm({edit = false}) {
  const {productId} = useParams();
  const navigate = useNavigate();
  const [values, setValues] = useState(emptyProduct);
  const [preview, setPreview] = useState('Enter a purchase date and duration');
  const [busy, setBusy] = useState(edit);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!edit) return;
    const controller = new AbortController();
    api.get(`/products/${productId}`, {signal: controller.signal}).then(({data}) => setValues(current => ({...current, ...Object.fromEntries(productFields.map(key => [key, data.product[key] ?? '']))})))
      .catch(requestError => {if (!canceled(requestError)) setError(errorMessage(requestError));})
      .finally(() => {if (!controller.signal.aborted) setBusy(false);});
    return () => controller.abort();
  }, [edit, productId]);

  useEffect(() => {
    if (edit) return;
    const start_date = values.warranty_start_date || values.purchase_date;
    const duration = Number(values.warranty_duration);
    if (!start_date || !Number.isInteger(duration) || duration < 1) {setPreview('Enter a valid date and duration');return;}
    const controller = new AbortController();
    const timer = setTimeout(() => api.post('/warranties/preview', {start_date, duration, duration_unit: values.warranty_duration_unit}, {signal: controller.signal})
      .then(({data}) => setPreview(formatDate(data.expiry_date)))
      .catch(requestError => {if (!canceled(requestError)) setPreview(errorMessage(requestError));}), 250);
    return () => {clearTimeout(timer);controller.abort();};
  }, [edit, values.purchase_date, values.warranty_start_date, values.warranty_duration, values.warranty_duration_unit]);

  function update(event) {setValues(current => ({...current, [event.target.name]: event.target.value}));}
  async function submit(event) {
    event.preventDefault();setBusy(true);setError('');
    const payload = Object.fromEntries(productFields.map(key => [key, values[key]]));
    if (!edit) Object.assign(payload, {warranty_duration: Number(values.warranty_duration), warranty_duration_unit: values.warranty_duration_unit,
      coverage: values.coverage, exclusions: values.exclusions.split('\n').map(item => item.trim()).filter(Boolean), service_center_conditions: values.service_center_conditions});
    if (!edit && values.warranty_provider.trim()) payload.warranty_provider = values.warranty_provider.trim();
    if (!edit && values.warranty_start_date) payload.warranty_start_date = values.warranty_start_date;
    try {
      const {data} = edit ? await api.put(`/products/${productId}`, payload) : await api.post('/products', payload);
      navigate(`/${data.product.id}`, {replace: true, state: {notice: data.message}});
    } catch (requestError) {setError(errorMessage(requestError));} finally {setBusy(false);}
  }

  if (busy && edit && !values.name) return <div className="loading">Loading product...</div>;
  return <div className="form-layout"><Link to={edit ? `/${productId}` : '/'} className="back-link"><ChevronLeft size={15} />{edit ? 'Back to product' : 'All products'}</Link>
    <div className="form-intro"><p className="eyebrow">{edit ? 'PRODUCT INFORMATION' : 'A LITTLE MORE PEACE OF MIND'}</p><h1>{edit ? 'Edit product' : 'Register a product'}</h1><p>{edit ? 'Keep your product information up to date. Warranty records are managed separately.' : 'Add your product and purchase details. We will calculate the warranty dates.'}</p></div>
    <form id="product-form" className="panel" onSubmit={submit}><FormSection title="Product details" step="1"><div className="fields">
      <Field label="Product name" name="name" value={values.name} onChange={update} maxLength="200" required /><Field label="Brand" name="brand" value={values.brand} onChange={update} maxLength="100" required />
      <Field label="Category" name="category" value={values.category} onChange={update} maxLength="100" required /><Field label="Model" name="model_number" value={values.model_number} onChange={update} maxLength="100" required />
      <Field label="Serial number" name="serial_number" value={values.serial_number} onChange={update} maxLength="150" required />
    </div></FormSection><FormSection title="Purchase details" step="2"><div className="fields"><Field label="Purchase date" name="purchase_date" type="date" value={values.purchase_date} onChange={update} required />
      <Field label="Purchase price" name="purchase_price" type="number" min="0" max="9999999999.99" step="0.01" value={values.purchase_price} onChange={update} required /><Field label="Retailer" name="retailer" value={values.retailer} onChange={update} maxLength="200" required /></div></FormSection>
      {!edit && <FormSection title="Original warranty" step="3"><div className="fields"><Field label="Duration" name="warranty_duration" type="number" min="1" max={values.warranty_duration_unit === 'years' ? 100 : 1200} value={values.warranty_duration} onChange={update} required />
        <label>Duration unit<select name="warranty_duration_unit" value={values.warranty_duration_unit} onChange={update}><option value="months">Months</option><option value="years">Years</option></select></label>
        <Field label="Provider (optional)" name="warranty_provider" value={values.warranty_provider} onChange={update} maxLength="200" /><Field label="Start date (optional)" name="warranty_start_date" type="date" value={values.warranty_start_date} onChange={update} />
        <TextArea label="Coverage (optional)" name="coverage" value={values.coverage} onChange={update} /><TextArea label="Exclusions (optional, one per line)" name="exclusions" value={values.exclusions} onChange={update} /><TextArea label="Service-center conditions (optional)" name="service_center_conditions" value={values.service_center_conditions} onChange={update} /></div>
        <div className="calculation"><span>Calculated warranty expiry</span><strong id="expiry-preview">{preview}</strong></div></FormSection>}
      <div className="form-section"><ErrorBox error={error} /><p className="fine-print">{edit ? 'Products already used in claims retain protected identity fields.' : 'Expiry is calculated by the API from the start date and duration.'}</p></div>
      <div className="form-actions"><Link className="button secondary" to={edit ? `/${productId}` : '/'}>Cancel</Link><button type="submit" className="button" disabled={busy}>{busy ? 'Saving...' : edit ? 'Save changes' : 'Register product'}</button></div>
    </form></div>;
}

function FormSection({title, step, children}) {return <section className="form-section"><div className="section-heading"><span className="step-number">{step}</span><h2>{title}</h2></div>{children}</section>;}
function Field({label, ...props}) {return <label>{label}<input {...props} /></label>;}
function TextArea({label, ...props}) {return <label className="wide">{label}<textarea maxLength="10000" {...props} /></label>;}

function ProductDetails() {
  const {productId} = useParams();
  const navigate = useNavigate(), location = useLocation(), request = useRef(null);
  const [product, setProduct] = useState(null);
  const [warranty, setWarranty] = useState(undefined);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState(() => location.state?.notice || '');
  const load = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    try {
      const {data} = await api.get(`/products/${productId}`, {signal: controller.signal});
      if (!controller.signal.aborted) {setProduct(data.product);setError('');}
    } catch (requestError) {
      if (!canceled(requestError)) setError(errorMessage(requestError));
    } finally {
      if (request.current === controller) request.current = null;
    }
  }, [productId]);
  useEffect(() => {
    load();
    const interval = setInterval(() => {if (document.visibilityState === 'visible') load();}, 60000);
    const visible = () => {if (document.visibilityState === 'visible') load();};
    window.addEventListener('focus', visible);
    document.addEventListener('visibilitychange', visible);
    return () => {clearInterval(interval);request.current?.abort();window.removeEventListener('focus', visible);
      document.removeEventListener('visibilitychange', visible);};
  }, [load]);

  async function removeProduct() {
    if (!window.confirm(`Delete ${product.name} and its warranty records?`)) return;
    try {await api.delete(`/products/${product.id}`);navigate('/', {replace: true});} catch (requestError) {setError(errorMessage(requestError));}
  }
  async function removeWarranty(item) {
    if (!window.confirm('Delete this warranty record?')) return;
    try {const {data} = await api.delete(`/warranties/${item.id}`);setNotice(data.message);await load();} catch (requestError) {setError(errorMessage(requestError));}
  }

  if (!product && !error) return <div className="loading">Loading product...</div>;
  if (!product) return <ErrorBox error={error} />;
  const current = product.current_warranty;
  return <><Link className="back-link" to="/"><ChevronLeft size={15} />All products</Link>{notice && <div className="notice" role="status">{notice}</div>}<ErrorBox error={error} />
    <div className="page-heading detail-page"><div className="detail-heading"><span className="product-icon"><Box size={30} /></span><div><p className="eyebrow">PRODUCT DETAILS</p><h1>{product.name}</h1><p>{product.brand} - {product.model_number} - {product.category}</p></div></div>
      <div className="detail-actions"><Link className="button secondary" to={`/${product.id}/edit`}><Pencil size={16} />Edit product</Link><button className="button" data-action="add-warranty" onClick={() => setWarranty(null)}><Plus size={16} />{product.warranties.length ? 'Extend warranty' : 'Add warranty'}</button></div></div>
    <div className="detail-grid"><div><section className="panel"><div className="panel-heading"><h2>Product information</h2></div><div className="panel-body"><dl className="data-grid">
      <Detail label="Product name" value={product.name} /><Detail label="Brand" value={product.brand} /><Detail label="Category" value={product.category} /><Detail label="Model" value={product.model_number} /><Detail label="Serial number" value={product.serial_number} /><Detail label="Purchase date" value={formatDate(product.purchase_date)} /><Detail label="Purchase price" value={product.purchase_price} /><Detail label="Retailer" value={product.retailer} /><Detail label="Product age" value={product.product_age} /><Detail label="Original warranty duration" value={product.warranty_duration ? `${product.warranty_duration} ${product.warranty_duration_unit}` : 'No original warranty'} />
    </dl></div></section><section className="panel"><div className="panel-heading"><div><h2>Warranty history <span className="count-pill">{product.warranties.length}</span></h2><p>Original protection and every additional period of cover.</p></div></div>
      {product.warranties.length ? product.warranties.map(item => <article className="warranty-card" key={item.id}><header><div><h3>{item.is_extended ? 'Extended warranty' : 'Original warranty'}</h3><span className="subline">{item.provider} - {item.duration} {item.duration_unit}</span></div><Status>{item.warranty_status}</Status></header><dl className="data-grid"><Detail label="Start date" value={formatDate(item.start_date)} /><Detail label="Expiry date" value={formatDate(item.expiry_date)} /><Detail wide label="Coverage" value={item.coverage || 'Not specified'} /><Detail wide label="Exclusions" value={item.exclusions?.join('\n') || 'Not specified'} /><Detail wide label="Service-center conditions" value={item.service_center_conditions || 'Not specified'} /></dl><div className="actions"><button className="text-button" onClick={() => setWarranty(item)}>Edit warranty</button><button className="text-button danger" onClick={() => removeWarranty(item)}>Delete warranty</button></div></article>) : <div className="panel-body"><p className="muted">No warranty is registered yet.</p><button className="button secondary" onClick={() => setWarranty(null)}>Add original warranty</button></div>}
    </section><div className="danger-zone"><span>Products used in claims are retained for your records.</span><button className="text-button danger" onClick={removeProduct}><Trash2 size={15} />Delete product</button></div></div>
      <div><section className="panel"><div className="coverage-hero"><Status>{product.warranty_status}</Status><h2>{product.warranty_remaining}</h2><p>{current ? `Warranty expiry - ${formatDate(current.expiry_date)}` : 'Add a warranty to see your protection.'}</p>{current && <><progress max="100" value={current.progress_percent}>{current.progress_percent}%</progress><div className="progress-caption"><span>{formatDate(current.start_date)}</span><span>{formatDate(current.expiry_date)}</span></div></>}</div><div className="panel-body"><dl className="data-grid"><Detail label="Provider" value={current?.provider} /><Detail label="As of (UTC)" value={formatDate(product.server_date)} /></dl></div></section>
      <section className="panel"><div className="panel-heading"><h2>Your warranty timeline</h2></div><div className="panel-body"><ol className="timeline">{product.timeline.map((event, index) => <li className={event.kind === 'today' ? 'today' : ''} key={`${event.date}-${index}`}><span>{event.label}</span><time dateTime={event.date}>{formatDate(event.date)}</time></li>)}</ol></div></section></div></div>
    {warranty !== undefined && <WarrantyForm product={product} warranty={warranty} onClose={() => setWarranty(undefined)} onSaved={async message => {setWarranty(undefined);setNotice(message);await load();}} />}
  </>;
}

function Detail({label, value, wide = false}) {return <div className={wide ? 'wide' : ''}><dt>{label}</dt><dd className="coverage-text">{value || 'Not specified'}</dd></div>;}

function WarrantyForm({product, warranty, onClose, onSaved}) {
  const latest = useMemo(() => product.warranties.map(item => item.expiry_date).sort().at(-1), [product.warranties]);
  const [values, setValues] = useState({provider: warranty?.provider || product.brand, start_date: warranty?.start_date || (latest ? nextDay(latest) : product.purchase_date), duration: warranty?.duration || 12,
    duration_unit: warranty?.duration_unit || 'months', coverage: warranty?.coverage || '', exclusions: warranty?.exclusions?.join('\n') || '', service_center_conditions: warranty?.service_center_conditions || ''});
  const [preview, setPreview] = useState('Calculating...');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => api.post('/warranties/preview', {start_date: values.start_date, duration: Number(values.duration), duration_unit: values.duration_unit}, {signal: controller.signal})
      .then(({data}) => setPreview(formatDate(data.expiry_date)))
      .catch(requestError => {if (!canceled(requestError)) setPreview(errorMessage(requestError));}), 250);
    return () => {clearTimeout(timer);controller.abort();};
  }, [values.start_date, values.duration, values.duration_unit]);
  const update = event => setValues(current => ({...current, [event.target.name]: event.target.value}));
  async function submit(event) {
    event.preventDefault();setBusy(true);setError('');
    const payload = {...values, duration: Number(values.duration), exclusions: values.exclusions.split('\n').map(item => item.trim()).filter(Boolean)};
    try {const {data} = warranty ? await api.put(`/warranties/${warranty.id}`, payload) : await api.post(`/products/${product.id}/warranties`, payload);await onSaved(data.message);}
    catch (requestError) {setError(errorMessage(requestError));} finally {setBusy(false);}
  }
  return <div className="modal-backdrop" role="presentation"><section className="warranty-modal" role="dialog" aria-modal="true" aria-labelledby="warranty-title"><div className="dialog-heading"><div><p className="eyebrow">{product.name}</p><h2 id="warranty-title">{warranty ? 'Edit warranty' : product.warranties.length ? 'Add an extended warranty' : 'Add original warranty'}</h2></div><button className="icon-button" onClick={onClose} aria-label="Close warranty form">x</button></div>
    <p className="muted">Each warranty period must follow the original without overlapping another period.</p><form id="warranty-form" onSubmit={submit}><div className="fields"><Field label="Provider" name="provider" value={values.provider} onChange={update} required /><Field label="Start date" name="start_date" type="date" min={product.purchase_date} value={values.start_date} onChange={update} required /><Field label="Duration" name="duration" type="number" min="1" max={values.duration_unit === 'years' ? 100 : 1200} value={values.duration} onChange={update} required /><label>Duration unit<select name="duration_unit" value={values.duration_unit} onChange={update}><option value="months">Months</option><option value="years">Years</option></select></label><TextArea label="Coverage" name="coverage" value={values.coverage} onChange={update} /><TextArea label="Exclusions (one per line)" name="exclusions" value={values.exclusions} onChange={update} /><TextArea label="Service-center conditions" name="service_center_conditions" value={values.service_center_conditions} onChange={update} /></div><div className="calculation"><span>Calculated expiry date</span><strong id="expiry-preview">{preview}</strong></div><ErrorBox error={error} /><div className="form-actions"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="submit" className="button" disabled={busy}>{busy ? 'Saving...' : warranty ? 'Save warranty' : 'Add warranty'}</button></div></form></section></div>;
}

export default function ProductsApp() {
  const [user, setUser] = useState(() => getSession()?.user || null);
  async function signOut() {
    try {await api.post('/auth/logout');} catch { /* The local session still ends. */ }
    setSession(null);setUser(null);
  }
  return <BrowserRouter basename="/products">{user ? <Layout user={user} onSignOut={signOut} /> : <Auth onAuth={setUser} />}</BrowserRouter>;
}
