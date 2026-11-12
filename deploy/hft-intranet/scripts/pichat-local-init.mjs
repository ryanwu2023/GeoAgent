#!/usr/bin/env node
// Runs only in the environment's one-shot initializer. Never in an application process.
import { readFileSync, writeFileSync, mkdirSync, existsSync, cpSync, chownSync, readdirSync, chmodSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import knexFactory from 'knex';
import YAML from 'yaml';

const { environmentId, config, assetRevision } = JSON.parse(readFileSync('/run/provision/input.json', 'utf8'));
if (!/^[a-z][a-z0-9-]{1,24}$/.test(environmentId)) throw new Error('LOCAL_ENVIRONMENT_ID_INVALID');
const runtime = '/var/lib/pi-chat/runtime';
const data = '/var/lib/pi-chat/data';
const ownerFile = `${runtime}/environment-owner.json`;
if (existsSync(ownerFile) && JSON.parse(readFileSync(ownerFile, 'utf8')).id !== environmentId) throw new Error('LOCAL_RUNTIME_OWNER_MISMATCH');
const moduleAt = (path) => import(pathToFileURL(join(process.cwd(), 'dist', path)).href);
function privateFile(path, content) {
  writeFileSync(path, content, { mode: 0o600 });
  chmodSync(path, 0o600); chownSync(path, 1000, 1000);
}
function secret(name) {
  const path = `/run/provision/${name}`;
  if (!existsSync(path)) privateFile(path, randomBytes(32).toString('hex'));
  return readFileSync(path, 'utf8').trim();
}
function ownTree(path) {
  chownSync(path, 1000, 1000);
  for (const entry of readdirSync(path, { withFileTypes: true })) {
    const child = join(path, entry.name);
    if (entry.isDirectory()) ownTree(child);
    else if (!entry.isSymbolicLink()) chownSync(child, 1000, 1000);
  }
}
for (const path of [runtime, data, '/var/lib/pi-chat/logs', '/var/lib/pi-chat/keys', '/run/report-secret', '/run/report-runtime', '/run/report-data']) {
  mkdirSync(path, { recursive: true, mode: 0o700 }); chownSync(path, 1000, 1000);
}
const url = (role, password) => `postgresql://${role}:${encodeURIComponent(password)}@postgres:5432/pichat`;
const adminUrl = url('postgres', readFileSync('/run/pg-secret/password', 'utf8').trim());
const roles = { migration: 'pichat_migration', mainOwner: 'pichat_app_owner', reportOwner: 'pichat_report_owner', main: 'pichat_app', report: 'pichat_report' };
const admin = knexFactory({ client: 'pg', connection: adminUrl });
try {
  for (const role of Object.values(roles)) {
    const found = await admin.raw('select 1 from pg_roles where rolname = ?', [role]);
    if (!found.rows.length) await admin.raw('CREATE ROLE ?? NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION', [role]);
  }
  for (const role of [roles.migration, roles.main, roles.report]) {
    // Generated hex only; pg cannot bind a password in DDL.
    const password = secret(role);
    if (!/^[a-f0-9]{64}$/.test(password)) throw new Error('LOCAL_DATABASE_SECRET_INVALID');
    await admin.raw(`ALTER ROLE ?? LOGIN PASSWORD '${password}'`, [role])
      .catch(() => { throw new Error('LOCAL_ROLE_CREDENTIAL_UPDATE_FAILED'); });
  }
  await admin.raw('GRANT ??, ?? TO ??', [roles.mainOwner, roles.reportOwner, roles.migration]);
  await admin.raw('GRANT CREATE ON DATABASE pichat TO ??', [roles.migration]);
  await admin.raw('REVOKE CREATE ON SCHEMA public FROM PUBLIC');
  await admin.raw('GRANT CREATE, USAGE ON SCHEMA public TO ??', [roles.migration]);
  await admin.raw('GRANT CREATE, USAGE ON SCHEMA public TO ??', [roles.mainOwner]);
  await admin.raw('GRANT EXECUTE ON FUNCTION pg_control_system() TO ??', [roles.main]);
  await admin.raw('GRANT EXECUTE ON FUNCTION pg_control_system() TO ??', [roles.migration]);
} finally { await admin.destroy(); }
const migrationUrl = url(roles.migration, secret(roles.migration));
const runtimeUrl = url(roles.main, secret(roles.main));
const reportUrl = url(roles.report, secret(roles.report));
const migrationEnv = { ...process.env, PI_CHAT_DATABASE_URL: migrationUrl, PI_CHAT_MIGRATION_DATABASE_URL: migrationUrl,
  PI_CHAT_MAIN_DATABASE_ROLE: roles.mainOwner, PI_CHAT_REPORT_DATABASE_ROLE: roles.reportOwner,
  PI_CHAT_MIGRATION_DATABASE_ROLE: roles.migration };
async function command(script, args, env, input) {
  await new Promise((yes, no) => {
    const child = spawn(process.execPath, [script, ...args], { env, stdio: [input ? 'pipe' : 'ignore', 'inherit', 'inherit'] });
    child.once('error', no); child.once('exit', (code) => code === 0 ? yes() : no(new Error(`LOCAL_INIT_COMMAND_FAILED:${script}:${code}`)));
    if (input) {
      // A script that exits before reading its input leaves the write to hit EPIPE; unheard, that error kills the
      // initializer. Its exit code decides instead. Any other stdin error means the input never arrived.
      child.stdin.on('error', (error) => {
        if (error.code === 'EPIPE' || error.code === 'ERR_STREAM_DESTROYED') return;
        child.kill('SIGKILL'); no(error);
      });
      child.stdin.end(input);
    }
  });
}
await command('scripts/db/dbos-schema.mjs', ['--apply'], migrationEnv);
await command('scripts/db/migrate.mjs', ['--apply'], migrationEnv);
// PostgreSQL requires the new owner to be able to create in the containing schema.
const ownership = knexFactory({ client: 'pg', connection: migrationUrl });
try {
  for (const [schema, owner] of [['app', roles.mainOwner], ['report', roles.reportOwner]]) {
    await ownership.raw('GRANT USAGE, CREATE ON SCHEMA ?? TO ??', [schema, owner]);
  }
} finally { await ownership.destroy(); }
await command('scripts/db/reconcile-service-roles.mjs', ['--apply'], migrationEnv);
const migration = knexFactory({ client: 'pg', connection: migrationUrl });
let fingerprint;
try {
  const { markSiteEnvironmentNonProduction, readDatabaseSystemIdentifier } = await moduleAt('platform/database/site-environment.js');
  await markSiteEnvironmentNonProduction(migration, { siteId: environmentId, markedBy: 'local-environment-init' });
  fingerprint = await readDatabaseSystemIdentifier(migration);
  const { initializeLocalTaskRuntime } = await moduleAt('bootstrap/local-task-runtime.js');
  const taskRuntime = await initializeLocalTaskRuntime(migration, { site: environmentId, releaseSha: assetRevision, systemIdentifier: fingerprint });
  privateFile('/run/provision/task-runtime-activation.json', JSON.stringify(taskRuntime, null, 2));
  for (const [schema, role] of [['app', roles.main], ['report', roles.report], ['dbos', roles.main], ['dbos', roles.report]]) {
    await migration.raw('GRANT USAGE ON SCHEMA ?? TO ??', [schema, role]);
    await migration.raw('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ?? TO ??', [schema, role]);
    await migration.raw('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ?? TO ??', [schema, role]);
    await migration.raw('GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ?? TO ??', [schema, role]);
  }
  await migration.raw('GRANT USAGE ON SCHEMA app TO ??', [roles.report]);
  for (const signature of ['app.authorize_api_key(text,text,text,text,text)', 'app.acquire_api_key_lease(text,text,text,integer)', 'app.release_api_key_lease(text,text,text)', 'app.report_readable_task_ids(text)', 'app.report_schema_version()']) {
    await migration.raw(`GRANT EXECUTE ON FUNCTION ${signature} TO ??`, [roles.report]);
  }
  await migration.raw('GRANT SELECT ON ALL TABLES IN SCHEMA public TO ??', [roles.main]);
  await migration.raw('REVOKE INSERT, UPDATE, DELETE ON app.site_environment FROM ??', [roles.main]);
} finally { await migration.destroy(); }
// Versioned assets are owned by this VM; the daemon never resolves a Mac path.
const assets = `/run/assets/${assetRevision}`;
if (!existsSync(`${assets}/.ready`)) {
  mkdirSync(assets, { recursive: true });
  for (const path of ['agents', 'skill_lib', 'platform-assets', 'release-assets', 'plugins', 'packages', 'dist', 'PLUGIN_ARTIFACT_MANIFEST.json']) {
    cpSync(join(process.cwd(), path), join(assets, path), { recursive: true, dereference: false });
  }
  ownTree(assets); privateFile(`${assets}/.ready`, assetRevision);
}
privateFile(`${runtime}/config.yaml`, YAML.stringify(config));
// Deployment-owned fields also have an existing PG projection. Publish through
// its schema/revision service; a stale active image must not override this deployment.
const { loadConfig } = await moduleAt('config.js');
const { PostgresSiteSettingsRepository } = await moduleAt('modules/settings/postgres-repository.js');
const { SiteSettingsService } = await moduleAt('modules/settings/service.js');
const settingsDatabase = knexFactory({ client: 'pg', connection: runtimeUrl });
try {
  const repository = new PostgresSiteSettingsRepository(settingsDatabase);
  const current = await repository.readActiveValues();
  const service = new SiteSettingsService(repository, loadConfig(runtime));
  for (const [namespace, value] of [
    ['docker', { ...config.docker, ...current.get('docker'), image: config.docker.image, containerPrefix: config.docker.containerPrefix }],
    ['agentRunner', { ...current.get('agentRunner'), ...config.agentRunner }],
    ['publicUrl', config.publicUrl],
  ]) {
    const result = await service.save(namespace, value, 'local-environment-deploy');
    if (result.activation.status !== 'active') throw new Error(`LOCAL_DEPLOY_CONFIG_REJECTED:${namespace}`);
  }
} finally { await settingsDatabase.destroy(); }
const { writeRuntimeProfile } = await moduleAt('bootstrap/runtime-profile.js');
writeRuntimeProfile(`${runtime}/runtime-profile.json`, { runtimeDir: runtime, databaseUrl: runtimeUrl,
  remoteDatabase: { systemIdentifier: fingerprint, siteEnvironment: 'non-production', verifiedAt: new Date().toISOString() } });
chownSync(`${runtime}/runtime-profile.json`, 1000, 1000);
privateFile('/run/report-secret/database-url', reportUrl);
const { ensureInternalToken } = await moduleAt('billing-rpc.js');
const internalToken = ensureInternalToken(data); ownTree(data);
// Report receives its own runtime and data directories. Only the same-environment
// signing secret is projected; no application DB profile, user workspace or root key.
privateFile('/run/report-runtime/config.yaml', YAML.stringify({
  siteId: environmentId, agentsDir: config.agentsDir, dataDir: '/var/lib/pi-chat/report-data',
  storage: { engine: 'postgres', postgres: { pool: { max: 8 } } },
}));
privateFile('/run/report-data/.internal-token', internalToken);
await command('scripts/ensure-admin.mjs', [runtime, 'admin', '-'], { ...process.env, PI_CHAT_DATABASE_URL: runtimeUrl }, secret('admin-password'));
ownTree(data);
privateFile(ownerFile, JSON.stringify({ id: environmentId, fingerprint, assetRevision }));
console.log(`LOCAL_ENVIRONMENT_INITIALIZED:${environmentId}:database=${fingerprint}`);
