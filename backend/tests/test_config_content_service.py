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


@pytest.mark.parametrize("content_format, raw", [
    ("yaml", '# 服务配置\ndb:\n  pool: 10  # 连接池大小\n\n# 保留说明\n'),
    ("properties", '# 服务配置\n! 连接池说明\ndb.pool : 10\n\n'),
])
def test_content_preview_preserves_comments(content_format, raw):
    """重新序列化正文会删除注释；原文应在两种权限预览中保留。"""
    content = validate_and_normalize_content(content_format, raw)
    assert redact_content(content, can_read_secret=True) == raw
    assert redact_content(content, can_read_secret=False) == raw


@pytest.mark.parametrize("content_format, raw, updated", [
    ("yaml", '# 数据库\ndb:\n  password: test-secret  # 密码\n  pool: 10 # 连接池\n',
     f'# 新说明\ndb:\n  password: {SECRET_PLACEHOLDER}  # 密码\n  pool: 20 # 连接池\n'),
    ("properties", '# 数据库\ndb.password : test-secret\n! 连接池\ndb.pool=10\n',
     f'# 新说明\ndb.password : {SECRET_PLACEHOLDER}\n! 连接池\ndb.pool=20\n'),
])
def test_redaction_and_placeholder_save_keep_comments(content_format, raw, updated):
    """脱敏与占位符合并不能删除说明，也不能泄漏或覆盖已有敏感值。"""
    base = validate_and_normalize_content(content_format, raw)
    redacted = redact_content(base, can_read_secret=False)
    assert 'test-secret' not in redacted
    assert '# 数据库' in redacted
    assert ('# 密码' if content_format == 'yaml' else '! 连接池') in redacted
    merged = merge_redacted_update(base, content_format, updated)
    visible = redact_content(merged, can_read_secret=True)
    assert '# 新说明' in visible
    assert ('# 连接池' if content_format == 'yaml' else '! 连接池') in visible
    assert 'test-secret' in visible
    assert SECRET_PLACEHOLDER not in visible
    assert merged.structured == ({'db': {'password': 'test-secret', 'pool': 20}} if content_format == 'yaml'
                                 else {'db.password': 'test-secret', 'db.pool': '20'})


def test_yaml_redacted_anchors_and_sensitive_containers_keep_safe_values():
    raw = ('# 共享配置\nsource: &db\n  password: test-secret\n  pool: 10\n'
           'copy: *db\nsecret: [one, two] # 凭据\n')
    redacted = redact_content(validate_and_normalize_content('yaml', raw), False)
    assert '# 共享配置' in redacted
    assert '# 凭据' in redacted
    assert 'test-secret' not in redacted
    parsed = validate_and_normalize_content('yaml', redacted).structured
    assert parsed['source']['password'] == parsed['copy']['password'] == SECRET_PLACEHOLDER
    assert parsed['secret'] == SECRET_PLACEHOLDER


@pytest.mark.parametrize("raw, comments", [
    ('# 说明\nurl: "https://example.test/#fragment" # 行尾\n', ('# 说明', '# 行尾')),
    ('body: | # 多行\n  # 正文内容\n# 末尾\n', ('# 多行', '# 末尾')),
    ('# 只有注释\n', ('# 只有注释',)),
])
def test_yaml_comment_comparison_ignores_hash_inside_values(raw, comments):
    assert validate_and_normalize_content('yaml', raw).comments == comments


def test_yaml_placeholder_merge_retains_yaml11_values_and_block_comments():
    raw = '# 数据库\npassword: original\nmode: "on"\nlegacy: 012\nbody: | # 内容\n  # 文本\n'
    base = validate_and_normalize_content('yaml', raw)
    displayed = redact_content(base, False)
    merged = merge_redacted_update(base, 'yaml', displayed)
    assert merged.structured == {'password': 'original', 'mode': 'on', 'legacy': 10, 'body': '# 文本\n'}
    assert '# 内容' in redact_content(merged, True)
