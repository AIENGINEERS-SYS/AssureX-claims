export function homeFor(role) {
  return role === 'admin' ? '/dashboard/admin' : role === 'reviewer' ? '/dashboard/reviewer' : role === 'employee' ? '/dashboard/employee' : '/claims';
}
export function canAccess(path, role) {
  if (path === '/' || path.startsWith('/claims') || path.startsWith('/draft')) return role === 'customer';
  if (path.startsWith('/products')) return ['customer', 'admin'].includes(role);
  if (path.startsWith('/dashboard/operations') || path.startsWith('/dashboard/admin')) return role === 'admin';
  if (path.startsWith('/dashboard/employee')) return ['employee', 'admin'].includes(role);
  if (path.startsWith('/dashboard/reviewer')) return ['reviewer', 'admin'].includes(role);
  if (path.startsWith('/dashboard/customer')) return ['customer', 'admin'].includes(role);
  return ['customer', 'employee', 'reviewer', 'admin'].includes(role);
}
