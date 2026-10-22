import React from 'react';
import {homeFor} from './access';
export default function AccessDenied({role}) {
  return <main className="panel" role="alert"><h1>Access denied</h1><p>Your account does not have access to this workspace.</p>
    <a href={homeFor(role)}>Go to your workspace</a></main>;
}
