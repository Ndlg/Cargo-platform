# 现场基线 field-20260727-004852

记录时间：2026-07-27 00:48:52 +08:00

这是 5173 现场正在运行版本的不可变回退节点。建立基线时没有重启容器、修改业务代码或读取业务数据。

## 代码对应关系

| 组件 | 现场来源 | 当前仓库关系 |
| --- | --- | --- |
| 后端 | `5559bb1b3329dedd71942ea24fb324265c13403f` | 已包含在 `main` 历史中 |
| 采集器 | `5559bb1b3329dedd71942ea24fb324265c13403f` | 已包含在 `main` 历史中 |
| 识别服务 | `62c21ad5247a4245a5c3cc0a82da1ca95723172d` | 与建立基线前的 `main` 一致 |
| 业务前端 | `main` 前端源码 | 离线构建比较一致；产物差异仅为 Windows/Linux Vue scope 哈希 |
| 管理前端 | `main` 前端源码 | 离线构建产物逐文件一致 |

## 精确镜像

| 组件 | 本地回退标签 | 镜像 ID |
| --- | --- | --- |
| 后端 | `cargo-platform-backend:field-20260727-004852` | `sha256:b11269793433776517c156b27e12cb324cc0f8f18de93060bd936ef27274b55f` |
| 识别服务 | `cargo-platform-waybill-parser:field-20260727-004852` | `sha256:cfec30ba5af48a8a6b7ff4ad9f1854689dacca2c7b3a42370c744220c948c7b4` |
| 业务前端 | `cargo-platform-tenant-ui:field-20260727-004852` | `sha256:13f1345c0da7d72d82bef21d75b71d94693a4e6769b3b72d69ef57287f8b2458` |
| 管理前端 | `cargo-platform-admin-ui:field-20260727-004852` | `sha256:6e257681e7cdb98d8b13c5122080f9d929d185a0edd8f73c4b727749cc0c51f2` |

离线镜像包：

`C:\Users\ndlgx\Documents\Projects\GitHub\Ndlg\cargo-platform-field-baselines\field-20260727-004852\cargo-platform-images.tar`

SHA-256：

`61C3B28060B02B6EBF30726A993DF64681E2D081EFF3C5325BC790D20AC7E877`

离线包只包含程序镜像，不包含 `cargo-platform-data` 业务数据卷。

## 回退

回退会重建业务容器，只能在明确获准的维护窗口执行：

```powershell
docker load --input "C:\Users\ndlgx\Documents\Projects\GitHub\Ndlg\cargo-platform-field-baselines\field-20260727-004852\cargo-platform-images.tar"
docker compose -f docker-compose.yml -f ops/field-baselines/field-20260727-004852/docker-compose.override.yml config --quiet
docker compose -f docker-compose.yml -f ops/field-baselines/field-20260727-004852/docker-compose.override.yml up -d --no-build --no-deps waybill-parser backend tenant-ui platform-admin-ui
```

回退命令不会删除或重建 `cargo-platform-data`。
