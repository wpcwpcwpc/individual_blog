---
title: "Git 集成与版本控制"
summary: "Git 是现代软件开发的核心基础设施。Claude Code 对 Git 的集成远超简单的命令调用——它实现了一套零进程 Git 状态读取系统，直接解析 .git 目录内的文件来获取分支、HEAD、远程 URL 等信息，"
publishedAt: 2026-07-29
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 13
seriesGroup: 安全与可观测性
source: "13-Git集成与版本控制.md"
sourceSha256: 0319842a80e7
---
> **系列**: Claude Code CLI 深度技术拆解 (13/24)  
> **主题**: 零进程 Git 状态读取、文件系统监视器、差异分析与提交归因  
> **依赖**: 第6篇(工具系统)、第9篇(权限系统)、第12篇(文件系统)

## 概述

Git 是现代软件开发的核心基础设施。Claude Code 对 Git 的集成远超简单的命令调用——它实现了一套**零进程 Git 状态读取系统**，直接解析 `.git` 目录内的文件来获取分支、HEAD、远程 URL 等信息，避免了频繁 spawn `git` 进程带来的性能开销。

本篇将深入分析 Claude Code 的 Git 集成架构，涵盖：
1. 文件系统级 Git 状态读取
2. GitFileWatcher 实时监听系统
3. Worktree 与 Submodule 支持
4. 差异分析与 PR-like Diff
5. 提交归因与贡献追踪
6. 安全验证机制

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Git Integration Layer                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────┐    ┌──────────────────┐    ┌──────────────────┐   │
│  │   git.ts        │    │  gitFilesystem   │    │  gitDiff.ts      │   │
│  │  (High-level)   │────│  (Zero-process)  │    │  (Diff Engine)   │   │
│  └─────────────────┘    └──────────────────┘    └──────────────────┘   │
│          │                      │                        │              │
│          │                      │                        │              │
│  ┌───────▼─────────┐    ┌──────▼──────────┐    ┌───────▼────────┐     │
│  │ detectRepository│    │ GitFileWatcher  │    │ commitAttrib   │     │
│  │  (URL parsing)  │    │ (fs.watchFile)  │    │ (Contribution) │     │
│  └─────────────────┘    └─────────────────┘    └────────────────┘     │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│  Dependencies:                                                          │
│  • fs/promises (readFile, stat, readdir)                               │
│  • execFileNoThrow (fallback to git binary)                            │
│  • memoizeWithLRU (caching)                                            │
└─────────────────────────────────────────────────────────────────────────┘
```

### 核心设计原则

| 原则 | 实现方式 |
|------|----------|
| **零进程读取** | 直接解析 `.git/HEAD`、`.git/config`、`packed-refs` |
| **实时监听** | `fs.watchFile` 监控关键 Git 文件变化 |
| **LRU 缓存** | 避免重复路径遍历和文件读取 |
| **安全验证** | 验证 ref 名称、SHA 格式，防止注入攻击 |
| **Worktree 感知** | 正确处理 `commondir` 和共享 refs |

---

## 2. Git 根目录发现

### 2.1 向上遍历算法

```typescript
// restored-src/src/utils/git.ts

const GIT_ROOT_NOT_FOUND = Symbol('git-root-not-found')

const findGitRootImpl = memoizeWithLRU(
  (startPath: string): string | typeof GIT_ROOT_NOT_FOUND => {
    const startTime = Date.now()
    let current = resolve(startPath)
    const root = current.substring(0, current.indexOf(sep) + 1) || sep
    let statCount = 0

    while (current !== root) {
      try {
        const gitPath = join(current, '.git')
        statCount++
        const stat = statSync(gitPath)
        
        // .git 可以是目录（常规仓库）或文件（worktree/submodule）
        if (stat.isDirectory() || stat.isFile()) {
          logForDiagnosticsNoPII('info', 'find_git_root_completed', {
            duration_ms: Date.now() - startTime,
            stat_count: statCount,
            found: true,
          })
          return current.normalize('NFC')
        }
      } catch {
        // .git 不存在，继续向上
      }
      
      const parent = dirname(current)
      if (parent === current) break
      current = parent
    }

    return GIT_ROOT_NOT_FOUND
  },
  path => path,  // 缓存键
  50,            // LRU 容量
)
```

### 关键设计点

1. **LRU 缓存 (50 条目)**: `gitDiff` 会频繁调用 `findGitRoot(dirname(file))`，编辑多个目录的文件会产生大量条目
2. **NFC 规范化**: 处理 macOS 的 Unicode 规范化差异
3. **stat 计数**: 用于性能诊断，跟踪遍历深度

### 2.2 Canonical Root（规范根）解析

Worktree 的 `.git` 是一个指向主仓库的文件，而非目录：

```typescript
// restored-src/src/utils/git.ts

const resolveCanonicalRoot = memoizeWithLRU(
  (gitRoot: string): string => {
    try {
      // Worktree 中，.git 是包含 "gitdir: <path>" 的文件
      const gitContent = readFileSync(join(gitRoot, '.git'), 'utf-8').trim()
      if (!gitContent.startsWith('gitdir:')) {
        return gitRoot
      }
      
      const worktreeGitDir = resolve(
        gitRoot,
        gitContent.slice('gitdir:'.length).trim(),
      )
      
      // commondir 指向共享的 .git 目录
      const commonDir = resolve(
        worktreeGitDir,
        readFileSync(join(worktreeGitDir, 'commondir'), 'utf-8').trim(),
      )
      
      // 安全验证：防止恶意 commondir 指向任意路径
      // 验证 worktreeGitDir 是 commonDir/worktrees/ 的直接子目录
      if (resolve(dirname(worktreeGitDir)) !== join(commonDir, 'worktrees')) {
        return gitRoot
      }
      
      // 验证 gitdir 文件指向回 gitRoot/.git
      const backlink = realpathSync(
        readFileSync(join(worktreeGitDir, 'gitdir'), 'utf-8').trim(),
      )
      if (backlink !== join(realpathSync(gitRoot), '.git')) {
        return gitRoot
      }
      
      // Bare-repo worktree：common dir 不在工作目录内
      if (basename(commonDir) !== '.git') {
        return commonDir.normalize('NFC')
      }
      
      return dirname(commonDir).normalize('NFC')
    } catch {
      return gitRoot
    }
  },
  root => root,
  50,
)
```

### 安全防护详解

```
攻击场景：恶意仓库的 .git 文件可以指向任意 commondir

防护措施：
1. worktreeGitDir 必须是 commonDir/worktrees/ 的子目录
2. gitdir 文件必须指向回当前 gitRoot/.git
3. 两个验证缺一不可
```

---

## 3. 零进程 Git 状态读取

### 3.1 HEAD 解析

```typescript
// restored-src/src/utils/git/gitFilesystem.ts

/**
 * HEAD 格式（来自 git 源码 refs/files-backend.c）：
 *   - `ref: refs/heads/<branch>\n`  — 在分支上
 *   - `ref: <other-ref>\n`          — 特殊 symref（如 bisect 时）
 *   - `<hex-sha>\n`                 — detached HEAD（如 rebase 时）
 */
export async function readGitHead(
  gitDir: string,
): Promise<
  { type: 'branch'; name: string } | { type: 'detached'; sha: string } | null
> {
  try {
    const content = (await readFile(join(gitDir, 'HEAD'), 'utf-8')).trim()
    
    if (content.startsWith('ref:')) {
      const ref = content.slice('ref:'.length).trim()
      
      if (ref.startsWith('refs/heads/')) {
        const name = ref.slice('refs/heads/'.length)
        // 拒绝路径遍历和参数注入
        if (!isSafeRefName(name)) {
          return null
        }
        return { type: 'branch', name }
      }
      
      // 非本地分支的 symref — 解析为 SHA
      if (!isSafeRefName(ref)) {
        return null
      }
      const sha = await resolveRef(gitDir, ref)
      return sha ? { type: 'detached', sha } : { type: 'detached', sha: '' }
    }
    
    // 裸 SHA（detached HEAD）— 验证格式
    if (!isValidGitSha(content)) {
      return null
    }
    return { type: 'detached', sha: content }
  } catch {
    return null
  }
}
```

### 3.2 Ref 解析（Loose + Packed）

```typescript
// restored-src/src/utils/git/gitFilesystem.ts

/**
 * 解析 git ref（如 refs/heads/main）到 commit SHA
 * 
 * 检查顺序：
 * 1. Loose ref 文件（refs/heads/<branch>）
 * 2. packed-refs 文件
 * 
 * packed-refs 格式（来自 packed-backend.c）：
 *   - 头部：`# pack-refs with: <traits>\n`
 *   - 条目：`<40-hex-sha> <refname>\n`
 *   - Peeled：`^<40-hex-sha>\n`（跟在 annotated tag 后）
 */
export async function resolveRef(
  gitDir: string,
  ref: string,
): Promise<string | null> {
  const result = await resolveRefInDir(gitDir, ref)
  if (result) {
    return result
  }

  // 对于 worktree：尝试共享 refs 所在的 common gitdir
  const commonDir = await getCommonDir(gitDir)
  if (commonDir && commonDir !== gitDir) {
    return resolveRefInDir(commonDir, ref)
  }

  return null
}

async function resolveRefInDir(
  dir: string,
  ref: string,
): Promise<string | null> {
  // 尝试 loose ref 文件
  try {
    const content = (await readFile(join(dir, ref), 'utf-8')).trim()
    
    if (content.startsWith('ref:')) {
      // Symref — 递归解析
      const target = content.slice('ref:'.length).trim()
      if (!isSafeRefName(target)) {
        return null
      }
      return resolveRef(dir, target)
    }
    
    // Loose ref 应该是裸 SHA
    if (!isValidGitSha(content)) {
      return null
    }
    return content
  } catch {
    // Loose ref 不存在，尝试 packed-refs
  }

  // 解析 packed-refs
  try {
    const packed = await readFile(join(dir, 'packed-refs'), 'utf-8')
    for (const line of packed.split('\n')) {
      if (line.startsWith('#') || line.startsWith('^')) {
        continue
      }
      const spaceIdx = line.indexOf(' ')
      if (spaceIdx === -1) {
        continue
      }
      if (line.slice(spaceIdx + 1) === ref) {
        const sha = line.slice(0, spaceIdx)
        return isValidGitSha(sha) ? sha : null
      }
    }
  } catch {
    // 无 packed-refs
  }

  return null
}
```

### 3.3 Ref 名称安全验证

```typescript
// restored-src/src/utils/git/gitFilesystem.ts

/**
 * 验证从 .git/ 读取的 ref/branch 名称是安全的
 * 
 * 允许列表：ASCII 字母数字、/、.、_、+、-、@
 * 
 * 防护对象：
 * - 路径遍历（..）
 * - 参数注入（以 - 开头）
 * - Shell 元字符（反引号、$、;、|、&、<、>、空格等）
 */
export function isSafeRefName(name: string): boolean {
  if (!name || name.startsWith('-') || name.startsWith('/')) {
    return false
  }
  if (name.includes('..')) {
    return false
  }
  // 拒绝单点和空路径组件
  if (name.split('/').some(c => c === '.' || c === '')) {
    return false
  }
  // 仅允许列表字符
  if (!/^[a-zA-Z0-9/._+@-]+$/.test(name)) {
    return false
  }
  return true
}

/**
 * 验证字符串是 git SHA：40 hex（SHA-1）或 64 hex（SHA-256）
 */
export function isValidGitSha(s: string): boolean {
  return /^[0-9a-f]{40}$/.test(s) || /^[0-9a-f]{64}$/.test(s)
}
```

---

## 4. GitFileWatcher 实时监听系统

### 4.1 架构设计

```typescript
// restored-src/src/utils/git/gitFilesystem.ts

const WATCH_INTERVAL_MS = process.env.NODE_ENV === 'test' ? 10 : 1000

class GitFileWatcher {
  private gitDir: string | null = null
  private commonDir: string | null = null
  private initialized = false
  private initPromise: Promise<void> | null = null
  private watchedPaths: string[] = []
  private branchRefPath: string | null = null
  private cache = new Map<string, CacheEntry<unknown>>()

  async ensureStarted(): Promise<void> {
    if (this.initialized) return
    if (this.initPromise) return this.initPromise
    this.initPromise = this.start()
    return this.initPromise
  }

  private async start(): Promise<void> {
    this.gitDir = await resolveGitDir()
    this.initialized = true
    if (!this.gitDir) return

    // Worktree 的分支 refs 和主 config 在 commonDir
    this.commonDir = await getCommonDir(this.gitDir)

    // 监听 .git/HEAD 和 .git/config
    this.watchPath(join(this.gitDir, 'HEAD'), () => {
      void this.onHeadChanged()
    })
    
    // Config（remote URLs）在 worktree 中位于 commonDir
    this.watchPath(join(this.commonDir ?? this.gitDir, 'config'), () => {
      this.invalidate()
    })

    // 监听当前分支的 ref 文件
    await this.watchCurrentBranchRef()

    registerCleanup(async () => {
      this.stopWatching()
    })
  }
  
  // ...
}
```

### 4.2 分支切换处理

```typescript
private async watchCurrentBranchRef(): Promise<void> {
  if (!this.gitDir) return

  const head = await readGitHead(this.gitDir)
  // 分支 refs 在 worktree 中位于 commonDir
  const refsDir = this.commonDir ?? this.gitDir
  const refPath =
    head?.type === 'branch' ? join(refsDir, 'refs', 'heads', head.name) : null

  // 已经在监听此 ref
  if (refPath === this.branchRefPath) return

  // 停止监听旧分支 ref
  // 适用于：branch→branch 和 branch→detached（checkout --detach, rebase, bisect）
  if (this.branchRefPath) {
    unwatchFile(this.branchRefPath)
    this.watchedPaths = this.watchedPaths.filter(
      p => p !== this.branchRefPath,
    )
  }

  this.branchRefPath = refPath
  if (!refPath) return

  // Ref 文件可能还不存在（新分支首次 commit 前）
  // watchFile 可以监听不存在的文件 — 文件出现时会触发
  this.watchPath(refPath, () => {
    this.invalidate()
  })
}

private async onHeadChanged(): Promise<void> {
  // HEAD 变化 — 可能是分支切换或 detach
  // 先 invalidate（标记脏），再等滚动空闲后更新 watcher
  this.invalidate()
  await waitForScrollIdle()
  await this.watchCurrentBranchRef()
}
```

### 4.3 缓存失效与竞态处理

```typescript
/**
 * 按 key 获取缓存值。首次调用时计算并缓存。
 * 后续调用返回缓存值，直到监听的文件变化将 dirty 标记为 true。
 * 
 * 竞态处理：dirty 在异步 compute 开始前清除。
 * 如果文件在 compute 期间变化，invalidate() 重新设置 dirty，
 * 下次 get() 会再次读取，而非返回过时值。
 */
async get<T>(key: string, compute: () => Promise<T>): Promise<T> {
  await this.ensureStarted()
  const existing = this.cache.get(key)
  
  if (existing && !existing.dirty) {
    return existing.value as T
  }
  
  // 在 compute 前清除 dirty
  if (existing) {
    existing.dirty = false
  }
  
  const value = await compute()
  
  // 仅在 compute 期间无新失效时更新缓存
  const entry = this.cache.get(key)
  if (entry && !entry.dirty) {
    entry.value = value
  }
  if (!entry) {
    this.cache.set(key, { value, dirty: false, compute })
  }
  
  return value
}
```

### 监听文件列表

| 文件 | 触发场景 | 缓存影响 |
|------|----------|----------|
| `.git/HEAD` | 分支切换、detach | 全部失效 + 更新分支监听 |
| `.git/config` | remote 变更 | 全部失效 |
| `.git/refs/heads/<branch>` | 新 commit | 全部失效 |

---

## 5. Git Config 解析器

### 5.1 轻量级 Config 解析

```typescript
// restored-src/src/utils/git/gitConfigParser.ts

/**
 * 解析 .git/config 中的单个值
 * 
 * Git config 格式（已对照 git 源码 config.c 验证）：
 *   - Section 名：大小写不敏感，字母数字 + 连字符
 *   - Subsection（引号内）：大小写敏感，支持反斜杠转义
 *   - Key 名：大小写不敏感
 *   - Value：可选引号，支持行内注释（# 或 ;）
 */
export async function parseGitConfigValue(
  gitDir: string,
  section: string,
  subsection: string | null,
  key: string,
): Promise<string | null> {
  try {
    const config = await readFile(join(gitDir, 'config'), 'utf-8')
    return parseConfigString(config, section, subsection, key)
  } catch {
    return null
  }
}
```

### 5.2 Value 解析（转义与注释）

```typescript
function parseValue(line: string, start: number): string {
  let result = ''
  let inQuote = false
  let i = start

  while (i < line.length) {
    const ch = line[i]!

    // 引号外的行内注释结束 value
    if (!inQuote && (ch === '#' || ch === ';')) {
      break
    }

    if (ch === '"') {
      inQuote = !inQuote
      i++
      continue
    }

    if (ch === '\\' && i + 1 < line.length) {
      const next = line[i + 1]!
      if (inQuote) {
        // 引号内识别转义序列
        switch (next) {
          case 'n': result += '\n'; break
          case 't': result += '\t'; break
          case '"': result += '"'; break
          case '\\': result += '\\'; break
          default: result += next; break
        }
        i += 2
        continue
      }
    }

    result += ch
    i++
  }

  return inQuote ? result : trimTrailingWhitespace(result)
}
```

---

## 6. 仓库检测与 URL 解析

### 6.1 Remote URL 规范化

```typescript
// restored-src/src/utils/git.ts

/**
 * 规范化 git remote URL 用于哈希计算
 * 
 * 转换规则：
 * - git@github.com:owner/repo.git   → github.com/owner/repo
 * - https://github.com/owner/repo   → github.com/owner/repo
 * - ssh://git@github.com/owner/repo → github.com/owner/repo
 * 
 * 特殊处理 CCR 代理 URL：
 * - http://...@127.0.0.1:PORT/git/owner/repo       → github.com/owner/repo
 * - http://...@127.0.0.1:PORT/git/ghe.host/o/r     → ghe.host/o/r
 */
export function normalizeGitRemoteUrl(url: string): string | null {
  const trimmed = url.trim()
  if (!trimmed) return null

  // SSH 格式：git@host:owner/repo.git
  const sshMatch = trimmed.match(/^git@([^:]+):(.+?)(?:\.git)?$/)
  if (sshMatch && sshMatch[1] && sshMatch[2]) {
    return `${sshMatch[1]}/${sshMatch[2]}`.toLowerCase()
  }

  // URL 格式
  const urlMatch = trimmed.match(
    /^(?:https?|ssh):\/\/(?:[^@]+@)?([^/]+)\/(.+?)(?:\.git)?$/,
  )
  if (urlMatch && urlMatch[1] && urlMatch[2]) {
    const host = urlMatch[1]
    const path = urlMatch[2]

    // CCR 代理 URL 处理
    if (isLocalHost(host) && path.startsWith('git/')) {
      const proxyPath = path.slice(4)
      const segments = proxyPath.split('/')
      // 3+ segments 且首段含点 → host/owner/repo (GHE)
      if (segments.length >= 3 && segments[0]!.includes('.')) {
        return proxyPath.toLowerCase()
      }
      // 2 segments → github.com 默认
      return `github.com/${proxyPath}`.toLowerCase()
    }

    return `${host}/${path}`.toLowerCase()
  }

  return null
}
```

### 6.2 仓库哈希（隐私保护）

```typescript
/**
 * 返回规范化 remote URL 的 SHA256 哈希（前 16 位）
 * 
 * 用途：提供全局唯一的仓库标识符
 * - SSH 和 HTTPS clone 产生相同哈希
 * - 不在日志中暴露实际仓库名
 */
export async function getRepoRemoteHash(): Promise<string | null> {
  const remoteUrl = await getRemoteUrl()
  if (!remoteUrl) return null

  const normalized = normalizeGitRemoteUrl(remoteUrl)
  if (!normalized) return null

  const hash = createHash('sha256').update(normalized).digest('hex')
  return hash.substring(0, 16)
}
```

### 6.3 Git Remote URL 解析

```typescript
// restored-src/src/utils/detectRepository.ts

/**
 * 解析 git remote URL 为 host、owner、name 组件
 * 
 * 支持格式：
 *   https://host/owner/repo.git
 *   git@host:owner/repo.git
 *   ssh://git@host/owner/repo.git
 *   git://host/owner/repo.git
 */
export function parseGitRemote(input: string): ParsedRepository | null {
  const trimmed = input.trim()

  // SSH 格式
  const sshMatch = trimmed.match(/^git@([^:]+):([^/]+)\/([^/]+?)(?:\.git)?$/)
  if (sshMatch?.[1] && sshMatch[2] && sshMatch[3]) {
    if (!looksLikeRealHostname(sshMatch[1])) return null
    return {
      host: sshMatch[1],
      owner: sshMatch[2],
      name: sshMatch[3],
    }
  }

  // URL 格式
  const urlMatch = trimmed.match(
    /^(https?|ssh|git):\/\/(?:[^@]+@)?([^/:]+(?::\d+)?)\/([^/]+)\/([^/]+?)(?:\.git)?$/,
  )
  if (urlMatch) {
    const protocol = urlMatch[1]
    const hostWithPort = urlMatch[2]
    const hostWithoutPort = hostWithPort.split(':')[0] ?? ''
    if (!looksLikeRealHostname(hostWithoutPort)) return null
    
    // 仅 HTTPS 保留端口
    const host = protocol === 'https' || protocol === 'http'
      ? hostWithPort
      : hostWithoutPort
      
    return { host, owner: urlMatch[3], name: urlMatch[4] }
  }

  return null
}

/**
 * 检查 hostname 是否像真实域名（非 SSH 别名）
 * 
 * 要求最后一段（TLD）纯字母 — 真实 TLD 不含连字符或数字
 * 拒绝如 "github.com-work" 这样的 SSH 别名
 */
function looksLikeRealHostname(host: string): boolean {
  if (!host.includes('.')) return false
  const lastSegment = host.split('.').pop()
  if (!lastSegment) return false
  return /^[a-zA-Z]+$/.test(lastSegment)
}
```

---

## 7. Git Diff 分析引擎

### 7.1 差异获取策略

```typescript
// restored-src/src/utils/gitDiff.ts

const GIT_TIMEOUT_MS = 5000
const MAX_FILES = 50
const MAX_DIFF_SIZE_BYTES = 1_000_000  // 1 MB
const MAX_LINES_PER_FILE = 400         // GitHub 自动加载限制
const MAX_FILES_FOR_DETAILS = 500      // 超过则跳过每文件详情

/**
 * 获取 working tree 与 HEAD 的 diff 统计和 hunks
 * 
 * 在 merge/rebase/cherry-pick/revert 期间返回 null
 * （工作树包含非用户主动的传入变更）
 */
export async function fetchGitDiff(): Promise<GitDiffResult | null> {
  const isGit = await getIsGit()
  if (!isGit) return null

  // 跳过瞬态 git 状态
  if (await isInTransientGitState()) {
    return null
  }

  // 快速探测：用 --shortstat 获取总计，O(1) 内存
  const { stdout: shortstatOut, code } = await execFileNoThrow(
    gitExe(),
    ['--no-optional-locks', 'diff', 'HEAD', '--shortstat'],
    { timeout: GIT_TIMEOUT_MS, preserveOutputOnError: false },
  )

  if (code === 0) {
    const quickStats = parseShortstat(shortstatOut)
    if (quickStats && quickStats.filesCount > MAX_FILES_FOR_DETAILS) {
      // 文件太多 — 返回准确总计，跳过每文件详情
      return {
        stats: quickStats,
        perFileStats: new Map(),
        hunks: new Map(),
      }
    }
  }

  // 用 --numstat 获取每文件统计
  const { stdout: numstatOut, code: numstatCode } = await execFileNoThrow(
    gitExe(),
    ['--no-optional-locks', 'diff', 'HEAD', '--numstat'],
    { timeout: GIT_TIMEOUT_MS, preserveOutputOnError: false },
  )

  if (numstatCode !== 0) return null

  const { stats, perFileStats } = parseGitNumstat(numstatOut)

  // 包含 untracked 文件
  const remainingSlots = MAX_FILES - perFileStats.size
  if (remainingSlots > 0) {
    const untrackedStats = await fetchUntrackedFiles(remainingSlots)
    if (untrackedStats) {
      stats.filesCount += untrackedStats.size
      for (const [path, fileStats] of untrackedStats) {
        perFileStats.set(path, fileStats)
      }
    }
  }

  // 返回统计 — hunks 按需通过 fetchGitDiffHunks() 获取
  return { stats, perFileStats, hunks: new Map() }
}
```

### 7.2 瞬态 Git 状态检测

```typescript
/**
 * 检查是否在瞬态 git 状态（merge, rebase, cherry-pick, revert）
 * 
 * 使用 fs.access 检查瞬态 ref 文件，避免 spawn 进程
 */
async function isInTransientGitState(): Promise<boolean> {
  const gitDir = await getGitDir(getCwd())
  if (!gitDir) return false

  const transientFiles = [
    'MERGE_HEAD',
    'REBASE_HEAD',
    'CHERRY_PICK_HEAD',
    'REVERT_HEAD',
  ]

  const results = await Promise.all(
    transientFiles.map(file =>
      access(join(gitDir, file))
        .then(() => true)
        .catch(() => false),
    ),
  )
  return results.some(Boolean)
}
```

### 7.3 Unified Diff 解析

```typescript
/**
 * 解析 unified diff 输出为每文件 hunks
 * 
 * 限制：
 * - MAX_FILES: 达到后停止
 * - > 1MB 文件: 完全跳过
 * - ≤ 1MB 文件: 解析但限制为 MAX_LINES_PER_FILE 行
 */
export function parseGitDiff(
  stdout: string,
): Map<string, StructuredPatchHunk[]> {
  const result = new Map<string, StructuredPatchHunk[]>()
  if (!stdout.trim()) return result

  const fileDiffs = stdout.split(/^diff --git /m).filter(Boolean)

  for (const fileDiff of fileDiffs) {
    if (result.size >= MAX_FILES) break
    if (fileDiff.length > MAX_DIFF_SIZE_BYTES) continue

    const lines = fileDiff.split('\n')
    const headerMatch = lines[0]?.match(/^a\/(.+?) b\/(.+)$/)
    if (!headerMatch) continue
    
    const filePath = headerMatch[2] ?? headerMatch[1] ?? ''
    const fileHunks: StructuredPatchHunk[] = []
    let currentHunk: StructuredPatchHunk | null = null
    let lineCount = 0

    for (let i = 1; i < lines.length; i++) {
      const line = lines[i] ?? ''

      // Hunk 头：@@ -oldStart,oldLines +newStart,newLines @@
      const hunkMatch = line.match(
        /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/,
      )
      if (hunkMatch) {
        if (currentHunk) fileHunks.push(currentHunk)
        currentHunk = {
          oldStart: parseInt(hunkMatch[1] ?? '0', 10),
          oldLines: parseInt(hunkMatch[2] ?? '1', 10),
          newStart: parseInt(hunkMatch[3] ?? '0', 10),
          newLines: parseInt(hunkMatch[4] ?? '1', 10),
          lines: [],
        }
        continue
      }

      // 添加 diff 行到 hunk
      if (currentHunk && (line.startsWith('+') || line.startsWith('-') || 
          line.startsWith(' ') || line === '')) {
        if (lineCount >= MAX_LINES_PER_FILE) continue
        
        // 强制扁平字符串拷贝，打破 V8 sliced string 引用
        // split() 创建的行引用父字符串，这样会保持整个父串存活
        currentHunk.lines.push('' + line)
        lineCount++
      }
    }

    if (currentHunk) fileHunks.push(currentHunk)
    if (fileHunks.length > 0) result.set(filePath, fileHunks)
  }

  return result
}
```

### 7.4 单文件 PR-like Diff

```typescript
/**
 * 获取单文件相对于 merge base 的结构化 diff
 * 
 * 产生 PR-like diff，显示分支分叉以来的所有变更
 * 如果无法确定 merge base（如在默认分支上），回退到 HEAD
 */
export async function fetchSingleFileGitDiff(
  absoluteFilePath: string,
): Promise<ToolUseDiff | null> {
  const gitRoot = findGitRoot(dirname(absoluteFilePath))
  if (!gitRoot) return null

  const gitPath = relative(gitRoot, absoluteFilePath).split(sep).join('/')
  const repository = getCachedRepository()

  // 检查文件是否被 git 追踪
  const { code: lsFilesCode } = await execFileNoThrowWithCwd(
    gitExe(),
    ['--no-optional-locks', 'ls-files', '--error-unmatch', gitPath],
    { cwd: gitRoot, timeout: SINGLE_FILE_DIFF_TIMEOUT_MS },
  )

  if (lsFilesCode === 0) {
    // 文件被追踪 — diff against merge base
    const diffRef = await getDiffRef(gitRoot)
    const { stdout, code } = await execFileNoThrowWithCwd(
      gitExe(),
      ['--no-optional-locks', 'diff', diffRef, '--', gitPath],
      { cwd: gitRoot, timeout: SINGLE_FILE_DIFF_TIMEOUT_MS },
    )
    if (code !== 0 || !stdout) return null
    return {
      ...parseRawDiffToToolUseDiff(gitPath, stdout, 'modified'),
      repository,
    }
  }

  // 文件未追踪 — 生成合成 diff
  const syntheticDiff = await generateSyntheticDiff(gitPath, absoluteFilePath)
  return syntheticDiff ? { ...syntheticDiff, repository } : null
}

/**
 * 确定最佳 diff 参照 ref
 * 
 * 优先级：
 * 1. CLAUDE_CODE_BASE_REF 环境变量（CCR 托管容器设置）
 * 2. 与默认分支的 merge base
 * 3. HEAD（回退）
 */
async function getDiffRef(gitRoot: string): Promise<string> {
  const baseBranch = process.env.CLAUDE_CODE_BASE_REF || (await getDefaultBranch())
  const { stdout, code } = await execFileNoThrowWithCwd(
    gitExe(),
    ['--no-optional-locks', 'merge-base', 'HEAD', baseBranch],
    { cwd: gitRoot, timeout: SINGLE_FILE_DIFF_TIMEOUT_MS },
  )
  if (code === 0 && stdout.trim()) {
    return stdout.trim()
  }
  return 'HEAD'
}
```

---

## 8. Git 状态聚合

### 8.1 完整仓库状态

```typescript
// restored-src/src/utils/git.ts

export type GitRepoState = {
  commitHash: string
  branchName: string
  remoteUrl: string | null
  isHeadOnRemote: boolean
  isClean: boolean
  worktreeCount: number
}

export async function getGitState(): Promise<GitRepoState | null> {
  try {
    const [
      commitHash,
      branchName,
      remoteUrl,
      isHeadOnRemote,
      isClean,
      worktreeCount,
    ] = await Promise.all([
      getHead(),
      getBranch(),
      getRemoteUrl(),
      getIsHeadOnRemote(),
      getIsClean(),
      getWorktreeCount(),
    ])

    return {
      commitHash,
      branchName,
      remoteUrl,
      isHeadOnRemote,
      isClean,
      worktreeCount,
    }
  } catch (_) {
    return null  // 最佳努力
  }
}
```

### 8.2 Stash 操作

```typescript
/**
 * Stash 所有变更（包括 untracked 文件）到干净状态
 * 
 * 重要：先 stage untracked 文件再 stash，防止数据丢失
 */
export const stashToCleanState = async (message?: string): Promise<boolean> => {
  try {
    const stashMessage = message || 
      `Claude Code auto-stash - ${new Date().toISOString()}`

    // 检查是否有 untracked 文件
    const { untracked } = await getFileStatus()

    // 先添加 untracked 文件到 index
    if (untracked.length > 0) {
      const { code: addCode } = await execFileNoThrow(
        gitExe(),
        ['add', ...untracked],
        { preserveOutputOnError: false },
      )
      if (addCode !== 0) return false
    }

    // Stash 所有变更
    const { code } = await execFileNoThrow(
      gitExe(),
      ['stash', 'push', '--message', stashMessage],
      { preserveOutputOnError: false },
    )
    return code === 0
  } catch (_) {
    return false
  }
}
```

---

## 9. Git 操作追踪（指标与分析）

```typescript
// restored-src/src/tools/shared/gitOperationTracking.ts

/**
 * 检测 shell 命令输出中的 git 操作
 * 
 * 扫描命令和输出来检测：
 * - git commit（包括 --amend 和 cherry-pick）
 * - git push
 * - git merge / rebase
 * - gh pr create/edit/merge/comment/close/ready
 */
export function detectGitOperation(
  command: string,
  output: string,
): {
  commit?: { sha: string; kind: CommitKind }
  push?: { branch: string }
  branch?: { ref: string; action: BranchAction }
  pr?: { number: number; url?: string; action: PrAction }
} {
  const result: ReturnType<typeof detectGitOperation> = {}
  
  const isCherryPick = GIT_CHERRY_PICK_RE.test(command)
  if (GIT_COMMIT_RE.test(command) || isCherryPick) {
    const sha = parseGitCommitId(output)
    if (sha) {
      result.commit = {
        sha: sha.slice(0, 6),
        kind: isCherryPick
          ? 'cherry-picked'
          : /--amend\b/.test(command)
            ? 'amended'
            : 'committed',
      }
    }
  }
  
  if (GIT_PUSH_RE.test(command)) {
    const branch = parseGitPushBranch(output)
    if (branch) result.push = { branch }
  }
  
  if (GIT_MERGE_RE.test(command) && 
      /(Fast-forward|Merge made by)/.test(output)) {
    const ref = parseRefFromCommand(command, 'merge')
    if (ref) result.branch = { ref, action: 'merged' }
  }
  
  // ... PR 检测逻辑
  
  return result
}
```

### Commit ID 解析

```typescript
// git commit 输出：[branch abc1234] message
// 或 root commit：[branch (root-commit) abc1234] message
export function parseGitCommitId(stdout: string): string | undefined {
  const match = stdout.match(/\[[\w./-]+(?: \(root-commit\))? ([0-9a-f]+)\]/)
  return match?.[1]
}

// git push 输出解析分支名
// 格式："abc..def  branch -> branch" 或 "* [new branch] branch -> branch"
function parseGitPushBranch(output: string): string | undefined {
  const match = output.match(
    /^\s*[+\-*!= ]?\s*(?:\[new branch\]|\S+\.\.+\S+)\s+\S+\s*->\s*(\S+)/m,
  )
  return match?.[1]
}
```

---

## 10. 提交归因系统

### 10.1 归因状态

```typescript
// restored-src/src/utils/commitAttribution.ts

export type AttributionState = {
  // 按相对路径键控的文件状态
  fileStates: Map<string, FileAttributionState>
  // 用于净变更计算的会话基线
  sessionBaselines: Map<string, { contentHash: string; mtime: number }>
  // 编辑来源
  surface: string
  // 会话开始时的 HEAD SHA（检测外部提交）
  startingHeadSha: string | null
  // 会话总提示数（用于计算 steer count）
  promptCount: number
  promptCountAtLastCommit: number
  // 权限提示追踪
  permissionPromptCount: number
  permissionPromptCountAtLastCommit: number
  // ESC 按键追踪（用户取消权限提示）
  escapeCount: number
  escapeCountAtLastCommit: number
}
```

### 10.2 归因摘要

```typescript
export type AttributionSummary = {
  claudePercent: number   // Claude 贡献百分比
  claudeChars: number     // Claude 编写的字符数
  humanChars: number      // 人类编写的字符数
  surfaces: string[]      // 来源列表（cli, vscode 等）
}

export type FileAttribution = {
  claudeChars: number
  humanChars: number
  percent: number
  surface: string
}

export type AttributionData = {
  version: 1
  summary: AttributionSummary
  files: Record<string, FileAttribution>
  surfaceBreakdown: Record<string, { claudeChars: number; percent: number }>
  excludedGenerated: string[]  // 排除的生成文件
  sessions: string[]           // 参与的会话 ID
}
```

### 10.3 Surface Key 构建

```typescript
/**
 * 构建包含模型名的 surface key
 * 格式："surface/model"（如 "cli/claude-sonnet"）
 */
export function buildSurfaceKey(surface: string, model: ModelName): string {
  return `${surface}/${getCanonicalName(model)}`
}

/**
 * 获取当前客户端 surface
 */
export function getClientSurface(): string {
  return process.env.CLAUDE_CODE_ENTRYPOINT ?? 'cli'
}
```

### 10.4 内部模型名保护

```typescript
/**
 * 内部仓库允许列表（可使用内部模型名）
 */
const INTERNAL_MODEL_REPOS = [
  'github.com:anthropics/claude-cli-internal',
  'github.com/anthropics/claude-cli-internal',
  // ... 更多内部仓库
]

/**
 * 清理模型名为公开等价名
 * 将内部变体映射到公开名，基于模型系列
 */
export function sanitizeModelName(shortName: string): string {
  if (shortName.includes('opus-4-6')) return 'claude-opus-4-6'
  if (shortName.includes('opus-4-5')) return 'claude-opus-4-5'
  if (shortName.includes('sonnet-4-6')) return 'claude-sonnet-4-6'
  if (shortName.includes('sonnet-4-5')) return 'claude-sonnet-4-5'
  if (shortName.includes('sonnet-4')) return 'claude-sonnet-4'
  if (shortName.includes('haiku-4-5')) return 'claude-haiku-4-5'
  // 未知模型使用通用名
  return 'claude'
}

/**
 * 检查当前仓库是否在内部模型名允许列表中
 * 每进程只检查一次
 */
export const isInternalModelRepo = sequential(async (): Promise<boolean> => {
  if (repoClassCache !== null) {
    return repoClassCache === 'internal'
  }

  const cwd = getAttributionRepoRoot()
  const remoteUrl = await getRemoteUrlForDir(cwd)

  if (!remoteUrl) {
    repoClassCache = 'none'
    return false
  }
  const isInternal = INTERNAL_MODEL_REPOS.some(repo => remoteUrl.includes(repo))
  repoClassCache = isInternal ? 'internal' : 'external'
  return isInternal
})
```

---

## 11. 预留 Git 状态（Issue 提交）

```typescript
// restored-src/src/utils/git.ts

/**
 * 用于 issue 提交的预留 git 状态
 * 使用 remote base（如 origin/main）而非本地 commit
 * 因为 remote base 很少被 force-push
 */
export type PreservedGitState = {
  /** 与 remote 分支的 merge-base SHA */
  remote_base_sha: string | null
  /** 使用的 remote 分支（如 "origin/main"） */
  remote_base: string | null
  /** 从 merge-base 到当前状态的 patch（包含未提交变更） */
  patch: string
  /** 带内容的 untracked 文件 */
  untracked_files: Array<{ path: string; content: string }>
  /** merge-base 与 HEAD 之间提交的 git format-patch 输出 */
  format_patch: string | null
  /** 当前 HEAD SHA */
  head_sha: string | null
  /** 当前分支名 */
  branch_name: string | null
}

// Untracked 文件捕获的大小限制
const MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024  // 500MB 每文件
const MAX_TOTAL_SIZE_BYTES = 5 * 1024 * 1024 * 1024  // 5GB 总计
const MAX_FILE_COUNT = 20000
```

---

## 12. 性能优化总结

### 12.1 零进程 vs 进程 Spawn 比较

| 操作 | 零进程方式 | 进程 Spawn | 性能提升 |
|------|-----------|-----------|---------|
| 读取 HEAD | `readFile('.git/HEAD')` | `git rev-parse HEAD` | ~100x |
| 获取分支名 | 解析 HEAD 文件 | `git branch --show-current` | ~100x |
| 获取 remote URL | 解析 config 文件 | `git remote get-url origin` | ~50x |
| 检查 shallow | `stat('.git/shallow')` | `git rev-parse --is-shallow-repository` | ~100x |

### 12.2 缓存层级

```
┌─────────────────────────────────────────────────────────────────┐
│                         缓存架构                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Layer 1: memoizeWithLRU                                       │
│  ├── findGitRoot (50 entries)                                  │
│  ├── resolveCanonicalRoot (50 entries)                         │
│  └── gitExe (memoize once)                                     │
│                                                                 │
│  Layer 2: GitFileWatcher Cache                                 │
│  ├── branch (dirty on HEAD change)                             │
│  ├── head (dirty on HEAD/ref change)                           │
│  ├── remoteUrl (dirty on config change)                        │
│  └── defaultBranch (dirty on config change)                    │
│                                                                 │
│  Layer 3: Repository Detection Cache                           │
│  └── repositoryWithHostCache (per cwd)                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 12.3 V8 内存优化

```typescript
// Diff 解析中的字符串拷贝优化
// 问题：V8 的 sliced string 保持父字符串存活
// split() 创建的子串引用原始大字符串

// ❌ 错误：保持整个 fileDiff 存活
currentHunk.lines.push(line)

// ✅ 正确：强制扁平字符串拷贝
currentHunk.lines.push('' + line)
```

---

## 13. 安全考虑总结

### 13.1 攻击向量与防护

| 攻击向量 | 风险 | 防护措施 |
|---------|------|---------|
| 恶意 .git/HEAD | Shell 注入 | `isSafeRefName()` 验证 |
| 恶意 ref 文件 | 路径遍历 | 拒绝 `..`，验证 SHA 格式 |
| 恶意 commondir | 信任绕过 | 双向链接验证 |
| SSH 别名混淆 | 错误仓库识别 | TLD 验证 |

### 13.2 Worktree 安全验证

```
验证流程：
1. .git 文件 → gitdir: <worktreeGitDir>
2. worktreeGitDir/commondir → <commonDir>
3. 验证：dirname(worktreeGitDir) === commonDir/worktrees/
4. worktreeGitDir/gitdir → 验证指向回 gitRoot/.git
5. 两个验证都通过才信任 commonDir
```

---

## 14. 总结

Claude Code 的 Git 集成展示了多层精细化工程：

1. **零进程架构**: 直接解析 `.git` 文件，避免进程 spawn 开销
2. **实时监听**: `fs.watchFile` 配合脏标记实现高效缓存失效
3. **Worktree 感知**: 正确处理 commondir 和共享 refs
4. **安全优先**: 多层验证防止恶意仓库攻击
5. **内存优化**: V8 字符串拷贝技巧防止内存泄漏
6. **归因追踪**: 完整的贡献归因系统用于 commit trailer

这套系统使 Claude Code 能够高效地感知项目的版本控制状态，同时保持安全和性能的平衡。

---

**下一篇预告**: 第14篇：内存与上下文管理 - 深入分析 CLAUDE.md 解析、上下文压缩策略和记忆系统的实现。
