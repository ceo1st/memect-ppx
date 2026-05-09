# PPX 发布助手

## 发布前检查

1. 确认当前在 `github-main-clean` 分支，工作区干净
2. 显示当前版本：`cat version.txt`
3. 显示自上次 tag 以来的提交：`git log $(git describe --tags --abbrev=0 2>/dev/null)..HEAD --oneline`

询问用户：**本次是否需要打 tag 正式发版？还是只推送分支？**

---

## 场景 A：仅推送（不打 tag）

```bash
bash scripts/release_github.sh
```

推送 `github-main-clean` → `github/open/main-clean`，自动过滤 `.claude/`。

---

## 场景 B：正式发版（打 tag + 推送）

**Step 1** — 确认 `version.txt` 版本号是否需要更新

**Step 2** — 更新 CHANGELOG 并提交

```bash
bash scripts/release_changelog.sh
git add CHANGELOG.md && git commit -m "docs: update CHANGELOG for $(cat version.txt)"
```

**Step 3** — 打 tag 并推送

```bash
bash scripts/release_tag.sh --push
```

**Step 4** — 推送分支到 github

```bash
bash scripts/release_github.sh
```

**Step 5** — 提示用户去 GitHub 开 PR：`open/main-clean` → `main`
访问：`https://github.com/memect/memect-ppx/compare/open/main-clean`

---

## 注意
- Tag 格式：`memect-ppx-{version}-released`
- `.claude/` 和 `scripts/` 不会推送到 github
