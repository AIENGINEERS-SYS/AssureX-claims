import React, {useEffect, useState} from 'react';
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query';
import {api, errorMessage} from '../claims/api';
import './search.css';

export const label = value => String(value ?? '').replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
const endpoint = scope => scope === 'global' ? '/search' : `/${scope}/search`;
export function params(filters) {
  const result = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === '' || value == null) continue;
    if (Array.isArray(value)) value.forEach(item => result.append(key, item));
    else result.set(key, String(value));
  }
  return result;
}
const statuses = ['draft', 'submitted', 'under_evaluation', 'additional_information_required', 'manual_review', 'approved', 'rejected', 'closed'];
const warrantyStatuses = ['active', 'near_expiry', 'expired', 'extended_warranty', 'not_started', 'no_warranty'];
const categories = ['Electronics', 'Mobile Devices', 'Home Appliances', 'Computers', 'Audio Equipment', 'Other'];
const base = {q: '', sort: 'newest', page: 1, page_size: 25};

export function StatusSelector({title, name, options, value = [], change}) {
  return <fieldset className="sx-multiselect"><legend>{title}</legend>{options.map(option => <label key={option}>
    <input type="checkbox" name={name} value={option} checked={value.includes(option.toLowerCase())}
      onChange={e => change(e.target.checked ? [...value, option.toLowerCase()] : value.filter(item => item !== option.toLowerCase()))}/>{label(option)}</label>)}</fieldset>;
}

export function DatePicker({filters, set}) {
  return <fieldset className="sx-date"><legend>Date range</legend><label>Date field<select value={filters.date_field || 'submission_date'} onChange={e => set('date_field', e.target.value)}>
    {['submission_date', 'review_date', 'warranty_expiry_date'].map(value => <option key={value} value={value}>{label(value)}</option>)}</select></label>
    <label>Period<select value={filters.date_preset || ''} onChange={e => set({date_preset: e.target.value, start_date: '', end_date: ''})}>
      <option value="">Any time</option>{[['today', 'Today'], ['7d', 'Last 7 days'], ['30d', 'Last 30 days'], ['90d', 'Last 90 days'], ['custom', 'Custom range']]
        .map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
    {filters.date_preset === 'custom' && <><label>From<input type="date" name="start_date" value={filters.start_date || ''} onChange={e => set('start_date', e.target.value)}/></label>
      <label>Through<input type="date" name="end_date" min={filters.start_date || undefined} value={filters.end_date || ''} onChange={e => set('end_date', e.target.value)}/></label></>}
  </fieldset>;
}

export function ConfidenceSlider({filters, set}) {
  return <fieldset><legend>Latest Python confidence</legend><label className="sx-inline"><input type="checkbox" checked={filters.min_confidence != null && filters.min_confidence !== ''}
    onChange={e => set({min_confidence: e.target.checked ? 0 : '', max_confidence: e.target.checked ? 1 : ''})}/>Filter confidence</label>
    {filters.min_confidence != null && filters.min_confidence !== '' && <>
      <label>Minimum · {Math.round(filters.min_confidence * 100)}%<input aria-label="Minimum confidence" type="range" min="0" max="1" step="0.01" value={filters.min_confidence}
        onChange={e => set({min_confidence: Number(e.target.value), max_confidence: Math.max(Number(e.target.value), Number(filters.max_confidence ?? 1))})}/></label>
      <label>Maximum · {Math.round((filters.max_confidence ?? 1) * 100)}%<input aria-label="Maximum confidence" type="range" min={filters.min_confidence} max="1" step="0.01" value={filters.max_confidence ?? 1}
        onChange={e => set('max_confidence', Number(e.target.value))}/></label></>}
  </fieldset>;
}

export function AdvancedFilterPanel({filters, set}) {
  return <div className="sx-filter-grid">
    <fieldset><legend>Record details</legend>{[['claim_id', 'Claim ID'], ['product_id', 'Product ID'], ['serial_number', 'Serial number'],
      ['product_name', 'Product name'], ['brand', 'Brand'], ['model', 'Model']].map(([name, title]) => <label key={name}>{title}
        <input name={name} maxLength={name.endsWith('_id') ? 32 : 100} value={filters[name] || ''} onChange={e => set(name, e.target.value)}/></label>)}
      <label>Serial match<select value={filters.serial_match || 'partial'} onChange={e => set('serial_match', e.target.value)}><option value="partial">Contains</option><option value="exact">Exact, ignoring case</option></select></label>
    </fieldset>
    <div><StatusSelector title="Claim status" name="claim_status" options={statuses} value={filters.claim_status} change={value => set('claim_status', value)}/>
      <StatusSelector title="Category" name="category" options={categories} value={filters.category} change={value => set('category', value)}/></div>
    <div><StatusSelector title="Warranty status" name="warranty_status" options={warrantyStatuses} value={filters.warranty_status} change={value => set('warranty_status', value)}/>
      <label>Warranty provider<input name="provider" value={filters.provider || ''} onChange={e => set('provider', e.target.value)}/></label>
      <label>Extended coverage<select value={filters.extended_warranty ?? ''} onChange={e => set('extended_warranty', e.target.value)}>
        <option value="">Either</option><option value="true">Extended</option><option value="false">Standard</option></select></label>
      <ConfidenceSlider filters={filters} set={set}/></div>
    <div><DatePicker filters={filters} set={set}/><fieldset><legend>People & assignment</legend>
      <label>Assignment<select value={filters.assignment || ''} onChange={e => set({assignment: e.target.value, reviewer_id: '', reviewer_name: ''})}>
        <option value="">Any</option><option value="assigned">Assigned</option><option value="unassigned">Unassigned</option></select></label>
      {['reviewer_id', 'reviewer_name', 'customer_id', 'customer'].map(name => <label key={name}>{label(name)}<input name={name} value={filters[name] || ''}
        disabled={filters.assignment === 'unassigned' && name.startsWith('reviewer')} onChange={e => set(name, e.target.value)}/></label>)}
    </fieldset></div>
  </div>;
}

export function FilterDrawer({filters, set, open, close}) {
  return open ? <section className="sx-drawer" id="advanced-search-filters" aria-label="Advanced search filters"><div className="sx-heading"><h3>Advanced filters</h3>
    <button type="button" onClick={close}>Close filters</button></div><AdvancedFilterPanel filters={filters} set={set}/></section> : null;
}

export function SortControls({filters, set, scope}) {
  return <div className="sx-sort"><label>Sort by<select aria-label="Sort by" value={filters.sort || 'newest'} onChange={e => set('sort', e.target.value)}>
    {['newest', 'oldest', 'recently_updated', ...(['claims', 'review', 'global'].includes(scope) ? ['highest_confidence', 'lowest_confidence'] : []), 'warranty_expiry_date']
      .map(value => <option key={value} value={value}>{label(value)}</option>)}</select></label>
    <label>Direction<select value={filters.order || ''} onChange={e => set('order', e.target.value)}><option value="">Default</option><option value="asc">Ascending</option><option value="desc">Descending</option></select></label></div>;
}

export function PaginationControls({data, filters, page}) {
  return <div className="sx-pagination"><button type="button" disabled={filters.page <= 1} onClick={() => page(filters.page - 1)}>Previous</button>
    <span>Page {filters.page} of {Math.max(1, data.total_pages || 0)} · {data.total.toLocaleString()} records</span>
    <button type="button" disabled={filters.page >= data.total_pages} onClick={() => page(filters.page + 1)}>Next</button></div>;
}

export function SearchBar({value, change, suggestions, pick}) {
  const [focused, setFocused] = useState(false);
  return <div className="sx-searchbar" onBlur={event => {if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false);}}>
    <label htmlFor="assurex-search">Search records</label><input id="assurex-search" type="search" autoComplete="off" maxLength={120} placeholder="Claim, product, serial number or person"
      value={value} onFocus={() => setFocused(true)} onChange={e => change(e.target.value)}/>
    {focused && value.trim().length >= 2 && suggestions && <div className="sx-suggestions" aria-label="Search suggestions">
      {Object.entries(suggestions).filter(([, items]) => items.length).map(([kind, items]) => <section key={kind}><strong>{label(kind)}</strong>{items.map(item => {
        const text = kind === 'claims' ? item.claim_id : kind === 'products' ? item.product_id : kind === 'warranties' ? item.warranty_id : item.full_name;
        return <button type="button" key={item.id} onClick={() => {pick(text);setFocused(false);}}>{text}<small>{item.product_name || item.name || item.provider || item.role}</small></button>;
      })}</section>)}
    </div>}
  </div>;
}

function Results({name, data, user, onOpen}) {
  if (!data.items.length) return <p className="sx-empty">No matching {name}. Try fewer filters or a different search.</p>;
  return <div className="sx-results">{data.items.map(item => {
    let href;
    if (name === 'claims' && user.role === 'customer') href = item.status === 'draft' ? `/claims/draft/${item.id}` : `/claims/${item.claim_id}`;
    else if (name === 'claims' && ['reviewer', 'admin'].includes(user.role) && ['manual_review', 'additional_information_required'].includes(item.status)) href = `/dashboard/reviewer?claim_id=${item.id}`;
    else if (name === 'products' && ['customer', 'admin'].includes(user.role)) href = `/products/${item.id}`;
    const heading = item.claim_id || item.name || item.warranty_id || item.full_name;
    return <article key={item.id} className="sx-result"><div><strong>{heading}</strong><p>{item.product_name || item.product_id || label(item.role)}</p>
      <small>{[item.serial_number, item.customer, item.reviewer ? `Reviewer: ${item.reviewer}` : '', item.expiry_date ? `Expires ${item.expiry_date}` : ''].filter(Boolean).join(' · ')}</small></div>
      <div className="sx-result-end">{item.status && <span className="sx-badge">{label(item.status)}</span>}{item.warranty_status && <span>{label(item.warranty_status)}</span>}
        {item.confidence_score != null && <span>Confidence {(item.confidence_score * 100).toFixed(1)}%</span>}
        {href && <a href={href} onClick={event => {if (onOpen && !event.ctrlKey && !event.metaKey) {event.preventDefault();onOpen(href);}}}>Open {name === 'claims' ? 'claim' : 'product'}</a>}</div>
    </article>;
  })}</div>;
}

export default function SearchWorkspace({user, initialScope = 'global', initialFilters = {}, onOpen, embedded = false}) {
  const [scope, setScope] = useState(initialScope), [filters, setFilters] = useState(() => ({...base, ...initialFilters})), [applied, setApplied] = useState(() => ({...base, ...initialFilters}));
  const [open, setOpen] = useState(false), [saveName, setSaveName] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const client = useQueryClient();
  const validDates = filters.date_preset !== 'custom' || (filters.start_date && filters.end_date);
  useEffect(() => {if (!validDates) return;const timer = setTimeout(() => setApplied(filters), 300);return () => clearTimeout(timer);}, [filters, validDates]);
  const result = useQuery({queryKey: ['search', user.id, scope, applied], enabled: !!validDates,
    queryFn: async ({signal}) => (await api.get(endpoint(scope), {params: params(applied), signal})).data,
    retry: false, refetchInterval: false, staleTime: 0});
  const suggestion = useQuery({queryKey: ['search-suggestions', user.id, applied.q], enabled: (applied.q || '').length >= 2,
    queryFn: async ({signal}) => (await api.get('/search/suggestions', {params: {q: applied.q}, signal})).data,
    refetchInterval: false, staleTime: 0});
  const saved = useQuery({queryKey: ['saved-searches', user.id], queryFn: async () => (await api.get('/search/saved')).data, refetchInterval: false});
  const recent = useQuery({queryKey: ['recent-searches', user.id, result.dataUpdatedAt], queryFn: async () => (await api.get('/search/recent')).data, refetchInterval: false});
  function set(key, value) {setFilters(old => ({...old, ...(typeof key === 'object' ? key : {[key]: value}), page: 1}));setError('');setNotice('');}
  function reuse(item) {setScope(item.scope);setFilters({...base, ...item.filters, page: 1});setError('');setNotice('');}
  const persist = useMutation({mutationFn: async () => {
    const clean = Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== '' && value != null));
    return api.post('/search/saved', {name: saveName, scope, filters: clean});
  }, onSuccess: () => {setSaveName('');setNotice({text: 'Search saved.'});client.invalidateQueries({queryKey: ['saved-searches']});}, onError: e => setError(errorMessage(e))});
  const remove = useMutation({mutationFn: id => api.delete(`/search/saved/${id}`), onSuccess: () => client.invalidateQueries({queryKey: ['saved-searches']}), onError: e => setError(errorMessage(e))});
  const exportReport = useMutation({mutationFn: format => api.post(`/reports/export/${format}`, {advanced: {...Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== '')),
    ...(scope === 'review' ? {claim_status: ['manual_review']} : {})}}), onSuccess: () => setNotice({text: 'Export requested. Download it from Reports when it is ready.', report: true}), onError: e => setError(errorMessage(e))});
  const groups = result.data?.groups || (result.data ? {[scope === 'review' ? 'claims' : scope]: result.data} : {});
  const total = Object.values(groups).reduce((sum, group) => sum + group.total, 0);
  return <section className={`search-workspace ${embedded ? 'sx-embedded' : ''}`} aria-label="Search workspace">
    {!embedded && <div className="sx-heading"><div><p className="sx-eyebrow">FIND THE RIGHT RECORD</p><h1>Search your workspace</h1><p>Combine filters across claims, products and warranties.</p></div></div>}
    <div className="sx-toolbar"><SearchBar value={filters.q || ''} change={value => set('q', value)} suggestions={suggestion.data?.groups} pick={value => set('q', value)}/>
      <label>Search in<select aria-label="Search in" value={scope} onChange={e => {setScope(e.target.value);setFilters(old => ({...old, page: 1, sort: 'newest'}));}}>
        {['global', 'claims', 'products', 'warranties', ...(['admin', 'reviewer'].includes(user.role) ? ['review'] : [])]
          .map(value => <option key={value} value={value}>{value === 'global' ? 'All records' : value === 'review' ? 'Reviewer queue' : label(value)}</option>)}</select></label>
      <button type="button" aria-expanded={open} aria-controls="advanced-search-filters" onClick={() => setOpen(!open)}>Advanced filters</button>
      <button type="button" onClick={() => {setFilters(base);setError('');setNotice('');}}>Clear all</button></div>
    <FilterDrawer filters={filters} set={set} open={open} close={() => setOpen(false)}/>
    <div className="sx-controls"><SortControls filters={filters} set={set} scope={scope}/><label>Page size<select aria-label="Page size" value={filters.page_size} onChange={e => set('page_size', Number(e.target.value))}>
      {[10,25,50,100].map(n => <option key={n}>{n}</option>)}</select></label></div>
    {!validDates && <p role="status">Choose both custom dates to update the results.</p>}
    {(error || result.error) && <p className="sx-error" role="alert">{error || errorMessage(result.error)}</p>}
    {notice && <p role="status" className="sx-notice">{notice.text} {notice.report && <a href="/reports">Open Reports</a>}</p>}
    <div aria-live="polite" className="sx-result-summary">{result.isFetching ? 'Searching…' : result.data ? `${total.toLocaleString()} matching records` : ''}</div>
    {Object.entries(groups).map(([name, data]) => <section className="sx-group" key={name}><h2>{label(name)} <small>{data.total.toLocaleString()}</small></h2><Results name={name} data={data} user={user} onOpen={onOpen}/>
      {scope !== 'global' && <PaginationControls data={data} filters={applied} page={page => setFilters(old => ({...old, page}))}/>}</section>)}
    {scope === 'global' && result.data && <PaginationControls data={{total, total_pages: Math.max(0, ...Object.values(groups).map(group => group.total_pages))}} filters={applied} page={page => setFilters(old => ({...old, page}))}/>}
    <div className="sx-library"><section><h3>Saved searches</h3><form onSubmit={event => {event.preventDefault();setError('');persist.mutate();}}>
      <label>Name this search<input name="saved_search_name" maxLength={80} required value={saveName} onChange={e => setSaveName(e.target.value)}/></label>
      <button disabled={persist.isPending || !validDates}>Save search</button></form>
      {saved.error && <p role="alert">{errorMessage(saved.error)}</p>}{!saved.data?.items.length && <p>No saved searches yet.</p>}
      {saved.data?.items.map(item => <div className="sx-saved" key={item.id}><button type="button" onClick={() => reuse(item)}>{item.name}</button><button type="button" disabled={remove.isPending}
        aria-label={`Delete ${item.name}`} onClick={() => remove.mutate(item.id)}>Delete</button></div>)}</section>
      <section><h3>Recent searches</h3>{recent.data?.items.slice(0, 5).map((item, i) => <button className="sx-recent" type="button" key={i} onClick={() => reuse(item)}>{item.query || label(item.scope)}<small>{Object.keys(item.filters).filter(key => !['q','page','page_size','sort','serial_match','date_field'].includes(key)).map(label).join(' · ') || 'All authorized records'}</small></button>)}</section>
    </div>
    {['claims', 'review'].includes(scope) && <section className="sx-export"><h3>Export these filters</h3><p>Exports include all matching claims you are authorized to report on. Unassigned review cases require assignment before export.</p>
      {['csv', 'excel', 'pdf'].map(format => <button type="button" key={format} disabled={exportReport.isPending || !validDates} onClick={() => exportReport.mutate(format)}>Export {format.toUpperCase()}</button>)}</section>}
  </section>;
}
