// Tailwind v4 ships its own PostCSS plugin (no separate tailwind.config.js
// required — config lives inline in globals.css via @theme). Autoprefixer
// covers older browsers without bloating the dev experience.
export default {
  plugins: {
    "@tailwindcss/postcss": {},
    autoprefixer: {},
  },
};
