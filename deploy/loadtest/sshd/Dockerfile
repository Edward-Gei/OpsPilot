# 压测 sshd 矩阵镜像：openssh + ansible + python3（兼作 Ansible 作业主机）
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-server iproute2 sshpass \
    && pip install --no-cache-dir ansible-core==2.16.6 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd

COPY entrypoint.sh /entrypoint.sh
# Windows 检出可能带 CRLF，进容器前归一为 LF
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
