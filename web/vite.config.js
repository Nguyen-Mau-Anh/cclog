import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Builds into the Python package so the daemon can serve it and
// `pip install` ships it without needing Node.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../cclog/web/dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:7331',
      '/events': 'http://127.0.0.1:7331',
    },
  },
});
