#!/usr/bin/env bash
# OpsPilot 数据备份脚本（在部署机上执行，产物落 deploy/backup/<时间戳>/）。
# 用法：cd deploy && bash scripts/backup.sh
# 备份范围：
#   1. MySQL 全库逻辑备份（mysqldump --single-transaction，不锁表）
#   2. 执行日志卷 exec-logs（tar 归档，包含执行日志）
# 恢复：
#   MySQL：docker exec -i opspilot-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' < mysql_opspilot.sql
#   日志卷：docker run --rm -v opspilot_exec-logs:/data -v <备份目录>:/backup alpine \
#           tar xzf /backup/exec_logs.tar.gz -C /data
set -euo pipefail

cd "$(dirname "$0")/.."   # 定位到 deploy/
STAMP=$(date +%Y%m%d_%H%M%S)
OUT="backup/${STAMP}"
mkdir -p "${OUT}"

echo "[1/2] MySQL 全库备份 ..."
# 密码从容器环境变量读取，避免出现在宿主命令行历史
docker exec opspilot-mysql sh -c \
  'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --triggers "$MYSQL_DATABASE"' \
  > "${OUT}/mysql_opspilot.sql"
echo "      -> ${OUT}/mysql_opspilot.sql ($(du -h "${OUT}/mysql_opspilot.sql" | cut -f1))"

echo "[2/2] 执行日志卷备份 ..."
docker run --rm -v opspilot_exec-logs:/data:ro -v "$(pwd)/${OUT}":/backup alpine \
  tar czf /backup/exec_logs.tar.gz -C /data .
echo "      -> ${OUT}/exec_logs.tar.gz ($(du -h "${OUT}/exec_logs.tar.gz" | cut -f1))"

echo "BACKUP DONE -> deploy/${OUT}"
