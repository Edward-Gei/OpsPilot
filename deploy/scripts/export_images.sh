#!/usr/bin/env bash
# OpsPilot 镜像离线导出/导入脚本（纯内网部署的镜像搬运通道）。
# 导出（有外网的构建机）：bash deploy/scripts/export_images.sh export
#   -> 产物 deploy/offline/opspilot_images.tar.gz（含 5 个镜像）
# 导入（内网部署机）：bash deploy/scripts/export_images.sh import
#   -> docker load 后即可 docker compose up -d（无需拉取外网镜像）
set -euo pipefail

cd "$(dirname "$0")/.."   # 定位到 deploy/
OFFLINE_DIR="offline"
TARBALL="${OFFLINE_DIR}/opspilot_images.tar.gz"

# 五个服务镜像清单：三个业务镜像（Compose 构建产物）和两个官方基础镜像。
IMAGES=(
  "opspilot-api:latest"
  "opspilot-worker:latest"
  "opspilot-nginx:latest"
  "mysql:8.0"
  "redis:7-alpine"
)

case "${1:-}" in
  export)
    echo "[1/2] 构建业务镜像（确保导出的是最新代码）..."
    docker compose build
    echo "[2/2] 导出镜像 -> ${TARBALL} ..."
    mkdir -p "${OFFLINE_DIR}"
    docker save "${IMAGES[@]}" | gzip > "${TARBALL}"
    echo "EXPORT DONE -> deploy/${TARBALL} ($(du -h "${TARBALL}" | cut -f1))"
    echo "连同 deploy/ 目录（compose/nginx.conf/mysql/init/.env.example）一起拷贝到内网部署机"
    ;;
  import)
    test -f "${TARBALL}" || { echo "未找到 ${TARBALL}，请先从构建机拷贝"; exit 1; }
    echo "导入镜像 ..."
    gunzip -c "${TARBALL}" | docker load
    echo "IMPORT DONE。后续：cp .env.example .env 并修改密钥 -> docker compose up -d"
    ;;
  *)
    echo "用法：$0 export|import"; exit 1
    ;;
esac
