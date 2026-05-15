# Sync Skills to ClawHub

同步 `skills/` 目录下所有 skill 到 ClawHub。

## 步骤

**Step 1** — 检查登录状态
```bash
clawhub whoami
```

**Step 2** — 读取每个 skill 的 `SKILL.md`，获取 `name` 和 `version`

**Step 3** — 逐个发布（用 `--slug` 确保发布到正确的 slug）

```bash
clawhub publish ./skills/ppx-parse --slug memect-ppx --version <version>
```

> 注意：必须用 `--slug <name>` 显式指定，否则 clawhub 会用旧 slug（ppx-parse）。

**Step 4** — 确认发布结果
```bash
clawhub inspect memect-ppx
```

## Slug 映射

| 目录 | slug |
|------|------|
| `skills/ppx-parse` | `memect-ppx` |
