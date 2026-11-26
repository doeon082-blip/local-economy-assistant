import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backendUrl = process.env.VITE_BACKEND_URL || 'http://127.0.0.1:8765';

export default defineConfig({
  base: process.env.VITE_BASE || '/',
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: backendUrl,
        changeOrigin: true,
      },
      '/healthz': {
        target: backendUrl,
        changeOrigin: true,
      },
    },
  },
});
