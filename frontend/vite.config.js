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
          // Rollup's shared CommonJS interop helper must stay in the react
          // chunk. Left in the charts chunk it makes react import charts, which
          // closes the charts <-> react cycle and leaves the page blank.
          if (path.includes('commonjsHelpers')) return 'react';
          if (!path.includes('/node_modules/')) return;
          // Match exact package directories only. A loose /\/(react)\// test also
          // caught @xyflow/react, which drags d3 into the react chunk and creates
          // a charts <-> react import cycle (blank page: "Cannot access 'P'
          // before initialization").
          if (/\/node_modules\/(recharts|d3-[^/]+|victory-vendor)\//.test(path))
            return 'charts';
          if (/\/node_modules\/(leaflet|react-leaflet)\//.test(path))
            return 'map';
          if (/\/node_modules\/(react|react-dom|scheduler)\//.test(path))
            return 'react';
        },
      },
    },
  },
});
