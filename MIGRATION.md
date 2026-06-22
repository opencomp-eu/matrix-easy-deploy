# Migration guide: YAML-first configuration

This project now uses `deploy.yaml` as the operator-owned source of truth.

## What changed

- Before: interactive setup generated and managed `.env` directly.
- Now: `deploy.yaml` is edited by operator or wizard, then `bash apply.sh` generates runtime artifacts.

Generated artifacts include:
- `.env`
- `caddy/Caddyfile`
- `modules/core/synapse/homeserver.yaml` (when `matrix.server_implementation: synapse`)
- `modules/core/tuwunel/tuwunel.toml` (when `matrix.server_implementation: tuwunel`)
- `modules/core/element/config.json` (if enabled)
- `modules/calls/coturn/turnserver.conf`
- `modules/calls/livekit/livekit.yaml`
- `.matrix-easy-deploy/secrets.yaml`
- `.matrix-easy-deploy/modules.yaml`

## Migration steps

1. Ensure your current deployment is healthy.
2. Create or review `deploy.yaml` (wizard: `bash matrix-wizard.sh` -> first setup).
3. Apply configuration:

```bash
bash apply.sh
```

4. Restart services if your changes require it:

```bash
bash stop.sh
bash start.sh
```

## Idempotency expectations

- Re-running `bash apply.sh` preserves generated secrets by default.
- Module desired state comes from `deploy.yaml` (`modules.*.enabled`).
- Module setup scripts now use idempotent `.env` upserts for module keys.

## Intentional secret rotation

If you intentionally want to rotate generated secrets:

```bash
bash apply.sh --rotate-secrets
```

This can invalidate existing integrations and credentials. Plan downtime/migration before using it.

## Choosing Tuwunel instead of Synapse

Add to `deploy.yaml` under `matrix`:

```yaml
matrix:
  server_implementation: tuwunel
```

Then run `bash apply.sh` and `bash start.sh` as usual. Admin tooling (`scripts/create-account.sh`, `scripts/med-admin.sh`) uses Tuwunel's `docker exec … --execute` admin commands instead of Synapse's HTTP admin API. Do not change `server_implementation` on a deployment that already has user data unless you are starting fresh.

## Moving to a new host

You no longer need to copy the Borg repository directory manually. Export a portable archive from the old host and restore it on the new one:

```bash
# Old host
bash backup.sh --export ~/med-kit-backup.tar.gz --encrypt

# New host (fresh clone)
bash bootstrap-from-backup.sh ~/med-kit-backup.tar.gz --encrypt --yes
```

Local Borg retention on the old host is unchanged; portable export is an additional copy step.

## Unifying admin accounts (existing deployments)

Older setups may have two admin accounts: the wizard account (`matrix.admin_username`, often `admin`) and a separate tooling account created by med-admin (often `med-admin`).

To align with the current single-account model:

1. Decide which account to keep (recommended: `matrix.admin_username` from `deploy.yaml`).
2. Re-grant bridge permissions if bridge config points at the wrong MXID (re-run the bridge module setup or edit `permissions` in the bridge config).
3. Run `bash scripts/med-admin.sh bootstrap --username <chosen> --password <password>` to store credentials in `.env`.
4. Optionally deactivate the unused admin account via `med-admin list-admins` and manual cleanup.
