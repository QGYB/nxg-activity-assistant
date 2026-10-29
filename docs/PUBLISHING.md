# 源码发布

本项目发布源码 ZIP，不分发本机虚拟环境、登录资料或运行数据库。正式仓库预定为 `QGYB/nxg-activity-assistant`。

## 首次上传

此目录已准备为独立、干净的 Git 仓库。完成本地检查和密钥扫描后，在 GitHub 创建同名空仓库，再配置远程并推送；不要上传桌面实际运行目录或上层工作区。

```powershell
git remote add origin https://github.com/QGYB/nxg-activity-assistant.git
git push -u origin main
```

在仓库设置中启用 secret scanning、push protection 和 private vulnerability reporting；主分支要求 PR 与 Windows `test` 检查，禁止强推与删除。单人维护可不要求额外他人批准。Dependabot 配置已包括周更分组和 7 天冷却。

## 创建版本

1. 新建分支更新 `pyproject.toml` 版本和 `CHANGELOG.md`，运行 `uv lock --offline`，通过 PR 合并。
2. 确认主分支 CI 通过；完整历史密钥扫描通过后打标签：

```powershell
git tag v0.1.0
git push origin v0.1.0
```

标签工作流先运行 Windows 检查，再生成源码包、逐文件 SHA-256 清单及校验文件并创建 Release。发布任务单独具备写权限，其余检查只读。官方 Actions 固定完整提交 SHA。

本机也可在干净的已提交仓库中生成相同源码包：

```powershell
uv run --locked python tools/build_source_release.py --output-dir dist
```

构建器只归档当前 Git 提交，检查许可证和版本，拒绝会话、数据库、缓存、私有配置、符号链接及不在公开目录许可范围内的文件。输出包括源码 ZIP、清单 JSON 和 SHA-256 文件。源码 ZIP 可在没有 Git 历史的环境中解压、锁定安装并运行；重新构建版本包则需要 Git。

发布目录与桌面实际运行目录相互独立。后续开发应在仓库中完成、测试后同步必要源码；正常重开助手时保留实际账号的校区、排除记录及原运行状态。
