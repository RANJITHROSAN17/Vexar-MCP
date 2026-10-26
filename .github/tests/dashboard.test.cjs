// Test the actual inline dashboard script with a minimal DOM and mocked fetch.
// No browser, network requests, npm dependencies, or HTML execution are required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../../assets/dashboard.html'), 'utf8');
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map(match => match[1]).find(text => text.includes('async function loadAuditData'));
assert.ok(script, 'dashboard loader script must exist');

class Element {
  constructor() { this.children = []; this.text = ''; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(value) { throw new Error('Do not render report content as HTML'); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.text = ''; this.children = children; }
}
function fixture(diagnostics = [{rule_id:'MCP005', severity:'error', source_file:'examples/test.py', line:4, message:'Unsafe call'}]) {
  return {
    summary: {score: 82, tools_scanned: 3},
    diagnostics,
    dashboard: {scope:'examples/', generated_at:'2026-10-27T01:00:00+00:00'},
  };
}
function setup(fetcher) {
  const elements = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element()]));
  const context = {
    document: {
      getElementById: id => elements.get(id) || null,
      createElement: () => new Element(),
      addEventListener: () => {},
    },
    fetch: fetcher,
    console: {warn: () => {}},
  };
  vm.createContext(context);
  vm.runInContext(script, context);
  return {context, elements, text: id => elements.get(id).textContent};
}
function success(report) { return async () => ({ok:true, json:async()=>report}); }
function assertUnavailable(app) {
  assert.equal(app.text('dashboard-status'), 'Report unavailable');
  for (const id of ['score-display','tools-display','findings-display','scan-result','last-updated']) {
    assert.equal(app.text(id), '--');
  }
  assert.match(app.text('violation-list'), /Unable to load/);
}

test('renders the real reporter schema and uses a rename-safe relative URL', async () => {
  const app = setup(async (url, options) => {
    assert.equal(url, './audit-report.json');
    assert.equal(options.cache, 'no-store');
    return {ok:true, json:async()=>fixture()};
  });
  await app.context.loadAuditData();
  assert.equal(app.text('score-display'), '82');
  assert.equal(app.text('tools-display'), '3');
  assert.equal(app.text('findings-display'), '1');
  assert.match(app.text('violation-list'), /MCP005.*examples\/test.py:4.*Unsafe call/);
  assert.equal(app.text('last-updated'), 'Tue, 27 Oct 2026 01:00:00 GMT');
  assert.match(html, /Vexar-MCP/);
  assert.doesNotMatch(html, /90\.77%|Passing Gates|Strict Pass|github\.com\/RANJITHROSAN17\/mcp-doctor/);
});

test('empty findings do not claim the whole repository is safe', async () => {
  const app = setup(success(fixture([])));
  await app.context.loadAuditData();
  assert.equal(app.text('findings-display'), '0');
  assert.equal(app.text('scan-result'), 'No findings');
  assert.match(app.text('violation-list'), /not a production safety guarantee/);
});

test('source-controlled messages remain text, not executable markup', async () => {
  const payload = '<img src=x onerror="globalThis.pwned=true">';
  const report = fixture();
  report.diagnostics[0].message = payload;
  report.diagnostics[0].rule_id = '<script>attack()</script>';
  const app = setup(success(report));
  await app.context.loadAuditData();
  assert.ok(app.text('violation-list').includes(payload));
  assert.ok(app.text('violation-list').includes('<script>attack()</script>'));
  assert.equal(app.context.pwned, undefined);
});

for (const [name, fetcher] of [
  ['HTTP error', async () => ({ok:false,status:404})],
  ['network error', async () => {throw new Error('offline');}],
  ['invalid JSON', async () => ({ok:true,json:async()=>{throw new Error('bad JSON');}})],
  ['obsolete report schema', success({score:100, violations:[]})],
  ['malformed diagnostic', success({...fixture(), diagnostics:[null]})],
  ['invalid report timestamp', success({...fixture(), dashboard:{scope:'examples/',generated_at:'invalid'}})],
  ['null report timestamp', success({...fixture(), dashboard:{scope:'examples/',generated_at:null}})],
]) {
  test(name + ' shows an explicit unavailable state', async () => {
    const app = setup(fetcher);
    await app.context.loadAuditData();
    assertUnavailable(app);
  });
}

test('failed refresh clears stale success data', async () => {
  const app = setup(success(fixture()));
  await app.context.loadAuditData();
  assert.equal(app.text('score-display'), '82');
  app.context.fetch = async () => {throw new Error('offline');};
  await app.context.loadAuditData();
  assertUnavailable(app);
});
