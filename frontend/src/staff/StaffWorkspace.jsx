import React, {useState} from 'react';
import {useQuery, useQueryClient} from '@tanstack/react-query';
import {api, errorMessage} from '../claims/api';

export default function StaffWorkspace({admin = false}) {
  const client = useQueryClient(), [selected, setSelected] = useState(null), [page, setPage] = useState(1);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [employee, setEmployee] = useState(''), [reviewer, setReviewer] = useState(''), [status, setStatus] = useState('manual_review');
  const [reason, setReason] = useState(''), [requestedType, setRequestedType] = useState('diagnostic_report');
  const [account, setAccount] = useState({full_name: '', email: '', password: '', role: 'employee'});
  const claims = useQuery({queryKey: ['staff-claims', admin, page], queryFn: async () => (await api.get(admin ? '/admin/claims' : '/claims/assigned', {params: {page, per_page: 25}})).data});
  const users = useQuery({queryKey: ['staff-users'], enabled: admin, queryFn: async () => (await api.get('/admin/users', {params: {per_page: 100}})).data});
  const failures = useQuery({queryKey: ['assessment-failures'], enabled: admin, queryFn: async () => (await api.get('/admin/assessments')).data});
  const details = useQuery({queryKey: ['employee-claim', selected], enabled: selected !== null,
    queryFn: async () => (await api.get(`/employee/claims/${selected}`)).data});
  async function act(action) {
    setBusy(true); setError('');
    try {await action(); await Promise.all(['staff-claims','employee-claim','staff-users','assessment-failures','admin'].map(key => client.invalidateQueries({queryKey: [key]})));}
    catch (err) {setError(errorMessage(err));} finally {setBusy(false);}
  }
  const claim = details.data?.claim;
  return <><div className="page-head"><div><h1>{admin ? 'Claim and account administration' : 'Assigned claims'}</h1>
    <p>{admin ? 'Manage assignments, employee accounts and failed assessment jobs.' : 'Inspect evidence, request information and run permitted assessments.'}</p></div></div>
    {error && <p className="error-box" role="alert">{error}</p>}
    {(claims.error || users.error || failures.error) && <p role="alert">{errorMessage(claims.error || users.error || failures.error)}</p>}
    <section className="panel"><h2>{admin ? 'Claims' : 'Your assigned work'}</h2>
      {claims.isPending ? <p role="status">Loading claims...</p> : <><ul>{claims.data?.items.map(item => <li key={item.id}>
        <button onClick={() => setSelected(item.id)}>{item.claim_id}</button> · {item.status.replaceAll('_',' ')}</li>)}</ul>
        {!claims.data?.items.length && <p>No claims are assigned.</p>}
        <button disabled={page === 1} onClick={() => setPage(value => value - 1)}>Previous</button>
        <span> Page {page} </span><button disabled={!claims.data || page * 25 >= claims.data.total} onClick={() => setPage(value => value + 1)}>Next</button></>}
    </section>
    {selected && <section className="panel"><h2>Claim workspace</h2>{details.error && <p role="alert">{errorMessage(details.error)}</p>}
      {claim ? <><h3>{claim.claim_id}</h3><p>{claim.fault_description}</p><p>Status: {claim.status.replaceAll('_',' ')}</p>
        <p>Assessment: {details.data.assessment.assessment_status} · Report: {details.data.assessment.report_status}</p>
        {details.data.evaluation && <p>Automated recommendation: {details.data.evaluation.recommendation.replaceAll('_',' ')}. An authorized reviewer records the final decision.</p>}
        <h3>Evidence</h3><ul>{details.data.documents.map(item => <li key={item.id}>{item.file_name} · {item.ocr_status} · {item.review_status}
          <button disabled={busy} onClick={() => act(async () => {const response = await api.get(`/documents/${item.id}/content`, {responseType: 'blob'});
            const url = URL.createObjectURL(response.data), link = document.createElement('a'); link.href = url; link.download = item.file_name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
          })}>Download evidence</button></li>)}</ul>
        {!['approved','rejected','closed','draft'].includes(claim.status) && <><button disabled={busy} onClick={() => act(() => api.post(`/claims/${claim.id}/assessment/retry`, {}))}>Queue assessment or retry report</button>
          <label>Processing status<select value={status} onChange={event => setStatus(event.target.value)}>
            <option value="manual_review">Manual review</option><option value="under_evaluation">Under evaluation</option><option value="additional_information_required">Additional information required</option></select></label>
          {status === 'additional_information_required' && <><label>Customer-visible request<input value={reason} onChange={event => setReason(event.target.value)} maxLength={2000}/></label>
            <label>Requested evidence<select value={requestedType} onChange={event => setRequestedType(event.target.value)}>
              {['receipt','invoice','product_image','damage_evidence','serial_number_image','diagnostic_report','repair_report','warranty_card'].map(kind => <option key={kind} value={kind}>{kind.replaceAll('_',' ')}</option>)}</select></label></>}
          <button disabled={busy || (status === 'additional_information_required' && !reason.trim())} onClick={() => act(() => api.patch(`/claims/${claim.id}/status`, {status,
            ...(status === 'additional_information_required' ? {reason, document_types: [requestedType]} : {})}))}>Update processing status</button></>}
        {admin && <><h3>Assignments</h3><label>Employee<select value={employee} onChange={event => setEmployee(event.target.value)}><option value="">Select employee</option>
          {users.data?.items.filter(user => user.role === 'employee' && user.is_active).map(user => <option value={user.id} key={user.id}>{user.full_name}</option>)}</select></label>
          <button disabled={busy || !employee} onClick={() => act(() => api.patch(`/claims/${claim.id}/assignment`, {employee_id: Number(employee)}))}>Assign employee</button>
          <label>Reviewer<select value={reviewer} onChange={event => setReviewer(event.target.value)}><option value="">Select reviewer</option>
          {users.data?.items.filter(user => user.role === 'reviewer' && user.is_active).map(user => <option value={user.id} key={user.id}>{user.full_name}</option>)}</select></label>
          <button disabled={busy || !reviewer || claim.status !== 'manual_review'} onClick={() => act(() => api.patch(`/dashboard/reviewer/claims/${claim.id}/assignment`, {reviewer_id: Number(reviewer)}))}>Assign reviewer</button></>}
      </> : details.isPending && <p role="status">Loading claim...</p>}</section>}
    {admin && <><section className="panel"><h2>Assessment jobs needing attention</h2><ul>{failures.data?.items.map(job => <li key={job.id}>Claim #{job.claim_id} · {job.message || job.status}
      <button disabled={busy} onClick={() => act(() => api.post(`/claims/${job.claim_id}/assessment/retry`, {}))}>Retry eligible job</button></li>)}</ul>
      {failures.data?.items.length === 0 && <p>No failed jobs.</p>}</section>
      <section className="panel"><h2>Create staff account</h2><form onSubmit={event => {event.preventDefault(); act(async () => {
        await api.post('/admin/users', account); setAccount({full_name:'', email:'', password:'', role:'employee'});
      });}}>{[['full_name','Full name','text'],['email','Email','email'],['password','Temporary password','password']].map(([key, text, type]) => <label key={key}>{text}
        <input type={type} autoComplete={type === 'password' ? 'new-password' : undefined} minLength={key === 'password' ? 12 : 1} required value={account[key]} onChange={event => setAccount(value => ({...value, [key]: event.target.value}))}/></label>)}
        <label>Role<select value={account.role} onChange={event => setAccount(value => ({...value, role: event.target.value}))}><option value="employee">Employee</option><option value="reviewer">Reviewer</option></select></label>
        <button disabled={busy}>Create account</button></form></section></>}
  </>;
}
