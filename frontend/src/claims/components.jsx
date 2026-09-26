import React, {useEffect, useState} from 'react';
import {api, errorMessage} from './api';

export const faultTypes = ['Mechanical Failure', 'Electrical Failure', 'Software Issue', 'Manufacturing Defect',
  'Accidental Damage', 'Water Damage', 'Overheating', 'Other'];
export const damageCategories = ['Minor', 'Moderate', 'Severe', 'Total Loss'];
export const documentTypes = ['receipt', 'invoice', 'product_image', 'serial_number_image', 'damage_evidence',
  'warranty_card', 'diagnostic_report', 'repair_report', 'other'];
export const requiredDocuments = ['receipt', 'product_image', 'serial_number_image', 'damage_evidence'];
export const label = value => value.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());

export function Progress({step}) {
  return <ol className="steps" aria-label="Claim progress">{['Product', 'Details', 'Documents', 'Review'].map((name, i) =>
    <li key={name} aria-current={step === i + 1 ? 'step' : undefined} className={step >= i + 1 ? 'reached' : ''}>
      <span>{i + 1}</span>{name}</li>)}</ol>;
}

export function ProductInfo({product}) {
  if (!product) return null;
  return <dl className="facts">{[['Product', product.name], ['Category', product.category], ['Brand', product.brand],
    ['Model number', product.model_number], ['Serial number', product.serial_number], ['Purchase date', product.purchase_date],
    ['Warranty status', product.warranty_status]].map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>;
}

export function ProductStep({products, register, errors, selected}) {
  return <section><h2>Select your product</h2><p>Choose the registered product you need help with.</p>
    {!products.length && <p>No registered products yet. <a href="/products/new">Register a product</a> to begin.</p>}
    <label htmlFor="product_id">Registered product</label>
    <select id="product_id" {...register('product_id', {required: 'Select a product.',
      validate: value => products.some(p => p.id === Number(value) && p.is_active) || 'Select an active product.'})}>
      <option value="">Choose a product</option>{products.map(p => <option key={p.id} value={p.id} disabled={!p.is_active}>
        {p.name} · {p.serial_number}{!p.is_active ? ' (inactive)' : ''}</option>)}
    </select><FieldError name="product_id" errors={errors}/><ProductInfo product={selected}/>
  </section>;
}

export function FieldError({name, errors}) {
  return errors[name] ? <p className="field-error" role="alert" id={`${name}-error`}>{errors[name].message}</p> : null;
}

export function DetailsStep({register, errors, today, description}) {
  return <section><h2>Tell us what happened</h2><p>A clear description helps our team assess your claim.</p>
    <div className="form-grid"><div><label htmlFor="fault_date">Fault date</label>
      <input id="fault_date" type="date" max={today} {...register('fault_date', {required: 'Enter the fault date.',
        validate: value => value <= today || 'Fault date cannot be in the future.'})}/><FieldError name="fault_date" errors={errors}/></div>
      <div><label htmlFor="fault_type">Fault type</label><select id="fault_type" {...register('fault_type', {required: 'Select a fault type.'})}>
        <option value="">Choose a fault type</option>{faultTypes.map(value => <option key={value}>{value}</option>)}</select><FieldError name="fault_type" errors={errors}/></div>
      <div><label htmlFor="damage_category">Damage category</label><select id="damage_category" {...register('damage_category', {required: 'Select a damage category.'})}>
        <option value="">Choose a category</option>{damageCategories.map(value => <option key={value}>{value}</option>)}</select><FieldError name="damage_category" errors={errors}/></div>
      <div><label>Submission date</label><p className="system-value">Set automatically when you submit</p></div></div>
    <label htmlFor="description">Description</label><textarea id="description" rows="5" maxLength={2000}
      {...register('description', {required: 'Describe the problem.', validate: value => value.trim().length >= 20 || 'Use at least 20 characters.',
        maxLength: {value: 2000, message: 'Use at most 2000 characters.'}})}/>
    <div className="hint">{description?.length || 0}/2000 characters · minimum 20</div><FieldError name="description" errors={errors}/>
    <label htmlFor="repair_history">Repair history <span className="hint">(optional)</span></label><textarea id="repair_history" rows="3" maxLength={1000}
      {...register('repair_history', {maxLength: {value: 1000, message: 'Use at most 1000 characters.'}})}/><FieldError name="repair_history" errors={errors}/>
    <label className="checkbox"><input type="checkbox" {...register('previous_replacement')}/>This product has previously been replaced</label>
  </section>;
}

const ocrFields = ['purchase_date', 'invoice_number', 'product_name', 'model_number', 'serial_number',
  'retailer', 'purchase_price', 'warranty_duration'];

function detectedValue(document, field) {
  const reviewed = document.verified_data?.[field]?.confirmed_value;
  if (reviewed !== undefined && reviewed !== null) return reviewed;
  return document.extracted_data?.[field]?.value ?? '';
}

function DocumentPreview({document}) {
  const [url, setUrl] = useState(''), [error, setError] = useState('');
  useEffect(() => {
    if (!document.file_type.startsWith('image/')) return;
    let active = true, objectUrl = '';
    api.get(`/documents/${document.id}/content`, {responseType: 'blob'}).then(({data}) => {
      objectUrl = URL.createObjectURL(data); if (active) setUrl(objectUrl);
    }).catch(err => {if (active) setError(errorMessage(err));});
    return () => {active = false; if (objectUrl) URL.revokeObjectURL(objectUrl);};
  }, [document.id]);
  async function download() {
    setError('');
    try {
      const {data} = await api.get(`/documents/${document.id}/content`, {responseType: 'blob'});
      const objectUrl = URL.createObjectURL(data), anchor = window.document.createElement('a');
      anchor.href = objectUrl; anchor.download = document.file_name; anchor.click();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (err) {setError(errorMessage(err));}
  }
  return <div className="document-preview">{url ? <img src={url} alt={`Preview of ${document.file_name}`}/> :
    <button type="button" className="text-button" onClick={download}>Download secure preview</button>}
    {error && <p className="field-error" role="alert">{error}</p>}</div>;
}

function OCRReview({document, review, retry, busy}) {
  const initial = Object.fromEntries(ocrFields.map(field => [field, detectedValue(document, field)]));
  initial.warranty_duration_unit = document.verified_data?.warranty_duration?.unit ||
    document.extracted_data?.warranty_duration?.unit || 'months';
  const [values, setValues] = useState(initial);
  const [expanded, setExpanded] = useState(false), [raw, setRaw] = useState(document.ocr_raw_text || '');
  const set = (field, value) => setValues(current => ({...current, [field]: value}));
  const status = document.ocr_status.replaceAll('_', ' ');
  async function toggle(event) {
    const open = event.currentTarget.open; setExpanded(open);
    if (open && !raw) {
      try {const {data} = await api.get(`/documents/${document.id}/ocr`); setRaw(data.document.ocr_raw_text || '');}
      catch { /* The main API error area handles mutation failures; preview remains optional. */ }
    }
  }
  return <details className="ocr-panel" onToggle={toggle}><summary>Document analysis · {status}</summary>
    {expanded && <DocumentPreview document={document}/>}
    {document.ocr_status === 'failed' ? <div className="ocr-failure"><p>{document.ocr_error || 'Analysis failed.'}</p>
      <button type="button" disabled={busy} onClick={() => retry(document.id)}>Retry analysis</button></div> : <>
      <p className="hint">Extracted automatically. Check every detected value before confirming.</p>
      <div className="ocr-grid">{ocrFields.map(field => {
        const extraction = document.extracted_data?.[field] || {};
        const inputType = field === 'purchase_date' ? 'date' : field === 'purchase_price' || field === 'warranty_duration' ? 'number' : 'text';
        return <div key={field}><label htmlFor={`ocr-${document.id}-${field}`}>{label(field)}</label>
          <input id={`ocr-${document.id}-${field}`} type={inputType} min={inputType === 'number' ? '0' : undefined}
            step={field === 'purchase_price' ? '0.01' : undefined} value={values[field] ?? ''}
            placeholder={extraction.state === 'ambiguous' ? 'Ambiguous — enter the correct value' : 'Not detected'}
            onChange={event => set(field, event.target.value)}/>
          <small>{extraction.value == null ? (extraction.state === 'ambiguous' ? 'Review required' : 'Not detected') :
            `${Math.round((extraction.confidence || 0) * 100)}% · ${extraction.confidence_label || 'Needs review'}`}</small>
          {field === 'warranty_duration' && <select aria-label="Warranty duration unit" value={values.warranty_duration_unit}
            onChange={event => set('warranty_duration_unit', event.target.value)}><option value="months">Months</option><option value="years">Years</option></select>}
        </div>})}</div>
      <button type="button" className="primary" disabled={busy}
        onClick={() => review(document.id, values)}>{document.review_status === 'confirmed' ? 'Update confirmed information' : 'Save corrections and confirm'}</button>
      {raw && <details className="raw-ocr"><summary>View raw OCR text</summary><pre>{raw}</pre></details>}
    </>}
  </details>;
}

export function DocumentsStep({documents, upload, remove, review, retry, progress, busy, policy}) {
  const total = documents.reduce((sum, doc) => sum + doc.file_size, 0);
  const perFile = policy?.max_document_size_mb || 10, perClaim = policy?.max_claim_upload_size_mb || 50;
  return <section><h2>Add supporting documents</h2><p>Drag a file into its category or use the picker, then review any extracted values. PDF, JPG, JPEG and PNG only.</p>
    <p className="hint">Up to {perFile} MB per file · {documents.length}/{policy?.max_documents_per_claim || 20} documents · {(total / 1024 / 1024).toFixed(1)} MB of {perClaim} MB used</p>
    <div className="document-grid">{documentTypes.map(type => <div className="document-slot" key={type}>
      <label htmlFor={`upload-${type}`}>{label(type)} <span className="hint">({requiredDocuments.includes(type) ? 'required' : 'optional'})</span></label>
      <div className="drop-zone" onDragOver={event => event.preventDefault()} onDrop={event => {
        event.preventDefault(); const file = event.dataTransfer.files[0]; if (file) upload(type, file);
      }}><span>Drop file here or choose</span><input id={`upload-${type}`} type="file" accept=".jpg,.jpeg,.png,.pdf" disabled={busy}
        onChange={event => {const file = event.target.files[0]; if (file) upload(type, file); event.target.value = '';}}/>
      </div>
      {progress?.type === type && <div role="status"><progress max="100" value={progress.percent} aria-label={`${label(type)} upload progress`}/><span>{progress.percent}% uploaded</span></div>}
      {documents.filter(doc => doc.document_type === type).map(doc => <div key={doc.id} className="document-result"><div className="file-row"><span>{doc.file_name}<small>{doc.file_type} · {(doc.file_size / 1024).toFixed(1)} KB</small></span>
        <span className={`status-chip ${doc.ocr_status}`}>{doc.ocr_status.replaceAll('_', ' ')}</span>
        <button type="button" className="text-button" disabled={busy} onClick={() => remove(doc.id)} aria-label={`Remove ${doc.file_name}`}>Remove</button></div>
        <OCRReview key={`${doc.id}-${doc.ocr_processed_at}-${doc.reviewed_at}`} document={doc} review={review} retry={retry} busy={busy}/></div>)}
    </div>)}</div>
  </section>;
}

export function ReviewStep({claim, product, values}) {
  return <section><h2>Review your claim</h2><p>Check your details and documents before submitting.</p>
    <h3>Product information</h3><ProductInfo product={product}/>
    <h3>Claim information</h3><dl className="facts">{['fault_date', 'fault_type', 'damage_category', 'description', 'repair_history'].map(key =>
      <div key={key}><dt>{label(key)}</dt><dd className="preserve">{values[key] || 'Not provided'}</dd></div>)}
      <div><dt>Previous replacement</dt><dd>{values.previous_replacement ? 'Yes' : 'No'}</dd></div></dl>
    <h3>Supporting documents</h3><ul className="review-documents">{claim.documents.map(doc => <li key={doc.id}>
      <strong>{label(doc.document_type)}</strong><span>{doc.file_name} · {doc.file_type} · OCR {doc.ocr_status.replaceAll('_', ' ')} · {doc.review_status.replaceAll('_', ' ')}</span></li>)}</ul>
    {claim.document_completeness && <p className="hint">Document completeness: {Math.round(claim.document_completeness.completeness_score * 100)}%</p>}
    <p className="hint">Your submission date and unique Claim ID will be generated when you submit.</p>
  </section>;
}
