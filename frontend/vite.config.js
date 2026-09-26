import {defineConfig} from 'vite';
import {fileURLToPath} from 'node:url';

export default defineConfig({
  build: {
    outDir: 'static/claims',
    emptyOutDir: true,
    rollupOptions: {
      input: fileURLToPath(new URL('./src/claims/main.jsx', import.meta.url)),
      output: {entryFileNames: 'claims.js', assetFileNames: 'claims.[ext]'},
    },
  },
});
