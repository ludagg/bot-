#!/usr/bin/env bash
# Sauvegarde quotidienne : pg_dump compressé vers un stockage objet compatible S3.
# Cron : 0 3 * * * /opt/sih/scripts/backup.sh
# Variables : BACKUP_BUCKET (ex. s3://mon-bucket/sih), BACKUP_ENDPOINT (optionnel, S3 non-AWS).
set -euo pipefail
cd "$(dirname "$0")/.."
f="sih-$(date -u +%Y%m%d-%H%M).sql.gz"
docker compose exec -T postgres pg_dump -U sih sih | gzip > "/tmp/$f"
if [ -n "${BACKUP_BUCKET:-}" ]; then
  aws s3 cp "/tmp/$f" "$BACKUP_BUCKET/$f" ${BACKUP_ENDPOINT:+--endpoint-url "$BACKUP_ENDPOINT"}
  rm "/tmp/$f"
else
  mkdir -p backups && mv "/tmp/$f" backups/
fi
