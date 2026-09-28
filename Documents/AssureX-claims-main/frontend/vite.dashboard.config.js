import {defineConfig} from 'vite';
import tailwindcss from '@tailwindcss/vite';
import {fileURLToPath} from 'node:url';

export default defineConfig({
  plugins: [tailwindcss()],
  build: {
    outDir: 'static/dashboard', emptyOutDir: true,
    rollupOptions: {
      input: fileURLToPath(new URL('./src/dashboard/main.jsx', import.meta.url)),
      output: {entryFileNames: 'dashboard.js', assetFileNames: 'dashboard.[ext]'},
    },
  },
});
