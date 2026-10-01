# Replication and recovery

Plan recovery for the database and workspaces separately. SQLite contains actions, events, request snapshots, idempotency keys, results, and model history. Workspace files and requested output files are ordinary filesystem artifacts.

## Replicate the database

Litestream 0.5.17 is included in the image. Server startup restores a missing database from the configured replica before starting Landing; an existing database is preserved. Litestream supervises the server, forwards shutdown signals, and attempts a final sync after Landing stops. It replicates the entire database, including actions, events, idempotency keys, and model execution history. Replication is asynchronous; `/up` reports application readiness, while Litestream logs and its optional heartbeat configuration report backup health.

Compose defaults to `file:///replica/landing` in a separate named `replica` volume. This allows recovery after losing the primary volume, but both volumes remain on the same host. For a backup outside that host, set a unique replica URL for this installation and provide your storage credentials:

```bash
export LITESTREAM_REPLICA_URL="s3://example-backups/landing/production"
export AWS_REGION="us-east-1"
export AWS_ACCESS_KEY_ID="your-storage-access-key"
export AWS_SECRET_ACCESS_KEY="your-storage-secret-key"
docker compose up -d
```

The bundled `/etc/litestream.yml` keeps snapshots for seven days and enables a local control socket at `/run/landing/litestream.sock`. For S3-compatible endpoints or other provider settings, mount a standard Litestream config and set `LITESTREAM_CONFIG` to its path. That config must replicate the same `LANDING_DB` used by Landing. A bare server container requires `LITESTREAM_REPLICA_URL` or an explicit config; it fails startup when neither is configured. CLI commands use the normal entry point and do not start replication automatically.

```bash
docker compose exec landing litestream sync -wait -socket /run/landing/litestream.sock /storage/landing.sqlite3
```

To recover a lost database, stop the old container, retain its replica, and start the service with a fresh primary volume and the same replica configuration. Restored active actions become `interrupted`; queued actions resume. Restoring SQLite does not restore workspace files, so prepare the same registered checkouts before restarting the service. Single-use CI containers can explicitly back up a closed database with `litestream replicate -once -force-snapshot DB_PATH REPLICA_URL` using the image's binary.

## Verify recovery

1. Stop the old worker and prevent another writer from opening the database.
2. Retain the replica and restore or prepare the registered workspaces.
3. Start with a fresh primary volume and the same replica configuration.
4. Check `/up`, then inspect action history and interrupted work.
5. Run a task against the restored workspace and verify the relevant native checks.

Do not remove the original replica while exercising recovery. File replicas on the same host cover primary-volume loss, not host loss. Test your chosen remote storage and credentials in their actual environment.

## Cover workspace recovery

Use your infrastructure's filesystem backup or checkout provisioning process. With ONCE, enable full-volume backups to cover workspace files as well as SQLite. The image uses ONCE's default paused-container backup behavior; it has no live workspace-copy hook. A Litestream replica and a full-volume backup address different recovery needs. See [Deploy Landing](deploy.md).
