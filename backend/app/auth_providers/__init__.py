"""认证源抽象（02-技术架构 §3.1）：local → ldap 链式尝试，OIDC 走回调流程。"""
from app.auth_providers.base import AuthProvider
from app.auth_providers.ldap import LdapAuthProvider
from app.auth_providers.local import LocalAuthProvider

__all__ = ["AuthProvider", "LocalAuthProvider", "LdapAuthProvider"]
