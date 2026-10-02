# Notifyr server

Durable notification storage, recipient inboxes, interactive replies and encrypted
UnifiedPush/browser Web Push for [Notifyr](https://github.com/SuperPauly/Notifyr).
Python 3.13+, FastAPI, protobuf v2, SQLite, pywebpush and one Uvicorn process.
Publication and response success mean **committed to storage**. Push-service
acceptance is recorded separately; device display/read status is not tracked.

Install [uv](https://docs.astral.sh/uv/) and [Hatch](https://hatch.pypa.io/), then:

```bash
uv sync --frozen --group dev
hatch run proto
hatch run check
hatch run admin init
hatch run admin recipient alice
hatch run admin recipient bob
hatch run admin publisher monitor --recipient alice --recipient bob
hatch run serve
```

Token commands print a cryptographically random bearer token **once**. Save it in
the client's secure credential store. Only its SHA-256 hash is persisted. The
admin CLI is local, not a public route. `hatch run admin tokens` lists hashes and
principals; `hatch run admin revoke HASH` revokes a token. `grant APP USER` adds
an application recipient permission; registered users must already exist.

Local development listens on HTTP port 8080. Use HTTPS through the existing edge
proxy for clients. `/docs` and `/openapi.json` document the running server.
Set `NOTIFYR_DB` to choose a persistent database path; the default is
`data/notifyr.sqlite`. Set explicit push origins before registering devices.

- [API contract and copyable examples](API_CONTRACT.md)
- [Deployment, consistent backup and restore](docs/DEPLOYMENT.md)
- [Android/browser integration and physical-device testing](docs/CLIENT_INTEGRATION.md)
- [Acceptance record and verification scope](IMPLEMENTATION.md)

The existing Android client needs HTTPS transports, credential storage, RFC3339
conversion and the versioned envelope/confirmation handlers described in the
integration document. Its demo transport is not an end-to-end server integration.

Run `hatch run smoke` for an isolated Docker build/start/recreation check. It uses
an ephemeral volume and network-disabled containers, then removes only its own
resources. Deployment files are prepared here; no live VPS stack is changed.
