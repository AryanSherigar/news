import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { defineConfig } from 'vite';

export default defineConfig(({ mode }) => {
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      host: '0.0.0.0',
      proxy: {
        '/api': {
          target: process.env.VITE_BACKEND_URL || 'http://localhost:8000',
          changeOrigin: true,
          ws: true,
          rewrite: (path) => path,
        },
      },
      hmr: process.env.DISABLE_HMR !== 'true',
    },
  };
});
