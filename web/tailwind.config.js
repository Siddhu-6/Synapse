/* Colours are CSS variables (index.css): carbon greys, bone type, one sodium-amber signal, and a
   muted oxide red that only ever means "failed". */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        paper: 'rgb(var(--paper) / <alpha-value>)',
        paper2: 'rgb(var(--paper2) / <alpha-value>)',
        paper3: 'rgb(var(--paper3) / <alpha-value>)',
        card: 'rgb(var(--card) / <alpha-value>)',
        rule: 'rgb(var(--rule) / <alpha-value>)',
        rule2: 'rgb(var(--rule2) / <alpha-value>)',
        ink: 'rgb(var(--ink) / <alpha-value>)',
        ink2: 'rgb(var(--ink2) / <alpha-value>)',
        muted: 'rgb(var(--muted) / <alpha-value>)',
        faint: 'rgb(var(--faint) / <alpha-value>)',
        signal: 'rgb(var(--signal) / <alpha-value>)',
        signalink: 'rgb(var(--signalink) / <alpha-value>)',
        alert: 'rgb(var(--alert) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['"Archivo Variable"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"Martian Mono Variable"', 'ui-monospace', 'Menlo', 'monospace'],
      },
      borderRadius: { DEFAULT: '0px', sm: '1px', md: '2px', lg: '3px' },
      keyframes: {
        impulse: { '0%': { top: '0%', opacity: 0 }, '8%': { opacity: 1 }, '92%': { opacity: 1 }, '100%': { top: '100%', opacity: 0 } },
        rise: { from: { opacity: 0, transform: 'translateY(4px)' }, to: { opacity: 1, transform: 'none' } },
        blink: { '0%,100%': { opacity: 1 }, '50%': { opacity: 0.25 } },
        sweep: { from: { transform: 'translateX(-100%)' }, to: { transform: 'translateX(100%)' } },
      },
      animation: {
        impulse: 'impulse 1.8s cubic-bezier(.45,0,.55,1) infinite',
        rise: 'rise .2s ease-out both',
        blink: 'blink 1.1s steps(2) infinite',
        sweep: 'sweep 1.6s cubic-bezier(.45,0,.55,1) infinite',
      },
    },
  },
}
