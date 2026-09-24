"""应用配置正文规范化与脱敏测试。"""
import pytest

from app.services.config_content_service import (
    SECRET_PLACEHOLDER,
    merge_redacted_update,
    redact_content,
    validate_and_normalize_content,
)


def test_yaml_secret_is_redacted_and_preserved_by_placeholder():
    """没有密钥查看权限时，保留占位符只能继承原有敏感值。"""
    base = validate_and_normalize_content("yaml", "db:\n  password: p@ss\n  pool: 10\n")
    redacted = redact_content(base, can_read_secret=False)
    assert "p@ss" not in redacted
    assert SECRET_PLACEHOLDER in redacted

    changed = merge_redacted_update(
        base,
        "yaml",
        f"db:\n  password: {SECRET_PLACEHOLDER}\n  pool: 20\n",
    )
    assert changed.structured["db"]["password"] == "p@ss"
    assert changed.structured["db"]["pool"] == 20


def test_properties_rejects_duplicate_key_and_redacts_token():
    """properties 的键唯一性与敏感键脱敏必须由服务端保证。"""
    content = validate_and_normalize_content("properties", "api_token=abc\nfeature=true\n")
    assert "abc" not in redact_content(content, can_read_secret=False)
    with pytest.raises(ValueError, match="重复"):
        validate_and_normalize_content("properties", "feature=true\nfeature=false\n")


def test_text_is_wholly_masked_without_secret_read_and_cannot_use_placeholder():
    """TEXT 内容不可分字段脱敏，缺少权限时只能整体替换。"""
    content = validate_and_normalize_content("text", "private text")
    assert redact_content(content, can_read_secret=False) == "********"
    with pytest.raises(ValueError, match="TEXT"):
        merge_redacted_update(content, "text", SECRET_PLACEHOLDER)


def test_consul_kv_requires_json_object_of_string_values():
    """Consul 前缀以 key/value JSON 对象表示，避免丢失二进制语义。"""
    content = validate_and_normalize_content("consul_kv", '{"feature/enabled":"true"}')
    assert content.structured == {"feature/enabled": "true"}
    with pytest.raises(ValueError, match="字符串"):
        validate_and_normalize_content("consul_kv", '{"feature/enabled":true}')


def test_redacted_update_cannot_delete_sensitive_parent_or_insert_secret():
    base = validate_and_normalize_content("yaml", "db:\n  password: p@ss\n  pool: 10\n")
    with pytest.raises(ValueError, match="敏感"):
        merge_redacted_update(base, "yaml", "other: true\n")
    with pytest.raises(ValueError, match="敏感"):
        merge_redacted_update(base, "yaml", "db:\n  password: replacement\n  pool: 10\n")


def test_embedded_service_account_and_signed_url_are_masked_and_preserved():
    raw = ('{"service_account_key_json":"TEST_PRIVATE_KEY",'
           '"access_key_id":"TEST_ACCESS_ID",'
           '"callback_url":"https://example.test/callback?mode=read&sig=TEST_SIGNATURE",'
           '"plain_url":"https://example.test/status?mode=read"}')
    base = validate_and_normalize_content("json", raw)
    redacted = redact_content(base, can_read_secret=False)
    assert "TEST_PRIVATE_KEY" not in redacted
    assert "TEST_ACCESS_ID" not in redacted
    assert "TEST_SIGNATURE" not in redacted
    assert "https://example.test/status?mode=read" in redacted

    updated = ('{"service_account_key_json":"' + SECRET_PLACEHOLDER + '",'
               '"access_key_id":"' + SECRET_PLACEHOLDER + '",'
               '"callback_url":"' + SECRET_PLACEHOLDER + '",'
               '"plain_url":"https://example.test/status?mode=write"}')
    merged = merge_redacted_update(base, "json", updated)
    assert merged.structured["callback_url"].endswith("sig=TEST_SIGNATURE")
    assert merged.structured["plain_url"].endswith("mode=write")
    with pytest.raises(ValueError, match="敏感"):
        merge_redacted_update(base, "json", updated.replace(SECRET_PLACEHOLDER, "REPLACED", 3))


def test_malformed_url_text_does_not_break_content_preview():
    base = validate_and_normalize_content("json", '{"endpoint":"http://["}')
    assert 'http://[' in redact_content(base, can_read_secret=False)
