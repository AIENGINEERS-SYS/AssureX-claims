import React from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {App} from './Dashboard';
import './dashboard.css';

const client = new QueryClient({defaultOptions: {queries: {
  staleTime: 15000, refetchInterval: 30000, refetchOnWindowFocus: true, retry: 1,
}}});
createRoot(document.getElementById('root')).render(<QueryClientProvider client={client}><App/></QueryClientProvider>);
