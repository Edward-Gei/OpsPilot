"""动态参数预生成服务测试。"""

import json
import shlex

import pytest

from app.services import parameter_prepare_service


def test_generator_command_quotes_single_quote_parameter_for_shell():
    """动态参数含单引号时，导出命令仍须保留单层 JSON。"""
    params = {"RSYNC_OPTS": "[\"--chown=centos':'centos\"]"}

    command = parameter_prepare_service._generator_command(params)

    export_command, bash_command = command.split("; ", 1)
    assert bash_command == "bash -s"
    export_parts = shlex.split(export_command)
    assert export_parts[0] == "export"
    assert json.loads(export_parts[1].split("=", 1)[1]) == params


@pytest.mark.asyncio
async def test_generator_sends_rendered_script_with_shell_safe_parameters(monkeypatch):
    """参数导出修复不能改变生成脚本的渲染和 SSH 调用方式。"""
    class _Result:
        exit_status = 0
        stdout = "{}"

    class _Connection:
        command = None
        script = None

        async def run(self, command, input):
            self.command = command
            self.script = input
            return _Result()

        def close(self):
            pass

    class _Host:
        ip = "10.0.0.10"
        ssh_port = 22

    connection = _Connection()

    async def fake_open_connection(*args):
        return connection

    params = {"SERVICE_NAME": "mt-web", "RSYNC_OPTS": "[\"--chown=centos':'centos\"]"}
    monkeypatch.setattr(parameter_prepare_service, "open_connection", fake_open_connection)

    await parameter_prepare_service._run_generator(
        _Host(), object(), "SERVICE={{ SERVICE_NAME }}", 60, params,
    )

    assert connection.script == "SERVICE=mt-web"
    export_command, bash_command = connection.command.split("; ", 1)
    assert bash_command == "bash -s"
    assert json.loads(shlex.split(export_command)[1].split("=", 1)[1]) == params
