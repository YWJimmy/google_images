# Git 协作与同步指南

本文以本项目远端仓库 `git@github.com:YWJimmy/google_images.git` 为例。命令默认在项目根目录执行。

## 1. 首次初始化并推送

```powershell
# 进入项目目录
cd F:\8project\google_rank\google_images

# 初始化仓库并将默认分支设为 main
git init -b main

# 确认忽略规则是否生效
git status --short
git check-ignore -v config.yaml profile\google_profile logs output

# 检查将要提交的文件；确认其中不含账号、Cookie、Token、客户关键词或结果数据
git add .
git status --short
git diff --cached

# 创建首次提交
git commit -m "Initial commit"

# 关联 GitHub SSH 远端并推送
git remote add origin git@github.com:YWJimmy/google_images.git
git push -u origin main
```

如果 `origin` 已存在，用下面的命令修正地址：

```powershell
git remote set-url origin git@github.com:YWJimmy/google_images.git
git remote -v
```

## 2. 日常开发流程

```powershell
# 开始工作前同步远端；--ff-only 可避免意外生成合并提交
git switch main
git pull --ff-only origin main

# 建议每项工作使用独立分支
git switch -c feature/short-description

# 查看修改并运行测试
git status --short
.\.venv\Scripts\python.exe -m pytest

# 提交前逐项检查
git diff
git add <文件路径>
git diff --cached
git commit -m "feat: 简短中文摘要"

# 推送工作分支
git push -u origin feature/short-description
```

合并完成后更新并清理本地分支：

```powershell
git switch main
git pull --ff-only origin main
git branch -d feature/short-description
```

## 3. 隐私与密钥检查

以下内容只应保留在本机：`config.yaml`、`.env*`、浏览器 `profile/`、`private/`、`google_state.json`、`*.storage-state.json`、日志、运行输出、SQLite 数据库和私有关键词表。公开配置应写入 `config.example.yaml`，不要把真实凭据填入示例文件。

```powershell
# 查看忽略文件及其命中的规则
git status --ignored --short
git check-ignore -v <文件路径>

# 提交前搜索常见敏感字段
rg -n -i --hidden -g '!/.git/**' -g '!/.venv/**' `
  '(api[_-]?key|access[_-]?token|secret|password|authorization|cookie|session)'

# 检查最终将被提交的文件清单和内容
git diff --cached --name-only
git diff --cached
```

若敏感文件尚未提交、但已被 `git add` 暂存：

```powershell
git restore --staged <文件路径>
```

若文件已经提交或推送，单纯加入 `.gitignore` 不会删除历史记录。应立即轮换泄露的密钥，再使用 `git filter-repo` 清理历史；清理共享历史和强制推送前须先与协作者确认。

## 4. 常用诊断命令

```powershell
git status --short --branch
git log --oneline --decorate --graph --all -20
git remote -v
git branch -vv
git fetch --prune origin
```

SSH 连接异常时可检查：

```powershell
ssh -T git@github.com
```

## 5. 提交信息规范

每次提交使用 Conventional Commits 类型和中文摘要，格式如下：

```text
<type>: <中文摘要>
```

示例：

```powershell
git commit -m "feat: 新增浏览器环境诊断模式"
git commit -m "fix: 单独处理 Google 同意页面"
git commit -m "docs: 补充诊断流程说明"
```
