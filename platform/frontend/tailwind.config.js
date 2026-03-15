/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["IBM Plex Sans", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "Consolas", "monospace"]
      },
      colors: {
        bg: "#10161c",
        panel: "#17212b",
        accent: "#67e8f9",
        success: "#22c55e",
        warn: "#f59e0b",
        danger: "#ef4444"
      }
    }
  },
  plugins: []
};

