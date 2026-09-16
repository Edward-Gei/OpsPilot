"""DNS 服务商适配器注册表。"""
from app.core.constants import DomainProvider
from app.dns_providers.base import DnsProviderAdapter


def get_adapter(provider: DomainProvider) -> DnsProviderAdapter:
    """按服务商延迟导入适配器，避免应用启动时创建 SDK 客户端。"""
    if provider is DomainProvider.AWS_ROUTE53:
        from app.dns_providers.aws_route53 import AwsRoute53Adapter

        return AwsRoute53Adapter()
    if provider is DomainProvider.TENCENT_DNSPOD:
        from app.dns_providers.tencent_dnspod import TencentDnsPodAdapter

        return TencentDnsPodAdapter()
    if provider is DomainProvider.GOOGLE_CLOUD_DNS:
        from app.dns_providers.google_cloud_dns import GoogleCloudDnsAdapter

        return GoogleCloudDnsAdapter()
    raise ValueError(f"未知 DNS 服务商: {provider}")
