#!/bin/sh
# sshd 矩阵入口：挂 secondary IP -> 配 root 密码 -> 前台起 sshd
# IP 规划：172.28.4.0/22 段内从 172.28.4.1 起连续分配 IP_COUNT 个（默认 500），
# 避开 IPAM 分配段 172.28.0.0/24，CMDB 导入的主机 IP 与此一一对应。
set -e

IP_COUNT="${IP_COUNT:-500}"
ROOT_PASSWORD="${ROOT_PASSWORD:-Loadtest123}"

# 1. 给 eth0 追加 secondary IP：172.28.4.1..172.28.4.250, 172.28.5.1..172.28.5.250
i=0
octet3=4
octet4=1
while [ "$i" -lt "$IP_COUNT" ]; do
    ip addr add "172.28.${octet3}.${octet4}/16" dev eth0 2>/dev/null || true
    i=$((i + 1))
    octet4=$((octet4 + 1))
    if [ "$octet4" -gt 250 ]; then
        octet4=1
        octet3=$((octet3 + 1))
    fi
done
echo "[loadtest] secondary IP 就绪：${IP_COUNT} 个（172.28.4.1 起）"

# 2. root 密码登录（仅压测容器，非生产配置）
echo "root:${ROOT_PASSWORD}" | chpasswd
sed -i 's/#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
sed -i 's/#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
# 500 并发建连：调大未认证连接队列，避免压测期拒连
echo "MaxStartups 500:30:600" >> /etc/ssh/sshd_config
ssh-keygen -A

# 3. 前台运行 sshd（监听 0.0.0.0，全部 secondary IP 生效）
exec /usr/sbin/sshd -D -e
