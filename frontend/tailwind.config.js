/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          primary: "#0a0e18",
          secondary: "#0f1724",
          tertiary: "#162032",
        },
        border: {
          primary: "#1e3050",
        },
        text: {
          primary: "#e0e8f0",
          secondary: "#7a8ea0",
          muted: "#3a5070",
        },
        accent: {
          blue: "#3a8fff",
          cyan: "#00c8ff",
          green: "#3FB950",
          red: "#F85149",
          yellow: "#D29922",
          purple: "#BC8CFF",
          orange: "#F0883E",
        },
      },
    },
  },
  plugins: [],
};
