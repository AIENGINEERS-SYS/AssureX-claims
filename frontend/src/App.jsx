import React, {Suspense, useEffect, useState} from 'react';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';

const ClaimsApp = React.lazy(() => import('./claims/main').then(module => ({default: module.App})));
const DashboardApp = React.lazy(() => import('./dashboard/main').then(module => ({default: module.App})));
const ProductsApp = React.lazy(() => import('./products/ProductsApp'));

const queryClient = new QueryClient({defaultOptions: {queries: {
  staleTime: 15000,
  refetchInterval: 30000,
  refetchOnWindowFocus: true,
  retry: 1,
}}});

export default function App() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const changed = () => setPath(window.location.pathname);
    const navigate = event => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target.closest('a[href]');
      if (!link) return;
      const target = new URL(link.href, window.location.href);
      const sections = ['/products', '/claims', '/dashboard'];
      const currentSection = sections.find(section => window.location.pathname.startsWith(section));
      const targetSection = sections.find(section => target.pathname.startsWith(section));
      if (target.origin !== window.location.origin || !targetSection || targetSection === currentSection) return;
      event.preventDefault();
      window.history.pushState(null, '', `${target.pathname}${target.search}${target.hash}`);
      changed();
    };
    window.addEventListener('popstate', changed);
    document.addEventListener('click', navigate);
    return () => {window.removeEventListener('popstate', changed);document.removeEventListener('click', navigate);};
  }, []);
  const content = path.startsWith('/products')
    ? <ProductsApp />
    : path.startsWith('/dashboard')
      ? <DashboardApp />
      : <ClaimsApp basePath={path.startsWith('/claims') ? '/claims' : ''} />;

  return <QueryClientProvider client={queryClient}>
    <Suspense fallback={<p role="status">Loading AssureX...</p>}>{content}</Suspense>
  </QueryClientProvider>;
}
