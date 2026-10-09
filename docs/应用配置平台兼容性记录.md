# 应用配置平台兼容性记录

本模块通过 HTTP API 访问外部平台。下表的“目标版本”是产品范围，不等于已经完成真实服务端联调；除 Nacos 2.2.3 的连接及发现外，其余操作仅有可注入 HTTP transport 的离线契约测试，不能据此宣称具体发行版兼容。

| 平台 | 目标版本 | 认证 | 真实服务端联调 |
| --- | --- | --- | --- |
| Apollo | v2.5.2（2026-09-23 查询官方最新稳定发行版） | Open API Token | 未完成 |
| Nacos | 2.2.3 至 2.5.0，需逐版本验证 | 用户名密码或 Token | 2.2.3 完成连接及配置发现；现有实例完成命名空间列表和选定命名空间的只读发现（版本未核对）；其余操作和版本未完成 |
| Consul | v2.0.4（2026-09-23 查询官方最新稳定发行版） | ACL Token | 未完成 |

Apollo v2.5.2 的官方 Open API 文档确认：发现使用 `GET /openapi/v1/envs/PRO/apps/{appId}/clusters/{cluster}/namespaces`；读取最新 Release 使用 `GET .../namespaces/{namespace}/releases/latest`；首次创建私有 Namespace 使用 `POST /openapi/v1/apps/{appId}/appnamespaces`，须先确认 PRO 集群存在，再创建 Item、发布 Release。已有 Namespace 但无 Release 可直接创建 Item 后发布。私有非 properties Namespace 名称带 `.yaml` 或 `.json` 后缀；Apollo 不提供 TEXT Namespace 格式，因此 Apollo 只开放 properties、YAML、JSON。创建和发布所用的 `OpsPilot` 操作账号须在 Apollo 存在并获得修改、发布及新建 Namespace 所需权限；若 PRO 启用了“编辑者不得自行发布”的 Namespace 锁，当前单一操作账号可能被拒绝，须在真实联调中确认或另行设计双账号映射。

Nacos 发现模式先通过 `/nacos/v1/console/namespaces` 获取命名空间名称与 ID，选择后逐页读取 `/nacos/v1/cs/configs` 的 `pageItems` 和 `totalCount`；默认 `public` 的定位与配置 API `tenant` 使用空字符串。命名空间列表没有授权时可使用手工定位模式；2.2.3 的现场配置发现请求需显式携带空的 `dataId` 和 `group` 参数，筛选时用指定值覆盖。写前复读精确 `tenant/group/dataId`。现有 Nacos 实例已完成命名空间列表及选定命名空间的配置发现只读调用（列表包含 `public`）；默认命名空间当前无配置可供进一步核对。未核对该实例版本，也未逐版本真实联调。Consul 前缀写入使用最多 64 个 KV 操作的 `/v1/txn`，对已读键执行 CAS/索引校验；读取后的新键插入并不能由该事务锁定。Apollo、Nacos 的发布写入也没有已验证的厂商原子 CAS；即使应用在写前再读一次，仍存在最后一次读取后的外部并发窗口。不能把这些保障表述为远端原子条件发布。

当前离线测试只验证 OpsPilot 请求、响应解析、错误分类和写后回读逻辑；真实联调需在隔离环境分别验证发现分页、已发布配置读取、首次创建、超时回读、权限拒绝、远端并发改动、Consul 多键事务和版本差异。联调记录只能保存版本、日期、通过/失败的操作和脱敏后的响应类型，不得保存 URL 中的敏感参数、Token、密码或配置正文。

版本与端点核对来源：[Apollo 官方 v2.5.2 Open API 文档](https://github.com/apolloconfig/apollo/blob/v2.5.2/docs/zh/portal/apollo-open-api-platform.md)、[Apollo 官方发行版](https://github.com/apolloconfig/apollo/releases)、[Consul 官方发行版](https://github.com/hashicorp/consul/releases)。
