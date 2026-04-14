/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class'],
  content: [
    './src/app/**/*.{ts,tsx}',
    './node_modules/@daveyplate/better-auth-ui/dist/**/*.{js,mjs}',
  ],
  corePlugins: {
    preflight: false,
  },
  theme: { extend: {} },
  plugins: [],
}

