# Replication and recovery

SQLite stores actions, request snapshots, events, results, idempotency keys, and model history. Workspace files and requested output files need separate recovery.

## Replicate the database

The image includes Litestream. Startup restores a missing database from the configured replica, preserves an existing database, then starts Landing. Litestream supervises the server, forwards shutdown signals, and attempts a final sync when it stops. Replication is asynchronous; `/up` measures application readiness, while Litestream logs and optional heartbeats report backup health.

Compose defaults to `file:///replica/landing` on a separate named volume. This covers primary-volume loss on the same host. For off-host recovery, select a unique destination and prepare storage credentials:

```bash
export LITESTREAM_REPLICA_URL="s3://example-backups/landing/production"
export AWS_REGION="us-east-1"
export AWS_ACCESS_KEY_ID="your-storage-access-key"
export AWS_SECRET_ACCESS_KEY="your-storage-secret-key"
docker compose up -d
```

The bundled config keeps snapshots for seven days and exposes a local control socket. To wait for synchronization:

```bash
docker compose exec landing litestream sync -wait -socket /run/landing/litestream.sock /storage/landing.sqlite3
```

For other provider settings, mount a standard Litestream config and set `LITESTREAM_CONFIG`. It must replicate the same `LANDING_DB`. A bare server container requires a replica URL or explicit config. CLI commands do not start replication automatically; a single-use CI container can snapshot a closed database with `litestream replicate -once -force-snapshot DB_PATH REPLICA_URL`. See the upstream [container guide](https://litestream.io/guides/docker/) and [replication reference](https://litestream.io/reference/replicate/).

## Restore and verify

1. Stop the old worker and prevent another writer from opening the database.
2. Retain the replica and restore or prepare the registered workspaces.
3. Start with a fresh primary volume and the same replica configuration.
4. Check `/up`, inspect action history, and review interrupted work.
5. Execute a task in the restored workspace and verify its native checks.

Restored active actions become `interrupted`; queued actions resume. Keep the original replica while verifying recovery. Exercise remote storage and its credentials in the environment where you will need them.

## Recover workspaces

Use your infrastructure's filesystem backups or checkout provisioning. With [ONCE](deploy.md#deploy-with-once), enable full-volume backups; Landing uses paused-container backups rather than claiming mutable workspaces are safe to copy live. Restoring SQLite alone does not restore the files a task needs.
