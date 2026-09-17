# Browser checks (#188)

The focused Chromium spec exercises campaign creation/encoding, unsafe URL
rejection, and server-error feedback in the actual React UI. API responses are
intercepted: these checks do not validate the live backend or authentication.

Opt-in execution (not part of the default build or test command):

```sh
pnpm exec playwright install chromium
pnpm test:e2e e2e/shorten.spec.ts
```

Playwright starts an isolated Vite server on 127.0.0.1:4173 and retains traces
on failure. Generated output is ignored by Git.

Browser execution was interrupted and has not been verified; the user requested
no further Playwright runs. Static TypeScript and ESLint checks passed.
