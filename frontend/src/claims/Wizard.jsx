import React, {useEffect, useRef, useState} from 'react';
import {useForm, useWatch} from 'react-hook-form';
import {Link, useNavigate, useParams} from 'react-router-dom';
import {api, allPages, errorMessage} from './api';
import {DetailsStep, DocumentsStep, ProductStep, Progress, requiredDocuments, ReviewStep} from './components';

const defaults = {product_id: '', fault_date: '', fault_type: '', description: '', damage_category: '', repair_history: '', previous_replacement: false};
function payload(values, step) {
  return {...values, product_id: Number(values.product_id) || null, fault_date: values.fault_date || null,
    fault_type: values.fault_type || null, damage_category: values.damage_category || null, current_step: step};
}

export default function Wizard() {
  const {id} = useParams(), navigate = useNavigate();
  const form = useForm({defaultValues: defaults, mode: 'onTouched'}), values = useWatch({control: form.control});
  const [claim, setClaim] = useState(null), [products, setProducts] = useState([]), [step, setStep] = useState(1);
  const [error, setError] = useState(''), [status, setStatus] = useState('Loading…'), [busy, setBusy] = useState(false), [progress, setProgress] = useState(null);
  const current = useRef(null), chain = useRef(Promise.resolve()), saved = useRef(''), latest = useRef({values, step});
  const blocked = useRef(false), alive = useRef(true);
  latest.current = {values, step};
  const selected = products.find(p => p.id === Number(values.product_id));

  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    Promise.all([allPages('/products/my'), api.get(`/claims/${id}`)]).then(([items, {data}]) => {
      if (cancelled) return;
      if (data.claim.status !== 'DRAFT') {navigate(`/${data.claim.claim_id}`, {replace: true}); return;}
      const draft = data.claim, initial = Object.fromEntries(Object.keys(defaults).map(key => [key, draft[key] ?? defaults[key]]));
      initial.product_id = draft.product_id ? String(draft.product_id) : '';
      current.current = draft; setClaim(draft); setProducts(items); setStep(draft.current_step); form.reset(initial);
      saved.current = JSON.stringify(payload(initial, draft.current_step)); setStatus('All changes saved');
    }).catch(err => {if (!cancelled) {setError(errorMessage(err)); setStatus('Unable to load draft');}});
    return () => {cancelled = true; alive.current = false;};
  }, [id]);

  // All mutations share a queue and always use the latest server version.
  function enqueue(action) {
    const next = chain.current.catch(() => {}).then(action);
    chain.current = next;
    return next;
  }
  function accept(draft) {current.current = draft; if (alive.current) setClaim(draft);}
  async function persist(input, atStep) {
    if (blocked.current) throw new Error('Draft must be reloaded.');
    const data = payload(input, atStep), signature = JSON.stringify(data);
    if (saved.current === signature) return current.current;
    setStatus('Saving…');
    const response = await api.put(`/claims/draft/${current.current.id}`, {...data, version: current.current.version});
    accept(response.data.claim); saved.current = signature;
    setStatus(JSON.stringify(payload(latest.current.values, latest.current.step)) === signature ? 'All changes saved' : 'Unsaved changes');
    return response.data.claim;
  }
  function failed(err) {
    if (!alive.current) return;
    if (err.response?.status === 409) blocked.current = true;
    setError(errorMessage(err)); setStatus('Changes not saved');
    for (const [name, messages] of Object.entries(err.response?.data?.error?.details || {})) {
      if (name in defaults) form.setError(name, {type: 'server', message: Array.isArray(messages) ? messages.join(' ') : String(messages)});
    }
  }
  const snapshot = JSON.stringify(payload(values, step));
  useEffect(() => {
    if (!claim || busy || blocked.current || snapshot === saved.current) return;
    setStatus('Unsaved changes');
    const timer = setTimeout(() => {enqueue(() => persist(values, step)).catch(failed);}, 900);
    return () => clearTimeout(timer);
  }, [snapshot, Boolean(claim), busy]);
  useEffect(() => {
    const warn = event => {if (current.current && JSON.stringify(payload(latest.current.values, latest.current.step)) !== saved.current) {event.preventDefault(); event.returnValue = '';}};
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, []);

  useEffect(() => {
    const leave = event => {
      const anchor = event.target.closest('a');
      if (!anchor || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || anchor.hash) return;
      if (!current.current || JSON.stringify(payload(latest.current.values, latest.current.step)) === saved.current) return;
      event.preventDefault(); event.stopPropagation();
      if (busy) return;
      run(async () => {
        await persist(form.getValues(), latest.current.step);
        const destination = new URL(anchor.href);
        if (destination.origin === location.origin && destination.pathname.startsWith('/claims')) navigate(destination.pathname.slice(7) || '/');
        else location.assign(destination.href);
      });
    };
    document.addEventListener('click', leave, true);
    return () => document.removeEventListener('click', leave, true);
  }, [busy]);

  useEffect(() => {
    const saveBeforeSignOut = event => {
      if (!current.current) return;
      setBusy(true);
      event.detail.waitFor.push(enqueue(() => persist(form.getValues(), latest.current.step))
        .catch(err => {failed(err); throw err;}).finally(() => {if (alive.current) setBusy(false);}));
    };
    window.addEventListener('assurex:before-signout', saveBeforeSignOut);
    return () => window.removeEventListener('assurex:before-signout', saveBeforeSignOut);
  }, []);

  async function run(action) {
    setBusy(true); setError('');
    try {await enqueue(action);} catch (err) {failed(err);} finally {if (alive.current) setBusy(false);}
  }
  async function go(next) {
    if (next > step) {
      const fields = step === 1 ? ['product_id'] : step === 2 ? ['fault_date', 'fault_type', 'description', 'damage_category', 'repair_history'] : [];
      if (!await form.trigger(fields)) return;
      if (step === 3 && requiredDocuments.some(type => !current.current.documents.some(d => d.document_type === type))) {
        setError('Upload the receipt, product image, serial number image and damage evidence before continuing.'); return;
      }
      if (step === 3 && current.current.documents.some(document => document.review_status === 'pending')) {
        setError('Review and confirm the extracted information before continuing.'); return;
      }
    }
    await run(async () => {
      await persist(form.getValues(), next);
      if (next === 4) {
        const {data} = await api.get(`/claims/${current.current.id}`);
        if (Object.keys(data.validation_errors).length) {
          setError(Object.entries(data.validation_errors).map(([key, messages]) => `${key}: ${messages.join(' ')}`).join('\n'));
        }
      }
      setStep(next); setStatus('All changes saved'); window.scrollTo({top: 0});
    });
  }
  async function upload(type, file) {
    const policy = current.current.document_policy || {max_document_size_mb: 10, max_claim_upload_size_mb: 50, max_documents_per_claim: 20};
    if (!/\.(jpe?g|png|pdf)$/i.test(file.name) || !['image/jpeg', 'image/png', 'application/pdf'].includes(file.type)) {setError('Choose a JPG, JPEG, PNG or PDF file.'); return;}
    if (file.size > policy.max_document_size_mb * 1024 * 1024) {setError(`Each file must be at most ${policy.max_document_size_mb} MB.`); return;}
    if (current.current.documents.length >= policy.max_documents_per_claim) {setError(`A claim can contain at most ${policy.max_documents_per_claim} documents.`); return;}
    if (current.current.documents.reduce((sum, doc) => sum + doc.file_size, file.size) > policy.max_claim_upload_size_mb * 1024 * 1024) {setError(`Total uploads cannot exceed ${policy.max_claim_upload_size_mb} MB.`); return;}
    await run(async () => {
      await persist(form.getValues(), step);
      const data = new FormData();
      data.append('version', current.current.version);
      data.append('document_type', type); data.append('file', file); setProgress({type, percent: 0});
      try {
        const response = await api.post(`/claims/${current.current.id}/documents`, data, {onUploadProgress: event => setProgress({type, percent: Math.round(event.loaded / (event.total || file.size) * 100)})});
        accept(response.data.claim); setStatus('All changes saved');
      } finally {setProgress(null);}
    });
  }
  function remove(documentId) {
    run(async () => {
      const response = await api.delete(`/documents/${documentId}`, {data: {version: current.current.version}});
      accept(response.data.claim);
    });
  }
  function review(documentId, corrections) {
    run(async () => {
      const clean = Object.fromEntries(Object.entries(corrections).map(([key, value]) => [key, value === '' ? null : value]));
      if (clean.warranty_duration !== null) clean.warranty_duration = Number(clean.warranty_duration);
      const response = await api.patch(`/documents/${documentId}/ocr-review`, {...clean, confirm: true, version: current.current.version});
      accept(response.data.claim); setStatus('Extracted information confirmed');
    });
  }
  function retry(documentId) {
    run(async () => {
      const response = await api.post(`/documents/${documentId}/ocr/retry`, {version: current.current.version});
      accept(response.data.claim); setStatus('Document analysis updated');
    });
  }
  function submit() {
    run(async () => {
      await persist(form.getValues(), step);
      const {data} = await api.post('/claims/submit', {draft_id: current.current.id, version: current.current.version});
      saved.current = JSON.stringify(payload(form.getValues(), step));
      navigate(`/${data.claim.claim_id}`, {replace: true});
    });
  }
  if (!claim) return <div className="panel"><p role="status">{status}</p>{error && <p role="alert">{error}</p>}<Link to="/">Back to claims</Link></div>;
  return <><div className="title-row"><div><p className="eyebrow">YOUR CLAIM, STEP BY STEP</p><h1>Submit a claim</h1></div><span className="save-state" role="status">{status}</span></div>
    <Progress step={step}/><div className="panel">
      {error && <div className="error" role="alert">{error}{blocked.current && <p><button type="button" onClick={() => window.location.reload()}>Reload saved draft</button></p>}</div>}
      <form onSubmit={event => event.preventDefault()}><fieldset disabled={busy || blocked.current}>
        {step === 1 && <ProductStep products={products} register={form.register} errors={form.formState.errors} selected={selected}/>}
        {step === 2 && <DetailsStep register={form.register} errors={form.formState.errors} today={claim.server_date} description={values.description}/>}
        {step === 3 && <DocumentsStep documents={claim.documents} upload={upload} remove={remove} review={review} retry={retry}
          progress={progress} busy={busy} policy={claim.document_policy}/>}
        {step === 4 && <ReviewStep claim={claim} product={selected} values={values}/>}
      </fieldset><div className="actions"><button type="button" disabled={busy} onClick={() => run(async () => {await persist(form.getValues(), step); navigate('/');})}>Save and exit</button>
        <div>{step > 1 && <button type="button" disabled={busy || blocked.current} onClick={() => go(step - 1)}>Previous</button>}
          {step < 4 ? <button type="button" className="primary" disabled={busy || blocked.current} onClick={() => go(step + 1)}>Next</button> :
            <button type="button" className="primary" disabled={busy || blocked.current} onClick={submit}>{busy ? 'Submitting…' : 'Submit claim'}</button>}</div></div></form>
    </div><p className="footnote">Your progress is saved automatically. You can return to this draft from My claims.</p></>;
}
