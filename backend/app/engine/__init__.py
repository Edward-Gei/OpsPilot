"""执行引擎包（M5，02-技术架构 §4）。

模块划分：
    control        控制信号（pause/resume/abort/force_abort）读写与执行期控制状态
    events         执行事件总线：seq 单调递增 + List 回放 + PubSub 实时推送
    logs           日志通道：文件落盘 + 100ms 聚合 PubSub 推送
    ssh_runner     Shell 步骤执行器（asyncssh 直连目标主机）
    ansible_runner Ansible 步骤执行器（SSH 到作业主机跑 ansible-playbook + RECAP 解析）
    pipeline       Pipeline 调度器：步骤串行 / 分批 / 并发 / 超时 / 失败策略 / 四检查点
    recovery       Worker 崩溃恢复三分支（启动时执行）
"""
