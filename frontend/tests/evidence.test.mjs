import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {mkdtemp, rm} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const temp = await mkdtemp(path.join(root, 'node_modules', '.evidence-test-'));
try {
  const file = path.join(temp, 'render.mjs');
  await build({stdin: {contents: `
    import React from 'react';
    import {renderToStaticMarkup} from 'react-dom/server';
    import {DocumentsStep} from './src/claims/components.jsx';
    export function render(documents) {return renderToStaticMarkup(React.createElement(DocumentsStep,
      {documents, upload(){}, remove(){}, review(){}, retry(){}, busy:false}));}
  `, resolveDir: root, loader:'jsx'}, bundle:true, platform:'node', format:'esm',
    packages:'external', define:{'import.meta.env':'{}'}, outfile:file});
  const {render} = await import(pathToFileURL(file));
  const base = {id:1, file_name:'photo.png', file_type:'image/png', file_size:100,
    ocr_status:'completed_with_warnings', processing_status:'NEEDS_REVIEW', review_status:'not_required',
    extracted_data:{}, verified_data:{}, validation_warnings:['Manual assessment required.']};
  for (const [kind, fields] of [['product_image',['product_name','model_number','serial_number']], ['damage_evidence',[]]]) {
    const html = render([{...base, document_type:kind, applicable_fields:fields}]);
    for (const key of ['invoice_number','retailer','purchase_price','purchase_date','warranty_duration']) {
      assert.ok(!html.includes(`ocr-1-${key}`), `${kind} incorrectly renders ${key}`);
    }
    assert.ok(html.includes('Manual assessment required.'));
    assert.ok(!html.includes('Damage assessment is complete.'));
    for (const key of fields) assert.ok(html.includes(`ocr-1-${key}`));
  }
  const receipt = render([{...base,document_type:'receipt',applicable_fields:['invoice_number','purchase_date']}]);
  assert.ok(receipt.includes('ocr-1-invoice_number'));
  assert.ok(receipt.includes('ocr-1-purchase_date'));
  console.log('3 evidence component render checks passed');
} finally {await rm(temp,{recursive:true,force:true});}
