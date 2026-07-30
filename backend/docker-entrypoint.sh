#!/bin/sh
# 容器入口：仅 api 容器（RUN_MIGRATIONS=1）执行数据库迁移（冲突声明 C4），
# worker 依赖 api 健康后启动，天然避免并发迁移。
set -e

if [ "${RUN_MIGRATIONS}" = "1" ]; then
  echo "[entrypoint] 执行数据库迁移 alembic upgrade head ..."
  alembic upgrade head
fi

exec "$@"
