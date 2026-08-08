import nextCoreWebVitals from "eslint-config-next/core-web-vitals";

const eslintConfig = [
  ...nextCoreWebVitals,
  {
    ignores: [".next/**", "playwright-report/**", "test-results/**", "node_modules/**"],
  },
];

export default eslintConfig;
