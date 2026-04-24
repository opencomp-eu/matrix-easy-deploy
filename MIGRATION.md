# Migration guide: YAML-first configuration

This project now uses `deploy.yaml` as the operator-owned source of truth.

## What changed

- Before: interactive setup generated and managed `.env` directly.
- Now: `deploy.yaml` is edited by operator or wizard, then `bash apply.sh` generates runtime artifacts.

Generated artifacts include:
- `.env`
- `caddy/Caddyfile`
- `modules/core/synapse/homeserver.yaml`
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
