/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#070B14',
        panel: '#0D1424',
        edge: '#1E2A44',
        copy: '#E2E8F0',
        muted: '#64748B',
        pipe: '#22D3EE',
        sensor: '#10B981',
        leak: '#EF4444',
        valve: '#F59E0B',
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      borderRadius: { panel: '10px' },
    },
  },
  plugins: [],
};
