/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          primary: "#0D1117",
          secondary: "#161B22",
          tertiary: "#21262D",
        },
        border: {
          primary: "#30363D",
        },
        text: {
          primary: "#E6EDF3",
          secondary: "#8B949E",
          muted: "#484F58",
        },
        accent: {
          blue: "#58A6FF",
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
