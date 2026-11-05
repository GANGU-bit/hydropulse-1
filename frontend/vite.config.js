import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { existsSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const mockDirectory = fileURLToPath(new URL('./public/mock/', import.meta.url));
// Expose fixture names without requiring the optional mock directory on a fresh clone.
const mockFiles = existsSync(mockDirectory)
  ? readdirSync(mockDirectory).filter((file) => file.endsWith('.json'))
  : [];
export default defineConfig({
  plugins: [react()],
  define: { __HYDROPULSE_MOCK_FILES__: JSON.stringify(mockFiles) },
  server: {
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          const path = id.replaceAll('\\', '/');
          if (!path.includes('/node_modules/')) return;
          if (/\/(recharts|d3-[^/]+|victory-vendor)\//.test(path))
            return 'charts';
          if (/\/(leaflet|react-leaflet)\//.test(path)) return 'map';
          if (/\/(react|react-dom|scheduler)\//.test(path)) return 'react';
        },
      },
    },
  },
});
