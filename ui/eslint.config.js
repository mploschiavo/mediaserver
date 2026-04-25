// ESLint flat config for the React UI.
//
// Ratchet rules locked at 0:
//   - no-console (allow warn/error in error-handling paths)
//   - no-only-tests/no-only-tests
//   - @typescript-eslint/no-explicit-any
//
// `pnpm lint` runs with --max-warnings=0, so any new occurrence fails CI.
//
// Plugins required (see .ratchets/pending/C-eslint.json):
//   - eslint-plugin-no-only-tests
//   - @typescript-eslint/eslint-plugin
//   - @typescript-eslint/parser

import js from '@eslint/js';
import tseslint from '@typescript-eslint/eslint-plugin';
import tsParser from '@typescript-eslint/parser';
import noOnlyTests from 'eslint-plugin-no-only-tests';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';

export default [
  // Ignore generated / build artifacts.
  {
    ignores: [
      'dist/**',
      'coverage/**',
      'storybook-static/**',
      'node_modules/**',
      'src/api/types.ts', // OpenAPI-generated; contains JSDoc @example with console.log in comments.
      'src/routeTree.ts', // TanStack Router-generated.
    ],
  },

  // Base JS recommended rules.
  js.configs.recommended,

  // TypeScript / TSX source files (NON-test).
  // no-console + no-explicit-any apply here.
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: [
      'src/**/*.test.{ts,tsx}',
      'src/**/*.spec.{ts,tsx}',
      'src/**/__tests__/**/*.{ts,tsx}',
      'src/test/**/*.{ts,tsx}',
    ],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 'latest',
        sourceType: 'module',
        ecmaFeatures: { jsx: true },
      },
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: {
      '@typescript-eslint': tseslint,
      react,
      'react-hooks': reactHooks,
      'no-only-tests': noOnlyTests,
    },
    rules: {
      // --- Ratchet rules (locked at 0) ---
      'no-console': ['error', { allow: ['warn', 'error'] }],
      '@typescript-eslint/no-explicit-any': 'error',
      'no-only-tests/no-only-tests': 'error',
      // Defer unused-locals to TS (`noUnusedLocals` in tsconfig). The
      // core rule false-positives on TS-only constructs (type-only
      // imports, declaration files), so we use the TS-aware variant
      // and allow `_`-prefixed names for intentionally-unused params.
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      // TS already enforces "undefined identifier" via the compiler;
      // ESLint's `no-undef` double-flags TypeScript type names as
      // runtime globals (RequestInit, React in JSX-namespace usage,
      // etc.) and produces false positives. Defer to tsc.
      'no-undef': 'off',
      // Older `js.configs.recommended` flag — keep removed-code
      // tracking off; we have tests + types covering this.
      'preserve-caught-error': 'off',
    },
    settings: {
      react: { version: 'detect' },
    },
  },

  // Test files: still enforce no .only and no `any`, but skip no-console.
  {
    files: [
      'src/**/*.test.{ts,tsx}',
      'src/**/*.spec.{ts,tsx}',
      'src/**/__tests__/**/*.{ts,tsx}',
      'src/test/**/*.{ts,tsx}',
    ],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 'latest',
        sourceType: 'module',
        ecmaFeatures: { jsx: true },
      },
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: {
      '@typescript-eslint': tseslint,
      'no-only-tests': noOnlyTests,
    },
    rules: {
      // --- Ratchet rules (locked at 0) for tests ---
      // no-console is intentionally NOT enabled here: tests sometimes use
      // console.error in assertion paths and the `allow: ["warn", "error"]`
      // override would otherwise flag any direct console.log usage. If
      // pushback arises, re-enable here with the same allow-list.
      '@typescript-eslint/no-explicit-any': 'error',
      'no-only-tests/no-only-tests': 'error',
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      // TS already enforces "undefined identifier" via the compiler;
      // ESLint's `no-undef` double-flags TypeScript type names as
      // runtime globals (RequestInit, React in JSX-namespace usage,
      // etc.) and produces false positives. Defer to tsc.
      'no-undef': 'off',
      // Older `js.configs.recommended` flag — keep removed-code
      // tracking off; we have tests + types covering this.
      'preserve-caught-error': 'off',
    },
  },
];
