# 后端镜像：api 与 worker 共用，compose 中以不同 command 启动
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# 非 root 运行；/data/logs 为执行日志卷挂载点
RUN useradd -m -u 1000 opspilot && mkdir -p /data/logs && chown -R opspilot:opspilot /app /data/logs
USER opspilot

ENTRYPOINT ["/docker-entrypoint.sh"]
# 默认启动 api；worker 容器由 compose 覆盖 command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
