# Deployment and recovery

The image uses Ubuntu 24.04, managed Python 3.13 and locked runtime dependencies.
Uvicorn runs one worker as UID/GID 10001. Compose mounts a named volume at `/data`,
uses a read-only root filesystem, drops capabilities and exposes port 8080 only to
the existing external `pangolin` Docker network. No host port is published.

These assets are prepared for deployment; the live VPS has not been changed.
Replace all `.env.example` placeholders before using them. Set `NOTIFYR_HOST` to
your HTTPS hostname and adjust Traefik entrypoint/certificate-resolver labels to
your existing proxy configuration. If Pangolin manages resources through its UI,
create an HTTPS resource pointing to `notifyr:8080` on the shared network instead
of assuming that Traefik's Docker provider consumes these labels. Verify the
existing network and routing conventions first. No fixed IP is fabricated here.

Set `FORWARDED_ALLOW_IPS` to the actual trusted proxy address or bounded CIDR;
never use `*`. Set `NOTIFYR_CORS_ORIGINS` to explicit browser origins, separated by
commas. Android does not need CORS. Set `NOTIFYR_VAPID_SUBJECT` to a real operator
mailto or HTTPS contact. List each allowed push-service HTTPS origin in
`NOTIFYR_PUSH_ORIGINS`. Private self-hosted origins require explicit
`NOTIFYR_PRIVATE_PUSH_ORIGINS`; loopback, link-local, multicast and unspecified
addresses remain blocked even with that exception. Allow DNS and outbound HTTPS
to these services. Do not route capability URLs through an unrestricted proxy.

For a reviewed deployment:

```sh
cp .env.example .env
# Edit .env with your hostname, trusted proxy, CORS, push origins and contact.
docker compose config --quiet
docker compose build
docker compose up -d
docker compose exec notifyr notifyr-admin init
docker compose exec notifyr notifyr-admin recipient alice
docker compose exec notifyr notifyr-admin recipient bob
docker compose exec notifyr notifyr-admin publisher example-app --recipient alice --recipient bob
```

Provisioning prints each opaque token once. Transfer it directly to the intended
client's protected credential storage. Recipient tokens represent a user across
that user's installations; publisher tokens represent one registered application
and its grants. Keep tokens out of shell arguments, Compose files, logs and source
control. Local `notifyr-admin tokens` lists hashes and principals; revoke using a
hash from that inventory. There is no public administration endpoint.

Check `/health`, `/ready`, `/v1/server-info` and authenticated `/v1/me` through the
HTTPS proxy. Confirm the certificate, host routing, rejection of missing/invalid
tokens and expected CORS headers. Check `docker compose ps` and logs. Logs contain
sanitised events and diagnostics, not message bodies, reply text, bearer tokens,
subscription URLs or keys. `notifyr-admin push-status` reports aggregate queue
status, including push-service acceptance; it cannot prove device display/read.

## Consistent backup

The SQLite database contains the server UUID, allocator, VAPID private/public
keys, token hashes, grants, messages, replies, subscriptions and delivery queue.
One consistent backup preserves them together. It is sensitive even though
bearer tokens are hashed. Restrict access and encrypt stored/off-host backups.
Do not copy just the live SQLite file while its WAL is active.

The administration CLI uses SQLite's online backup API, including committed WAL
content. The destination must not exist and is created with mode 0600:

```sh
docker compose exec notifyr notifyr-admin backup /data/backup-2026-10-01.sqlite
# Copy to a protected operator-controlled location using your backup tooling.
# Remove old in-volume backups only after validating the protected copy.
```

For a local run, `hatch run admin backup /secure/path/backup.sqlite` uses the same
procedure. Ensure its parent directory exists and is restricted. Run
`PRAGMA integrity_check` on the backup with SQLite and require `ok`; retain the
expected `/v1/server-info` UUID and public key for comparison after recovery.
Do not regenerate VAPID keys as part of backup or restart.

## Restore

Stop Notifyr before restoring. Preserve the current volume and its database/WAL
for investigation. In the stopped service's volume, replace the database with a
verified backup and remove only that database's stale `-wal`/`-shm` companions;
never mix a restored main database with a different WAL. Set ownership to
10001:10001 and database mode 0600. Restart and compare the server UUID and VAPID
public key with the backup record, verify readiness and authenticated inbox and
response polling, and inspect queue diagnostics. Never run two copies against
the same SQLite volume. Startup rejects missing, inconsistent or unsupported
identity metadata instead of silently replacing it.

A restore rolls back to the backup's point in time. For strict ID non-reuse,
stop/quiesce publishing and replies before taking a planned recovery snapshot.
If disaster recovery uses an older snapshot after newer IDs escaped to clients,
do not reopen publication until the allocator is advanced above every issued ID
using trustworthy retained evidence. If that high-water mark cannot be proved,
use a new server identity/connection and explicitly retire the old one; restoring
an old allocator under the old identity cannot preserve the non-reuse invariant.
Accepted replies after the snapshot may need reconciliation with sending apps.

Expired worker leases are reclaimed on restart. A push accepted just before a
crash may be sent again; clients must deduplicate and reconcile revisions. Keep
the database/VAPID identity when changing hostname and update the registered
client connection URL. `from_server` is an identity, never an arbitrary URL.

## Checks before deploying

```sh
hatch run check
uv run --frozen --group dev ruff format --check .
hatch run smoke
docker compose config --quiet
```

`smoke` builds an isolated image and runs disposable containers/volume with no
network or host ports. It checks non-root execution, readiness, authentication,
publish/get, clean shutdown and persistence across recreation, then removes its
own resources. It does not activate this Compose stack or change the live proxy.

SQLite and one Uvicorn worker bound write throughput. Move transactions and
delivery leases to PostgreSQL before scaling to concurrent server writers.
