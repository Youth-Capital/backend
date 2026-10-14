import test from 'node:test';
import assert from 'node:assert/strict';
import { GitHub, parseImage, readImageEnv, packageVersions, prepublish, cleanup, deploy, isNewer } from './release.mjs';

const context = { owner: 'Youth-Capital', app: 'backend', infraRepo: 'Youth-Capital/infra' };
const image = (number = 5, attempt = 1) => `ghcr.io/youth-capital/backend:backend-${'a'.repeat(40)}-r${number}-100-a${attempt}@sha256:${String(number).repeat(64)}`;
const version = (id, number = id) => ({ id, name: `sha256:${String(number).repeat(64)}`, created_at: `2026-10-${String(id).padStart(2, '0')}T00:00:00Z`,
  metadata: { container: { tags: [`backend-${'a'.repeat(40)}-r${number}-100-a1`] } } });
const file = value => ({ sha: 'blob-sha', content: Buffer.from(`BACKEND_IMAGE=${value}\n`).toString('base64') });
const successful = { id: 90, head_sha: 'infra-sha', status: 'completed', conclusion: 'success', event: 'push', created_at: '2026-10-10T00:00:00Z', html_url: 'https://github.com/Youth-Capital/infra/actions/runs/90' };
const deployed = { jobs: [{ name: 'Deploy backend', conclusion: 'success', steps: [{ name: 'Deploy application', conclusion: 'success' }] }] };

function mock(handler) {
  const calls = [];
  const api = new GitHub({ sourceToken: 'source-test-token', infraToken: 'infra-test-token', pause: async () => {},
    fetchImpl: async (url, options) => {
      const path = new URL(url).pathname + new URL(url).search;
      calls.push({ path, ...options, json: options.body ? JSON.parse(options.body) : undefined });
      const result = await handler(path, options, calls);
      if (result instanceof Error) throw result;
      const status = typeof result?.status === 'number' ? result.status : 200;
      return new Response(status === 204 ? null : JSON.stringify(result?.body ?? result), { status });
    },
  });
  return { api, calls };
}

test('canonical image enforces app, digest and release ordering', () => {
  const parsed = parseImage(image(), context.owner, context.app);
  assert.equal(parsed.runNumber, 5n);
  assert(isNewer(parseImage(image(6), context.owner, context.app), parsed));
  assert(isNewer(parseImage(image(5, 2), context.owner, context.app), parsed));
  assert.throws(() => parseImage(image().replace('youth-capital', 'attacker'), context.owner, context.app));
  assert.throws(() => parseImage(image().split('@')[0], context.owner, context.app));
  assert.throws(() => readImageEnv(`BACKEND_IMAGE=${image()}\nOTHER=value`, context.owner, context.app));
  assert.equal(readImageEnv('# First release pending\nBACKEND_IMAGE=\n', context.owner, context.app), null);
});

test('fully paginates registry versions before deciding retention', async () => {
  const { api, calls } = mock(path => path.includes('&page=1') ? Array.from({ length: 100 }, (_, id) => ({ id: id + 1, created_at: '2026-01-01', metadata: {} })) : [{ id: 101, created_at: '2026-01-02', metadata: {} }]);
  const versions = await packageVersions(api, context);
  assert.equal(versions.length, 101);
  assert.equal(versions[0].id, 101);
  assert.equal(calls.length, 2);
});

test('prepublish keeps newest two plus older infra desired image', async () => {
  const { api, calls } = mock((path, options) => {
    if (path.includes('/contents/')) return file(image(1));
    if (options.method === 'DELETE') return { status: 204 };
    return [version(1), version(2), version(3), version(4)];
  });
  const result = await prepublish(api, context);
  assert.equal(result.previousVersionId, 4);
  assert.deepEqual(calls.filter(call => call.method === 'DELETE').map(call => call.path), ['/orgs/Youth-Capital/packages/container/backend/versions/2']);
});

test('registry listing failure performs no deletion', async () => {
  const { api, calls } = mock(path => path.includes('/contents/') ? file(image(1)) : { status: 403, body: { message: 'Permission denied' } });
  await assert.rejects(prepublish(api, context), /403/);
  assert(!calls.some(call => call.method === 'DELETE'));
});

test('first publication allows absent package and empty manifest', async () => {
  const { api, calls } = mock(path => path.includes('/contents/') ? file('') : { status: 404 });
  const result = await prepublish(api, context);
  assert.equal(result.previousVersionId, '');
  assert(!calls.some(call => call.method === 'DELETE'));
});

test('cleanup retains candidate and previous published version', async () => {
  const { api, calls } = mock((path, options) => {
    if (path.includes('/commits?')) return [{ sha: 'infra-sha' }];
    if (path.includes('/contents/')) return file(image(5));
    if (options.method === 'DELETE') return { status: 204 };
    return [version(2), version(4), version(5)];
  });
  await cleanup(api, context, image(5), '4');
  assert.deepEqual(calls.filter(call => call.method === 'DELETE').map(call => call.path), ['/orgs/Youth-Capital/packages/container/backend/versions/2']);
});

test('stale cleanup never deletes newer registry versions', async () => {
  const { api, calls } = mock(() => file(image(6)));
  await cleanup(api, context, image(5), '4');
  assert.equal(calls.length, 1);
});

for (const [name, newer] of [
  ['a later publish-only release', version(6)],
  ['a later build attempt', { ...version(6, 5), metadata: { container: { tags: [`backend-${'a'.repeat(40)}-r5-100-a2`] } } }],
]) {
  test(`cleanup skips ${name} even when infra still points at the candidate`, async () => {
    const { api, calls } = mock(path => path.includes('/contents/') ? file(image(5)) : [version(4), version(5), newer]);
    await cleanup(api, context, image(5), '4');
    assert(!calls.some(call => call.method === 'DELETE'));
  });
}

test('cleanup aborts when infra changes midway through pruning', async () => {
  let reads = 0;
  const { api, calls } = mock((path, options) => {
    if (path.includes('/contents/')) return file(++reads > 1 ? image(6) : image(5));
    if (options.method === 'DELETE') return { status: 204 };
    return [version(1), version(2), version(4), version(5)];
  });
  await cleanup(api, context, image(5), '4');
  assert(!calls.some(call => call.method === 'DELETE'));
});

test('lost candidate prevents destructive cleanup', async () => {
  const { api, calls } = mock(path => path.includes('/contents/') ? file(image(5)) : [version(4)]);
  await assert.rejects(cleanup(api, context, image(5), '4'), /no longer exists/);
  assert(!calls.some(call => call.method === 'DELETE'));
});

test('same manifest with successful infra run requires no commit or dispatch', async () => {
  const { api, calls } = mock(path => {
    if (path.includes('/jobs?')) return deployed;
    if (path.includes('/commits?')) return [{ sha: 'infra-sha' }];
    if (path.includes('/contents/')) return file(image(5));
    if (path.includes('/actions/workflows/')) return { workflow_runs: [successful] };
    return [version(5)];
  });
  await deploy(api, context, image(5), { pause: async () => {} });
  assert(calls.every(call => call.method === 'GET'));
});

test('same manifest waits for active infra run', async () => {
  const { api, calls } = mock(path => {
    if (path.includes('/jobs?')) return deployed;
    if (path.includes('/commits?')) return [{ sha: 'infra-sha' }];
    if (path.includes('/contents/')) return file(image(5));
    if (path.includes('/actions/workflows/')) return { workflow_runs: [{ ...successful, status: 'in_progress', conclusion: null }] };
    if (path.includes('/actions/runs/')) return successful;
    return [version(5)];
  });
  await deploy(api, context, image(5), { pause: async () => {} });
  assert(calls.every(call => call.method === 'GET'));
});

test('failed deployment fails original job without automatic retry', async () => {
  const failed = { ...successful, conclusion: 'failure' };
  const { api, calls } = mock(path => {
    if (path.includes('/commits?')) return [{ sha: 'infra-sha' }];
    if (path.includes('/contents/')) return file(image(5));
    if (path.includes('/actions/workflows/')) return { workflow_runs: [failed] };
    if (path.includes('/actions/runs/')) return failed;
    return [version(5)];
  });
  await assert.rejects(deploy(api, context, image(5), { pause: async () => {} }), /cleanup is skipped/);
  assert(!calls.some(call => call.method === 'POST'));
});

test('explicit retry dispatches once with expected image and shared apply disabled', async () => {
  const { api, calls } = mock((path, options) => {
    if (path.includes('/jobs?')) return deployed;
    if (path.includes('/commits?')) return [{ sha: 'infra-sha' }];
    if (path.includes('/contents/')) return file(image(5));
    if (options.method === 'POST') return { workflow_run_id: 91, html_url: successful.html_url };
    if (path.includes('/actions/workflows/')) return { workflow_runs: [{ ...successful, conclusion: 'failure' }] };
    if (path.includes('/actions/runs/')) return { ...successful, id: 91 };
    return [version(5)];
  });
  await deploy(api, context, image(5), { allowRetry: true, pause: async () => {} });
  const posts = calls.filter(call => call.method === 'POST');
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].json, { ref: 'main', inputs: { expected_image: image(5), apply_shared: false } });
});

test('stale deploy does not commit or dispatch', async () => {
  const { api, calls } = mock(path => path.includes('/contents/') ? file(image(6)) : [version(5)]);
  await assert.rejects(deploy(api, context, image(5), { allowRetry: true }), /superseded/);
  assert(calls.every(call => call.method === 'GET'));
});

test('missing built image requires full release instead of stale job retry', async () => {
  const { api, calls } = mock(() => []);
  await assert.rejects(deploy(api, context, image(5)), /full application pipeline/);
  assert.equal(calls.length, 1);
});

test('commit conflicts reread file before bounded retry', async () => {
  let commits = 0;
  const { api, calls } = mock((path, options) => {
    if (path.includes('/jobs?')) return deployed;
    if (options.method === 'PUT') return ++commits === 1 ? { status: 409, body: { message: 'Conflict' } } : { commit: { sha: 'infra-sha' } };
    if (path.includes('/contents/')) return file(image(4));
    if (path.includes('/actions/workflows/')) return { workflow_runs: [successful] };
    return [version(5)];
  });
  await deploy(api, context, image(5), { pause: async () => {} });
  assert.equal(commits, 2);
  assert.equal(calls.filter(call => call.path.includes('/contents/') && call.method === 'GET').length, 2);
});

test('ambiguous mutation response is not blindly retried', async () => {
  const { api, calls } = mock(() => new TypeError('Connection lost'));
  await assert.rejects(api.request('/repos/example/contents/file', { infra: true, method: 'PUT', body: {} }), /idempotent/);
  assert.equal(calls.length, 1);
});

test('expired deadline prevents even the first API request', async () => {
  const { api, calls } = mock(() => []);
  api.deadline = Date.now() - 1;
  await assert.rejects(packageVersions(api, context), /deadline/);
  assert.equal(calls.length, 0);
});

for (const previous of ['', '999', 'not-an-id']) {
  test(`missing/invalid previous version (${previous || 'blank'}) cannot erase retention`, async () => {
    const { api, calls } = mock(path => path.includes('/contents/') ? file(image(5)) : [version(2), version(4), version(5)]);
    await assert.rejects(cleanup(api, context, image(5), previous), /Previous published version/);
    assert(!calls.some(call => call.method === 'DELETE'));
  });
}

test('publication ordering follows latest immutable tag even on a reused older digest', async () => {
  const oldDigest = version(1);
  oldDigest.metadata.container.tags.push(`backend-${'a'.repeat(40)}-r4-100-a1`);
  const { api } = mock((path, options) => path.includes('/contents/') ? file('') : options.method === 'DELETE' ? { status: 204 } : [oldDigest, version(2), version(3)]);
  const result = await prepublish(api, context);
  assert.equal(result.previousVersionId, 1);
  assert.deepEqual([...result.kept].sort(), ['1', '3']);
});

test('later pagination error performs no deletion', async () => {
  const { api, calls } = mock(path => {
    if (path.includes('/contents/')) return file('');
    if (path.includes('&page=1')) return Array.from({ length: 100 }, (_, index) => version(1));
    return { status: 403, body: { message: 'Permission denied' } };
  });
  await assert.rejects(prepublish(api, context), /403/);
  assert(!calls.some(call => call.method === 'DELETE'));
});

test('five commit conflicts fail boundedly without dispatch', async () => {
  const { api, calls } = mock((path, options) => options.method === 'PUT' ? { status: 409, body: { message: 'Conflict' } } : path.includes('/contents/') ? file(image(4)) : [version(5)]);
  await assert.rejects(deploy(api, context, image(5), { pause: async () => {} }), /409/);
  assert.equal(calls.filter(call => call.method === 'PUT').length, 5);
  assert(!calls.some(call => call.method === 'POST'));
});

test('successful workflow with skipped deployment is not accepted', async () => {
  const { api } = mock(path => {
    if (path.includes('/jobs?')) return { jobs: [{ name: 'Deploy backend', conclusion: 'skipped', steps: [] }] };
    if (path.includes('/commits?')) return [{ sha: 'infra-sha' }];
    if (path.includes('/contents/')) return file(image(5));
    if (path.includes('/actions/workflows/')) return { workflow_runs: [successful] };
    return [version(5)];
  });
  await assert.rejects(deploy(api, context, image(5)), /actual application deployment/);
});

test('active run deadline fails without dispatch or cleanup mutation', async () => {
  const { api, calls } = mock(path => {
    if (path.includes('/commits?')) return [{ sha: 'infra-sha' }];
    if (path.includes('/contents/')) return file(image(5));
    if (path.includes('/actions/workflows/') || path.includes('/actions/runs/')) return path.includes('/workflows/') ? { workflow_runs: [{ ...successful, status: 'in_progress' }] } : { ...successful, status: 'in_progress' };
    return [version(5)];
  });
  await assert.rejects(deploy(api, context, image(5), { pause: async () => { api.deadline = Date.now() - 1; } }), /20 minutes/);
  assert(calls.every(call => call.method === 'GET'));
});

test('rollback to an old image cannot reuse success before its current pointer commit', async () => {
  const { api, calls } = mock((path, options) => {
    if (path.includes('/commits?')) return [{ sha: 'rollback-sha' }];
    if (path.includes('/compare/')) return { status: 'behind' };
    if (path.includes('/contents/')) return file(image(5));
    if (path.includes('/jobs?')) return deployed;
    if (options.method === 'POST') return { workflow_run_id: 91, html_url: successful.html_url };
    if (path.includes('/actions/workflows/')) return { workflow_runs: [successful] };
    if (path.includes('/actions/runs/')) return { ...successful, id: 91, head_sha: 'rollback-sha' };
    return [version(5)];
  });
  await deploy(api, context, image(5), { allowRetry: true, pause: async () => {} });
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
});
