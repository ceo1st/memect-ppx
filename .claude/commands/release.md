# PPX 发布助手

执行以下发布流程，每步完成后等待确认再继续。

## 发布前检查

1. 确认当前分支是 `dev/open`，工作区干净
2. 显示 `version.txt` 当前版本号
3. 显示自上次 tag 以来的提交列表：`git log $(git describe --tags --abbrev=0)..HEAD --oneline`

询问用户：**版本号是否需要更新？是否继续发布？**

## 发布步骤（逐步确认）

### Step 1 — 更新 CHANGELOG
```bash
bash scripts/release_changelog.sh
```
显示变更内容，询问是否提交。

### Step 2 — 提交 CHANGELOG（如有变更）
```bash
git add CHANGELOG.md
git commit -m "docs: update CHANGELOG for $(cat version.txt)"
```

### Step 3 — 打 Tag 并推送到 github
```bash
bash scripts/release_tag.sh --push
```
显示生成的 tag 名称，确认后执行。

### Step 4 — 推送到 github/main
```bash
bash scripts/release_github.sh
```
此脚本有交互确认，直接运行即可。

### Step 5 — 创建 GitHub PR（可选）
询问是否需要在 GitHub 上创建 PR 或 Release。
如需要，提示用户访问：`https://github.com/memect/mantis-shrimp/releases/new`

## 注意事项
- Tag 格式必须是 `memect-ppx-{version}-released`
- 发布前确保内网 origin 已同步
- scripts/ 目录不纳入版本控制，勿提交
