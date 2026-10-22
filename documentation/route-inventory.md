# Registered routes and frontend communication

Generated from Flask's URL map and handler authorization metadata. Object scope is enforced by the shared claim scope, document scope and reporting services. Employee and reviewer access requires assignment; administrator roles are explicitly listed.

| Method | Path | Blueprint | Authentication | Roles | Purpose / handler |
| --- | --- | --- | --- | --- | --- |
| GET | `/` | application | Public | public | frontend root |
| GET | `/api/admin/analytics` | admin | JWT | admin | admin.analytics |
| GET | `/api/admin/assessments` | admin | JWT | admin | admin.assessment failures |
| GET | `/api/admin/claims` | admin | JWT | admin | admin.claims |
| POST | `/api/admin/users` | admin | JWT (fresh) | admin | admin.create user |
| GET | `/api/admin/users` | admin | JWT | admin | admin.users |
| DELETE | `/api/admin/users/<int:user_id>` | admin | JWT (fresh) | admin | admin.delete user |
| PATCH | `/api/admin/users/<int:user_id>` | admin | JWT (fresh) | admin | admin.update user |
| POST | `/api/auth/login` | auth | Public | public | auth.login |
| POST | `/api/auth/logout` | auth | JWT | all authenticated accounts | auth.logout |
| GET | `/api/auth/me` | auth | JWT | all authenticated accounts | auth.me |
| PATCH | `/api/auth/me` | auth | JWT | all authenticated accounts | auth.update profile |
| POST | `/api/auth/password` | auth | JWT (fresh) | all authenticated accounts | auth.password |
| POST | `/api/auth/refresh` | auth | JWT (fresh) (refresh) | all authenticated accounts | auth.refresh |
| POST | `/api/auth/register` | auth | Public | public | auth.register |
| POST | `/api/claims` | claim_workflow | JWT | customer | claim workflow.submit |
| GET | `/api/claims/<identifier>` | claim_workflow | JWT | customer | claim workflow.details |
| GET | `/api/claims/<identifier>/documents` | documents | JWT | customer, employee, reviewer, admin | documents.list documents |
| GET | `/api/claims/<int:claim_id>/assessment` | assessments | JWT | customer, employee, reviewer, admin | assessments.progress |
| GET | `/api/claims/<int:claim_id>/assessment/reports` | assessments | JWT | customer, employee, reviewer, admin | assessments.history |
| GET | `/api/claims/<int:claim_id>/assessment/reports/<report_id>` | assessments | JWT | customer, employee, reviewer, admin | assessments.view |
| GET | `/api/claims/<int:claim_id>/assessment/reports/<report_id>/download/<format>` | assessments | JWT | customer, employee, reviewer, admin | assessments.download |
| POST | `/api/claims/<int:claim_id>/assessment/retry` | assessments | JWT | customer, employee, reviewer, admin | assessments.retry |
| PATCH | `/api/claims/<int:claim_id>/assignment` | claims | JWT | admin | claims.assign |
| GET | `/api/claims/<int:claim_id>/decision` | predictions | JWT | customer, employee, reviewer, admin | predictions.decision |
| POST | `/api/claims/<int:claim_id>/documents` | claims | JWT | customer, employee, admin | claims.upload |
| GET | `/api/claims/<int:claim_id>/documents/<int:document_id>` | claims | JWT | customer, employee, reviewer, admin | claims.download |
| POST | `/api/claims/<int:claim_id>/evaluate` | predictions | JWT | employee, reviewer, admin | predictions.evaluate |
| PATCH | `/api/claims/<int:claim_id>/status` | claims | JWT | employee, admin | claims.status |
| GET | `/api/claims/assigned` | claims | JWT | employee, admin | claims.assigned |
| POST | `/api/claims/draft` | claim_workflow | JWT | customer | claim workflow.create |
| PUT | `/api/claims/draft/<int:draft_id>` | claim_workflow | JWT | customer | claim workflow.update |
| DELETE | `/api/claims/draft/<int:draft_id>/documents/<int:document_id>` | claim_workflow | JWT | customer | claim workflow.remove document |
| GET | `/api/claims/my` | claims | JWT | customer, admin | claims.mine |
| GET | `/api/claims/search` | search | JWT | customer, employee, reviewer, admin | search.claims |
| POST | `/api/claims/submit` | claim_workflow | JWT | customer | claim workflow.submit |
| POST | `/api/claims/upload` | claim_workflow | JWT | customer | claim workflow.upload |
| GET | `/api/dashboard/admin` | dashboard | JWT | admin | dashboard.admin |
| GET | `/api/dashboard/analytics` | dashboard | JWT | admin | dashboard.analytics |
| GET | `/api/dashboard/analytics/models` | dashboard | JWT | admin | dashboard.models |
| GET | `/api/dashboard/customer` | dashboard | JWT | customer, admin | dashboard.customer |
| GET | `/api/dashboard/notifications` | dashboard | JWT | customer, employee, reviewer, admin | dashboard.notifications |
| PATCH | `/api/dashboard/notifications/<int:notification_id>/read` | dashboard | JWT | customer, employee, reviewer, admin | dashboard.mark read |
| GET | `/api/dashboard/reviewer` | dashboard | JWT | reviewer, admin | dashboard.reviewer |
| GET | `/api/dashboard/reviewer/claims/<int:claim_id>` | dashboard | JWT | reviewer, admin | dashboard.review details |
| PATCH | `/api/dashboard/reviewer/claims/<int:claim_id>/assignment` | dashboard | JWT | admin | dashboard.assign |
| POST | `/api/dashboard/reviewer/claims/<int:claim_id>/remind` | dashboard | JWT | reviewer, admin | dashboard.remind |
| POST | `/api/dashboard/reviewer/claims/<int:claim_id>/request-documents` | dashboard | JWT | reviewer, admin | dashboard.request documents |
| POST | `/api/dashboard/reviewer/claims/<int:claim_id>/resume` | dashboard | JWT | reviewer, admin | dashboard.resume |
| POST | `/api/dashboard/reviewer/duplicates/decision` | dashboard | JWT | reviewer, admin | dashboard.decide duplicate |
| GET | `/api/dashboard/trends` | dashboard | JWT | customer, reviewer, admin | dashboard.trends endpoint |
| DELETE | `/api/documents/<int:document_id>` | documents | JWT | customer | documents.delete document |
| GET | `/api/documents/<int:document_id>` | documents | JWT | customer, employee, reviewer, admin | documents.document details |
| GET | `/api/documents/<int:document_id>/content` | documents | JWT | customer, employee, reviewer, admin | documents.document content |
| GET | `/api/documents/<int:document_id>/ocr` | documents | JWT | customer, employee, reviewer, admin | documents.document ocr |
| PATCH | `/api/documents/<int:document_id>/ocr-review` | documents | JWT | customer | documents.review ocr |
| POST | `/api/documents/<int:document_id>/ocr/retry` | documents | JWT | customer | documents.retry ocr |
| GET | `/api/employee/claims/<int:claim_id>` | employee | JWT | employee, admin | employee.details |
| GET | `/api/employee/claims/<int:claim_id>/audit-history` | employee | JWT | employee, admin | employee.history |
| GET | `/api/health` | application | Public | public | health |
| GET | `/api/notifications` | notifications | JWT | customer, employee, reviewer, admin | notifications.list notifications |
| DELETE | `/api/notifications/<int:notification_id>` | notifications | JWT | customer, employee, reviewer, admin | notifications.delete notification |
| GET | `/api/notifications/<int:notification_id>` | notifications | JWT | customer, employee, reviewer, admin | notifications.get notification |
| PATCH | `/api/notifications/<int:notification_id>/read` | notifications | JWT | customer, employee, reviewer, admin | notifications.mark read |
| GET | `/api/notifications/analytics` | notifications | JWT | admin | notifications.analytics |
| GET | `/api/notifications/preferences` | notifications | JWT | customer, employee, reviewer, admin | notifications.get preferences |
| PATCH | `/api/notifications/preferences` | notifications | JWT | customer, employee, reviewer, admin | notifications.update preferences |
| PATCH | `/api/notifications/read-all` | notifications | JWT | customer, employee, reviewer, admin | notifications.mark all read |
| GET | `/api/notifications/unread` | notifications | JWT | customer, employee, reviewer, admin | notifications.unread |
| POST | `/api/predict/gtm` | predictions | JWT | employee, reviewer, admin | predictions.gtm prediction |
| POST | `/api/predict/python` | predictions | JWT | employee, reviewer, admin | predictions.python prediction |
| GET | `/api/products` | products | JWT | customer, admin | products.index |
| POST | `/api/products` | products | JWT | customer, admin | products.register |
| DELETE | `/api/products/<int:product_id>` | products | JWT | customer, admin | products.delete |
| GET | `/api/products/<int:product_id>` | products | JWT | customer, admin | products.details |
| PUT | `/api/products/<int:product_id>` | products | JWT | customer, admin | products.update |
| POST | `/api/products/<int:product_id>/warranties` | products | JWT | customer, admin | products.add warranty |
| GET | `/api/products/<int:product_id>/warranties` | products | JWT | customer, admin | products.warranty history |
| GET | `/api/products/my` | claim_workflow | JWT | customer | claim workflow.products |
| GET | `/api/products/search` | search | JWT | customer, employee, reviewer, admin | search.products |
| GET | `/api/reports` | reports | JWT | customer, employee, reviewer, admin | reports.index |
| GET | `/api/reports/administrative` | reports | JWT | admin | reports.administrative |
| GET | `/api/reports/claim/<int:entity_id>` | reports | JWT | customer, employee, reviewer, admin | reports.claim |
| GET | `/api/reports/customer/<int:entity_id>` | reports | JWT | customer, employee, reviewer, admin | reports.customer |
| POST | `/api/reports/export/<format>` | reports | JWT | customer, employee, reviewer, admin | reports.export |
| GET | `/api/reports/history` | reports | JWT | customer, employee, reviewer, admin | reports.history |
| GET | `/api/reports/jobs/<job_id>` | reports | JWT | customer, employee, reviewer, admin | reports.status |
| GET | `/api/reports/jobs/<job_id>/download` | reports | JWT | customer, employee, reviewer, admin | reports.download |
| GET | `/api/reports/product/<int:entity_id>` | reports | JWT | customer, employee, reviewer, admin | reports.product |
| GET | `/api/reports/reviewer/<int:entity_id>` | reports | JWT | customer, employee, reviewer, admin | reports.reviewer |
| POST | `/api/review/<int:claim_id>/approve` | review | JWT | reviewer, admin | review.approve |
| GET | `/api/review/<int:claim_id>/audit-history` | review | JWT | reviewer, admin | review.history |
| POST | `/api/review/<int:claim_id>/notes` | review | JWT | reviewer, admin | review.notes |
| POST | `/api/review/<int:claim_id>/override` | review | JWT | reviewer, admin | review.override |
| POST | `/api/review/<int:claim_id>/reject` | review | JWT | reviewer, admin | review.reject |
| GET | `/api/review/<int:claim_id>/risk` | review | JWT | reviewer, admin | review.risk |
| GET | `/api/review/manual` | review | JWT | reviewer, admin | review.manual |
| GET | `/api/review/search` | search | JWT | reviewer, admin | search.review |
| GET | `/api/search` | search | JWT | customer, employee, reviewer, admin | search.global search |
| GET | `/api/search/analytics` | search | JWT | admin | search.analytics |
| GET | `/api/search/recent` | search | JWT | customer, employee, reviewer, admin | search.recent |
| POST | `/api/search/saved` | search | JWT | customer, employee, reviewer, admin | search.save |
| GET | `/api/search/saved` | search | JWT | customer, employee, reviewer, admin | search.saved list |
| DELETE | `/api/search/saved/<int:saved_id>` | search | JWT | customer, employee, reviewer, admin | search.remove |
| GET | `/api/search/suggestions` | search | JWT | customer, employee, reviewer, admin | search.suggestions |
| DELETE | `/api/warranties/<int:warranty_id>` | products | JWT | customer, admin | products.delete warranty |
| PUT | `/api/warranties/<int:warranty_id>` | products | JWT | customer, admin | products.update warranty |
| POST | `/api/warranties/preview` | products | JWT | customer, admin | products.preview |
| GET | `/api/warranties/search` | search | JWT | customer, employee, reviewer, admin | search.warranties |
| GET | `/claims` | application | Public | public | frontend section |
| GET | `/claims/<path:path>` | application | Public | public | frontend deep link |
| GET | `/dashboard` | application | Public | public | frontend section |
| GET | `/dashboard/<path:path>` | application | Public | public | frontend deep link |
| GET | `/docs` | application | Public | public | swagger ui |
| GET | `/docs/` | application | Public | public | swagger ui |
| GET | `/openapi.json` | application | Public | public | openapi document |
| GET | `/products` | application | Public | public | frontend section |
| GET | `/products/<path:path>` | application | Public | public | frontend deep link |
| GET | `/reports` | application | Public | public | frontend section |
| GET | `/reports/<path:path>` | application | Public | public | frontend deep link |
| GET | `/search` | application | Public | public | frontend section |
| GET | `/static/<path:filename>` | application | Public | public | static |

Frontend navigation and primary backend contracts:

| Frontend route group | Allowed roles | Backend APIs |
| --- | --- | --- |
| `/claims`, `/claims/draft/:id`, `/claims/:claimId` | customer | `/api/claims/my`, drafts/uploads/submission, own claim details and assessment reports |
| `/products/*` | customer, admin | `/api/products/*`, warranty and product document APIs |
| `/dashboard/customer` | customer, admin (explicit customer ID) | `/api/dashboard/customer`, trends and notifications |
| `/dashboard/employee` | employee, admin | `/api/claims/assigned`, `/api/employee/claims/:id`, processing status and assessment retry |
| `/dashboard/reviewer` | reviewer, admin | `/api/dashboard/reviewer/*`, `/api/review/*`, permitted evaluation and evidence APIs |
| `/dashboard/admin`, `/dashboard/operations` | admin | `/api/dashboard/admin`, `/api/admin/*`, assignment and assessment retry |
| `/dashboard/notifications` | authenticated | `/api/notifications/*` |
| `/reports/*` | authenticated, with per-report service authorization | `/api/reports/*` |
| `/search/*` | authenticated, with scoped results | `/api/search`, claim/product/warranty/review searches |

Discovered frontend API call sites (dynamic path expressions are retained):

| Source | API path / expression |
| --- | --- |
| `frontend/src/claims/api.js` | `api.get(path, {params: {page, per_page: 100}})` |
| `frontend/src/claims/AssessmentPanel.jsx` | `api.get('/claims/${claim.id}/assessment/reports/${id}')` |
| `frontend/src/claims/AssessmentPanel.jsx` | `api.get('/claims/${claim.id}/assessment/reports/${report.report.id}/download/${format}', {responseType: 'blob'})` |
| `frontend/src/claims/AssessmentPanel.jsx` | `api.get('/claims/${claim.id}/assessment/reports').then(({data}) => {if (active) setHistory(data.items)` |
| `frontend/src/claims/AssessmentPanel.jsx` | `api.get('/claims/${claim.id}/assessment')` |
| `frontend/src/claims/AssessmentPanel.jsx` | `api.post('/claims/${claim.id}/assessment/retry', {})` |
| `frontend/src/claims/components.jsx` | `api.get('/documents/${document.id}/content', {responseType: 'blob'})` |
| `frontend/src/claims/components.jsx` | `api.get('/documents/${document.id}/content', {responseType: 'blob'}).then(({data}) => {` |
| `frontend/src/claims/components.jsx` | `api.get('/documents/${document.id}/ocr')` |
| `frontend/src/claims/main.jsx` | `api.get('/claims/${current.current.id}')` |
| `frontend/src/claims/main.jsx` | `api.get('/claims/${encodeURIComponent(claimId)}').then(({data}) => {` |
| `frontend/src/claims/main.jsx` | `api.patch('/documents/${id}/ocr-review', {...Object.fromEntries(Object.entries(corrections).map(([key,value]) =>` |
| `frontend/src/claims/main.jsx` | `api.post('/auth/login', values)` |
| `frontend/src/claims/main.jsx` | `api.post('/auth/logout')` |
| `frontend/src/claims/main.jsx` | `api.post('/claims/draft', {})` |
| `frontend/src/claims/main.jsx` | `api.post('/claims/${latest.id}/documents', form, {onUploadProgress: event => {` |
| `frontend/src/claims/main.jsx` | `api.post('/documents/${id}/ocr/retry', {version: latest.version}))` |
| `frontend/src/claims/Wizard.jsx` | `api.delete('/documents/${documentId}', {data: {version: current.current.version}})` |
| `frontend/src/claims/Wizard.jsx` | `api.get('/claims/${current.current.id}')` |
| `frontend/src/claims/Wizard.jsx` | `api.get('/claims/${id}')` |
| `frontend/src/claims/Wizard.jsx` | `api.get('/claims/${id}')]).then(([items, {data}]) => {` |
| `frontend/src/claims/Wizard.jsx` | `api.patch('/documents/${documentId}/ocr-review', {...clean, confirm: true, version: current.current.version})` |
| `frontend/src/claims/Wizard.jsx` | `api.post('/claims/submit', {draft_id: current.current.id, version: current.current.version})` |
| `frontend/src/claims/Wizard.jsx` | `api.post('/claims/${current.current.id}/documents', data, {onUploadProgress: event => setProgress({type, percent: Math.round(event.loaded / (event.total // file.size) * 100)})})` |
| `frontend/src/claims/Wizard.jsx` | `api.post('/documents/${documentId}/ocr/retry', {version: current.current.version})` |
| `frontend/src/claims/Wizard.jsx` | `api.put('/claims/draft/${current.current.id}', {...data, version: current.current.version})` |
| `frontend/src/dashboard/Dashboard.jsx` | `api.delete('/notifications/${id}'), onSuccess: refresh})` |
| `frontend/src/dashboard/Dashboard.jsx` | `api.get('/notifications/unread', {params: {limit: 5}})).data,` |
| `frontend/src/dashboard/Dashboard.jsx` | `api.get('/documents/${doc.id}/content', {responseType: 'blob'})` |
| `frontend/src/dashboard/Dashboard.jsx` | `api.get(path, {params})).data,` |
| `frontend/src/dashboard/Dashboard.jsx` | `api.patch('/notifications/preferences', values),` |
| `frontend/src/dashboard/Dashboard.jsx` | `api.patch('/notifications/read-all'), onSuccess: () => {` |
| `frontend/src/dashboard/Dashboard.jsx` | `api.patch('/notifications/${id}/read'), onSuccess: () => {` |
| `frontend/src/dashboard/Dashboard.jsx` | `api.patch('/notifications/${id}/read'), onSuccess: refresh})` |
| `frontend/src/dashboard/Dashboard.jsx` | `api.post('/auth/login', {email, password})` |
| `frontend/src/dashboard/Dashboard.jsx` | `api.post('/auth/logout')` |
| `frontend/src/dashboard/Dashboard.jsx` | `query('admin', '/dashboard/admin'), [window, setWindow] = useState('30d'), [interval, setInterval] = useState('day')` |
| `frontend/src/dashboard/Dashboard.jsx` | `query('customer', '/dashboard/customer', {page, per_page: 8, user_id: userId})` |
| `frontend/src/dashboard/Dashboard.jsx` | `query('model-history', '/dashboard/analytics/models', {page, per_page: 8})` |
| `frontend/src/dashboard/Dashboard.jsx` | `query('notification-analytics', '/notifications/analytics', {days: 30})` |
| `frontend/src/dashboard/Dashboard.jsx` | `query('notification-preferences', '/notifications/preferences')` |
| `frontend/src/dashboard/Dashboard.jsx` | `query('notifications', '/notifications', params)` |
| `frontend/src/dashboard/Dashboard.jsx` | `query('review-detail', '/dashboard/reviewer/claims/${item.id}')` |
| `frontend/src/dashboard/Dashboard.jsx` | `query('reviewer', '/dashboard/reviewer', {page, per_page: 10, search: term})` |
| `frontend/src/dashboard/Dashboard.jsx` | `query('trends', '/dashboard/trends', {window, interval}), [customerId, setCustomerId] = useState(''), navigate = useNavigate()` |
| `frontend/src/products/ProductsApp.jsx` | `api.delete('/products/${product.id}')` |
| `frontend/src/products/ProductsApp.jsx` | `api.delete('/warranties/${item.id}')` |
| `frontend/src/products/ProductsApp.jsx` | `api.get('/products', {params, signal: controller.signal})` |
| `frontend/src/products/ProductsApp.jsx` | `api.get('/products/${productId}', {signal: controller.signal})` |
| `frontend/src/products/ProductsApp.jsx` | `api.get('/products/${productId}', {signal: controller.signal}).then(({data}) => setValues(current => ({...current, ...Object.fromEntries(productFields.map(key => [key, data.product[key] ??` |
| `frontend/src/products/ProductsApp.jsx` | `api.post('/auth/login', {email: values.email, password: values.password})` |
| `frontend/src/products/ProductsApp.jsx` | `api.post('/auth/logout')` |
| `frontend/src/products/ProductsApp.jsx` | `api.post('/auth/register', values)` |
| `frontend/src/products/ProductsApp.jsx` | `api.post('/warranties/preview', {start_date, duration, duration_unit: values.warranty_duration_unit}, {signal: controller.signal})` |
| `frontend/src/products/ProductsApp.jsx` | `api.post('/warranties/preview', {start_date: values.start_date, duration: Number(values.duration), duration_unit: values.duration_unit}, {signal: controller.signal})` |
| `frontend/src/products/ProductsApp.jsx` | `api.put('/products/${productId}', payload) : await api.post('/products', payload)` |
| `frontend/src/products/ProductsApp.jsx` | `api.put('/warranties/${warranty.id}', payload) : await api.post('/products/${product.id}/warranties', payload)` |
| `frontend/src/reports/ReportsApp.jsx` | `api.get('/reports', {params: {...filters, page, per_page: 25}})).data,` |
| `frontend/src/reports/ReportsApp.jsx` | `api.get('/reports/history', {params: {page: historyPage, per_page: 10}})).data,` |
| `frontend/src/reports/ReportsApp.jsx` | `api.get('/reports/jobs/${job.id}/download', {responseType: 'blob', timeout: 0,` |
| `frontend/src/reports/ReportsApp.jsx` | `api.post('/auth/login', values)` |
| `frontend/src/reports/ReportsApp.jsx` | `api.post('/auth/logout')` |
| `frontend/src/reports/ReportsApp.jsx` | `api.post('/reports/export/${format}', payload(form))).data,` |
| `frontend/src/search/SearchApp.jsx` | `api.get('/search/analytics')).data, refetchInterval: false})` |
| `frontend/src/search/SearchApp.jsx` | `api.post('/auth/login', {email: form.get('email'), password: form.get('password')})` |
| `frontend/src/search/SearchApp.jsx` | `api.post('/auth/logout')` |
| `frontend/src/search/SearchWorkspace.jsx` | `api.delete('/search/saved/${id}'), onSuccess: () => client.invalidateQueries({queryKey: ['saved-searches']}), onError: e => setError(errorMessage(e))})` |
| `frontend/src/search/SearchWorkspace.jsx` | `api.get('/search/recent')).data, refetchInterval: false})` |
| `frontend/src/search/SearchWorkspace.jsx` | `api.get('/search/saved')).data, refetchInterval: false})` |
| `frontend/src/search/SearchWorkspace.jsx` | `api.get('/search/suggestions', {params: {q: applied.q}, signal})).data,` |
| `frontend/src/search/SearchWorkspace.jsx` | `api.get(endpoint(scope), {params: params(applied), signal})).data,` |
| `frontend/src/search/SearchWorkspace.jsx` | `api.post('/search/saved', {name: saveName, scope, filters: clean})` |
| `frontend/src/search/SearchWorkspace.jsx` | `api.post('/reports/export/${format}', {advanced: {...Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== '')),` |
| `frontend/src/staff/StaffWorkspace.jsx` | `api.get('/admin/assessments')).data})` |
| `frontend/src/staff/StaffWorkspace.jsx` | `api.get('/admin/users', {params: {per_page: 100}})).data})` |
| `frontend/src/staff/StaffWorkspace.jsx` | `api.get('/documents/${item.id}/content', {responseType: 'blob'})` |
| `frontend/src/staff/StaffWorkspace.jsx` | `api.get('/employee/claims/${selected}')).data})` |
| `frontend/src/staff/StaffWorkspace.jsx` | `api.get(admin ? '/admin/claims' : '/claims/assigned', {params: {page, per_page: 25}})).data})` |
| `frontend/src/staff/StaffWorkspace.jsx` | `api.patch('/claims/${claim.id}/assignment', {employee_id: Number(employee)}))}>Assign employee</button>` |
| `frontend/src/staff/StaffWorkspace.jsx` | `api.patch('/claims/${claim.id}/status', {status,` |
| `frontend/src/staff/StaffWorkspace.jsx` | `api.patch('/dashboard/reviewer/claims/${claim.id}/assignment', {reviewer_id: Number(reviewer)}))}>Assign reviewer</button></>}` |
| `frontend/src/staff/StaffWorkspace.jsx` | `api.post('/admin/users', account)` |
| `frontend/src/staff/StaffWorkspace.jsx` | `api.post('/claims/${claim.id}/assessment/retry', {}))}>Queue assessment or retry report</button>` |
| `frontend/src/staff/StaffWorkspace.jsx` | `api.post('/claims/${job.claim_id}/assessment/retry', {}))}>Retry eligible job</button></li>)}</ul>` |
