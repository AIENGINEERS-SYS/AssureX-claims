import React from 'react';

export const faultTypes = ['Mechanical Failure', 'Electrical Failure', 'Software Issue', 'Manufacturing Defect',
  'Accidental Damage', 'Water Damage', 'Overheating', 'Other'];
export const damageCategories = ['Minor', 'Moderate', 'Severe', 'Total Loss'];
export const documentTypes = ['receipt', 'product_image', 'serial_number_image', 'damage_evidence',
  'warranty_card', 'diagnostic_report', 'repair_report'];
export const requiredDocuments = documentTypes.slice(0, 4);
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

export function DocumentsStep({documents, upload, remove, progress, busy}) {
  const total = documents.reduce((sum, doc) => sum + doc.file_size, 0);
  return <section><h2>Add supporting documents</h2><p>JPG, JPEG, PNG or PDF. Up to 10 MB per file and 50 MB per claim.</p>
    <p className="hint">{(total / 1024 / 1024).toFixed(1)} MB of 50 MB used</p>
    <div className="document-grid">{documentTypes.map(type => <div className="document-slot" key={type}>
      <label htmlFor={`upload-${type}`}>{label(type)} <span className="hint">({requiredDocuments.includes(type) ? 'required' : 'optional'})</span></label>
      <input id={`upload-${type}`} type="file" accept=".jpg,.jpeg,.png,.pdf" disabled={busy}
        onChange={event => {const file = event.target.files[0]; if (file) upload(type, file); event.target.value = '';}}/>
      {progress?.type === type && <div role="status"><progress max="100" value={progress.percent} aria-label={`${label(type)} upload progress`}/><span>{progress.percent}% uploaded</span></div>}
      {documents.filter(doc => doc.document_type === type).map(doc => <div key={doc.id} className="file-row"><span>{doc.file_name}<small>{doc.file_type} · {(doc.file_size / 1024).toFixed(1)} KB</small></span>
        <button type="button" className="text-button" disabled={busy} onClick={() => remove(doc.id)} aria-label={`Remove ${doc.file_name}`}>Remove</button></div>)}
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
      <strong>{label(doc.document_type)}</strong><span>{doc.file_name} · {doc.file_type}</span></li>)}</ul>
    <p className="hint">Your submission date and unique Claim ID will be generated when you submit.</p>
  </section>;
}
