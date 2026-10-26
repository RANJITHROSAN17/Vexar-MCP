// Execute the actual inline workflow script with an in-memory GitHub API.
// No API requests, credentials, package installs, or third-party modules.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const workflow = fs.readFileSync(path.join(__dirname, '../workflows/welcome-bot.yml'), 'utf8');
const match = workflow.match(/^          script: \|\r?\n((?:            .*\r?\n?)*)/m);
assert.ok(match, 'expected a single inline GitHub script');
const script = match[1].replace(/^ {12}/gm, '');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const execute = new AsyncFunction('github', 'context', 'core', script);
const marker = '<!-- mcp-doctor:first-contribution-welcome -->';
const cases = [
  ['maintainer PR', 'pull_request', 'RANJITHROSAN17', 'User', 'OWNER', [], 0],
  ['bot PR', 'pull_request', 'dependabot[bot]', 'Bot', 'FIRST_TIMER', [], 0],
  ['repeat contributor', 'pull_request', 'contributor', 'User', 'CONTRIBUTOR', [], 0],
  ['first PR', 'pull_request', 'new-user', 'User', 'FIRST_TIMER', [], 1],
  ['first issue', 'issue', 'new-user', 'User', 'FIRST_TIME_CONTRIBUTOR', [], 1],
  ['already greeted', 'pull_request', 'new-user', 'User', 'FIRST_TIMER', [{user:{type:'Bot'},body:marker}], 0],
  ['user marker cannot impersonate bot', 'pull_request', 'new-user', 'User', 'FIRST_TIMER', [{user:{type:'User'},body:marker}], 1],
  ['maintainer issue', 'issue', 'RANJITHROSAN17', 'User', 'OWNER', [], 0],
  ['malicious title stays data', 'pull_request', 'new-user', 'User', 'FIRST_TIMER', [], 1],
  ['missing payload', null, '', '', '', [], 0],
];
for (const [name,event,login,type,association,comments,wanted] of cases) {
  test(name, async () => {
    const created=[];
    let listed=0;
    const item={number:29,user:{login,type},author_association:association,title:'`; globalThis.executedUntrustedCode = true; //'};
    const github={rest:{issues:{listComments:Symbol('list'),createComment:async params=>created.push(params)}},paginate:async()=>{listed++; return comments;}};
    const context={repo:{owner:'RANJITHROSAN17',repo:'mcp-doctor'},payload:event?{[event]:item}:{},issue:{number:29}};
    await execute(github,context,{info:()=>{}});
    assert.equal(created.length,wanted);
    assert.equal(globalThis.executedUntrustedCode,undefined);
    if (wanted) {
      assert.equal(created[0].issue_number,29);
      assert.equal(created[0].owner,'RANJITHROSAN17');
      assert.ok(created[0].body.includes(marker));
    }
    if (!event || type === 'Bot' || association === 'OWNER' || association === 'CONTRIBUTOR') {
      assert.equal(listed,0, 'ineligible greetings must not call the API');
    }
  });
}
