/* Offline client acceptance: transpile the actual TS sources and execute the
 * session client with browser objects, never printing credentials. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const ts = require(path.join(root, 'dashboard/node_modules/.pnpm/typescript@5.9.3/node_modules/typescript'));
const sources = ['lib/api-session.ts', 'app/assurance-actions.tsx', 'app/control-plane-sandbox.tsx',
                 'app/evidence-lanes.tsx', 'app/model-replay.tsx'];
let compiledClient;
for (const name of sources) {
  const source = fs.readFileSync(path.join(root, 'dashboard', name), 'utf8');
  const result = ts.transpileModule(source, {
    fileName: name, reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS,
                       jsx: ts.JsxEmit.ReactJSX, strict: true },
  });
  assert.equal((result.diagnostics || []).filter(d => d.category === ts.DiagnosticCategory.Error).length, 0);
  if (name === 'lib/api-session.ts') compiledClient = result.outputText;
}

async function main() {
  const token = 'fixture-session-'.repeat(4);
  const calls = [];
  const history = [];
  const window = {
    location: { hash: '#orion_session=' + token, origin: 'http://127.0.0.1:18765',
                hostname: '127.0.0.1', pathname: '/', search: '', href: 'http://127.0.0.1:18765/' },
    history: { replaceState: (...args) => history.push(args) },
  };
  const sandbox = { exports: {}, window, URL, URLSearchParams, Headers,
    process: { env: {} }, fetch: async (url, init) => {
      calls.push({ url, init });
      return { ok: true, status: 200 };
    } };
  vm.createContext(sandbox);
  vm.runInContext(compiledClient, sandbox, { timeout: 1000 });
  const client = sandbox.exports;
  assert.equal(client.getApiRoot(), 'http://127.0.0.1:18765/api/v1');
  await client.authenticatedFetch(client.getApiRoot() + '/health');
  assert.equal(history.length, 1);
  assert.equal(history[0][2], '/');
  assert.equal(calls[0].init.headers.get('Authorization'), 'Bearer ' + token);
  assert.equal(calls[0].init.credentials, 'omit');
  assert.equal(calls[0].init.redirect, 'error');
  assert.equal(calls[0].url.includes(token), false);
  await client.authenticatedFetch(client.getApiRoot() + '/summary');
  assert.equal(history.length, 1);
  await assert.rejects(() => client.authenticatedFetch('https://untrusted.example.test/api'), /mesma origem/);
  assert.equal(calls.length, 2);
  client.clearApiSession();
  await client.authenticatedFetch(client.getApiRoot() + '/health');
  assert.equal(calls[2].init.headers.has('Authorization'), false);
  console.log(JSON.stringify({ passed: true, syntactic_sources: sources.length,
    cases: ['fragment_removed', 'memory_only', 'same_origin', 'opaque_bearer', 'no_query_secret',
            'no_cookies', 'redirect_rejected', 'clear_session', 'dynamic_local_port'],
    full_project_typecheck: false, reason: 'existing pnpm module links lack project type definitions' }));
}
main().catch(error => { console.error(error.name); process.exitCode = 1; });
