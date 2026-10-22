import React, {useEffect, useState} from 'react';
import {api, errorMessage} from './api';

const label = value => String(value || 'Pending').replaceAll('_', ' ');

function Finding({value}) {
  if (value == null || value === '') return <span>Not available</span>;
  if (Array.isArray(value)) return value.length ? <ul>{value.map((item, index) => <li key={index}><Finding value={item}/></li>)}</ul> : <span>None recorded</span>;
  if (typeof value === 'object') return <dl>{Object.entries(value).map(([key, item]) => <React.Fragment key={key}>
    <dt>{label(key)}</dt><dd><Finding value={item}/></dd></React.Fragment>)}</dl>;
  return <span>{String(value)}</span>;
}

export default function AssessmentPanel({claim, onRefresh}) {
  const [progress, setProgress] = useState(null), [report, setReport] = useState(null), [history, setHistory] = useState([]);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true, timer;
    async function load() {
      try {
        const {data} = await api.get(`/claims/${claim.id}/assessment`);
        if (!active) return;
        setProgress(data); setError('');
        if (data.claim_status.toUpperCase() !== claim.status) onRefresh?.().catch(() => {});
        if (!['approved','rejected','closed'].includes(data.claim_status)) timer = setTimeout(load,
          ['queued', 'running', 'retry_wait'].includes(data.assessment.status) ? 4000 : 20000);
      } catch (err) {if (active) {setError(errorMessage(err)); timer = setTimeout(load, 15000);}}
    }
    load();
    return () => {active = false; clearTimeout(timer);};
  }, [claim.id, claim.status, refresh]);
  useEffect(() => {
    let active = true;
    api.get(`/claims/${claim.id}/assessment/reports`).then(({data}) => {if (active) setHistory(data.items);}).catch(() => {});
    return () => {active = false;};
  }, [claim.id, progress?.assessment.report?.id]);
  async function perform(action) {
    setBusy(true); setError('');
    try {await action();} catch (err) {setError(errorMessage(err));} finally {setBusy(false);}
  }
  function openReport(id) {return perform(async () => {
    const {data} = await api.get(`/claims/${claim.id}/assessment/reports/${id}`); setReport(data);
  });}
  function retry() {return perform(async () => {
    await api.post(`/claims/${claim.id}/assessment/retry`, {}); setRefresh(value => value + 1); await onRefresh?.();
  });}
  function download(format) {return perform(async () => {
    const response = await api.get(`/claims/${claim.id}/assessment/reports/${report.report.id}/download/${format}`, {responseType: 'blob'});
    const url = URL.createObjectURL(response.data), anchor = document.createElement('a');
    anchor.href = url; anchor.download = `${claim.claim_id}-assessment-v${report.report.version}.${format === 'excel' ? 'xlsx' : format}`;
    anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });}
  const assessment = progress?.assessment || claim.assessment;
  const state = progress?.claim_status?.toUpperCase() || claim.status;
  const availableReport = assessment?.report || history[0];
  return <section className="panel assessment-panel" aria-label="Claim assessment">
    <h2>Assessment and report</h2>
    <p role="status">Claim: <strong>{label(state)}</strong> · Assessment: <strong>{label(assessment?.assessment_status)}</strong> · Report: <strong>{label(assessment?.report_status)}</strong></p>
    {state === 'MANUAL_REVIEW' && <p>Your claim is awaiting an authorized reviewer. The automated recommendation is not a final decision.</p>}
    {state === 'ADDITIONAL_INFORMATION_REQUIRED' && <p>Your reviewer needs more evidence. Complete the requested uploads and confirm the extracted information, then send the evidence for reassessment.</p>}
    {assessment?.message && <p>{assessment.message}</p>}
    {state === 'ADDITIONAL_INFORMATION_REQUIRED' && claim.information_request?.message && <p>{claim.information_request.message}</p>}
    {['queued', 'running', 'retry_wait'].includes(assessment?.status) && <p>Your claim is saved. Processing continues in the background; this page checks progress automatically.</p>}
    {assessment?.status === 'not_scheduled' && <p>No automatic assessment has been scheduled for this earlier claim. Please contact your service center.</p>}
    <div className="actions"><button type="button" onClick={() => perform(async () => {setRefresh(value => value + 1); await onRefresh?.();})} disabled={busy}>Refresh assessment</button>
      {(assessment?.retryable || state === 'ADDITIONAL_INFORMATION_REQUIRED') && !['APPROVED','REJECTED','CLOSED'].includes(state) && <button type="button" disabled={busy} onClick={retry}>
        {state === 'ADDITIONAL_INFORMATION_REQUIRED' ? 'Send evidence for reassessment' : 'Retry assessment or report'}</button>}
      {availableReport && <button type="button" className="primary" disabled={busy} onClick={() => openReport(availableReport.id)}>
        {assessment?.report ? 'View Assessment Report' : 'View Assessment Report (previous version)'}</button>}</div>
    {history.length > 0 && <details><summary>Report history</summary><ul>{history.map(item => <li key={item.id}>
      <button type="button" disabled={busy} onClick={() => openReport(item.id)}>Assessment version {item.version}</button> · {new Date(item.created_at).toLocaleString()}</li>)}</ul></details>}
    {report && <article aria-label="Assessment report"><h3>Assessment report · version {report.report.version}</h3>
      <p>{report.report.payload.notice}</p><p>Current authorized decision: <strong>{label(report.authorized_decision?.decision || 'Awaiting decision')}</strong></p>
      <div className="actions">{['pdf','csv','excel'].map(format => <button type="button" key={format} disabled={busy} onClick={() => download(format)}>Download {format.toUpperCase()}</button>)}</div>
      {Object.entries(report.report.payload).filter(([key]) => key !== 'notice').map(([key, value]) => <details key={key} open={['claim','automated_recommendation','follow_up'].includes(key)}>
        <summary>{label(key)}</summary><Finding value={value}/></details>)}</article>}
    {error && <p className="error" role="alert">{error}</p>}
  </section>;
}
