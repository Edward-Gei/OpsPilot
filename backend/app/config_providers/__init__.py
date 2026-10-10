"""配置平台适配器注册表。"""
from app.config_providers.apollo import ApolloAdapter
from app.config_providers.base import ConfigPlatformAdapter
from app.config_providers.consul import ConsulAdapter
from app.config_providers.nacos import NacosAdapter
from app.core.constants import ConfigProvider


def get_config_adapter(provider: ConfigProvider | str) -> ConfigPlatformAdapter:
    """每次返回无数据库状态的适配器，连接信息由服务层按需传入。"""
    value = provider.value if isinstance(provider, ConfigProvider) else provider
    if value == ConfigProvider.APOLLO.value:
        return ApolloAdapter()
    if value == ConfigProvider.NACOS.value:
        return NacosAdapter()
    if value == ConfigProvider.CONSUL.value:
        return ConsulAdapter()
    raise ValueError("不支持的配置平台")
