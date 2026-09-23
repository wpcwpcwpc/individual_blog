---
title: "核心工具实现"
summary: "第6篇建立了工具系统的架构基础（Tool 接口、权限模型、并发控制）。本篇深入分析五个最重要的核心工具实现："
publishedAt: 2026-06-19
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 7
seriesGroup: 工具与能力扩展
source: "07-核心工具实现.md"
sourceSha256: 97b4a6349758
---
> **系列**：Claude Code CLI 深度解构  
> **前置阅读**：06-工具系统架构.md  
> **范围**：BashTool、FileEditTool、FileReadTool、GrepTool、AgentTool 的核心实现细节

---

## 1. 概述

第6篇建立了工具系统的架构基础（`Tool` 接口、权限模型、并发控制）。本篇深入分析五个最重要的核心工具实现：

| 工具 | 职责 | 并发安全 | 关键复杂度 |
|------|------|----------|------------|
| **BashTool** | 执行 Shell 命令 | 条件安全 | 安全校验、沙箱、后台任务 |
| **FileEditTool** | 精确文本替换 | 否 | 冲突检测、原子写入 |
| **FileReadTool** | 读取文件内容 | 是 | 多格式支持、Token限制 |
| **GrepTool** | 正则搜索 | 是 | ripgrep 集成、分页 |
| **AgentTool** | 启动子代理 | 否 | 工作树隔离、远程执行 |

---

## 2. BashTool：Shell 命令执行器

### 2.1 输入/输出 Schema

```typescript
// 输入参数
const fullInputSchema = z.strictObject({
  command: z.string().describe('The command to execute'),
  timeout: semanticNumber(z.number().optional())
    .describe(`Optional timeout in milliseconds (max ${getMaxTimeoutMs()})`),
  description: z.string().optional()
    .describe('Clear, concise description of what this command does'),
  run_in_background: semanticBoolean(z.boolean().optional())
    .describe('Set to true to run this command in the background'),
  dangerouslyDisableSandbox: semanticBoolean(z.boolean().optional())
    .describe('Override sandbox mode'),
  // 内部字段，不暴露给模型
  _simulatedSedEdit: z.object({
    filePath: z.string(),
    newContent: z.string()
  }).optional()
});

// 输出结构
const outputSchema = z.object({
  stdout: z.string(),
  stderr: z.string(),
  rawOutputPath: z.string().optional(),        // 大输出时的文件路径
  interrupted: z.boolean(),
  isImage: z.boolean().optional(),             // stdout 包含图像数据
  backgroundTaskId: z.string().optional(),     // 后台任务 ID
  backgroundedByUser: z.boolean().optional(),  // 用户手动后台化
  assistantAutoBackgrounded: z.boolean().optional(), // 自动后台化
  returnCodeInterpretation: z.string().optional(),   // 非错误退出码解释
  noOutputExpected: z.boolean().optional(),    // 静默命令标识
});
```

### 2.2 命令分类系统

BashTool 实现了精细的命令分类，用于 UI 显示优化和安全决策：

```typescript
// 搜索类命令 - 可折叠显示
const BASH_SEARCH_COMMANDS = new Set([
  'find', 'grep', 'rg', 'ag', 'ack', 'locate', 'which', 'whereis'
]);

// 读取类命令 - 可折叠显示
const BASH_READ_COMMANDS = new Set([
  'cat', 'head', 'tail', 'less', 'more',
  'wc', 'stat', 'file', 'strings',
  'jq', 'awk', 'cut', 'sort', 'uniq', 'tr'
]);

// 目录列表命令 - 独立分类避免误导
const BASH_LIST_COMMANDS = new Set(['ls', 'tree', 'du']);

// 语义中性命令 - 不改变管道的读/写性质
const BASH_SEMANTIC_NEUTRAL_COMMANDS = new Set([
  'echo', 'printf', 'true', 'false', ':'
]);

// 静默命令 - 成功时无输出
const BASH_SILENT_COMMANDS = new Set([
  'mv', 'cp', 'rm', 'mkdir', 'rmdir', 'chmod', 
  'chown', 'chgrp', 'touch', 'ln', 'cd', 
  'export', 'unset', 'wait'
]);
```

#### 管道命令分析

```typescript
export function isSearchOrReadBashCommand(command: string): {
  isSearch: boolean;
  isRead: boolean;
  isList: boolean;
} {
  // 解析命令运算符
  const partsWithOperators = splitCommandWithOperators(command);
  
  let hasSearch = false;
  let hasRead = false;
  let hasList = false;
  let hasNonNeutralCommand = false;
  
  for (const part of partsWithOperators) {
    // 跳过重定向目标
    if (skipNextAsRedirectTarget) {
      skipNextAsRedirectTarget = false;
      continue;
    }
    
    // 处理重定向运算符
    if (part === '>' || part === '>>' || part === '>&') {
      skipNextAsRedirectTarget = true;
      continue;
    }
    
    // 跳过管道运算符
    if (part === '||' || part === '&&' || part === '|' || part === ';') {
      continue;
    }
    
    const baseCommand = part.trim().split(/\s+/)[0];
    
    // 跳过语义中性命令
    if (BASH_SEMANTIC_NEUTRAL_COMMANDS.has(baseCommand)) {
      continue;
    }
    
    hasNonNeutralCommand = true;
    
    // 关键：管道中任何非搜索/读取命令都会打破"只读"性质
    if (!BASH_SEARCH_COMMANDS.has(baseCommand) && 
        !BASH_READ_COMMANDS.has(baseCommand) && 
        !BASH_LIST_COMMANDS.has(baseCommand)) {
      return { isSearch: false, isRead: false, isList: false };
    }
    
    if (isPartSearch) hasSearch = true;
    if (isPartRead) hasRead = true;
    if (isPartList) hasList = true;
  }
  
  return { isSearch: hasSearch, isRead: hasRead, isList: hasList };
}
```

### 2.3 并发安全性判定

```typescript
// BashTool 的并发安全是条件性的
isConcurrencySafe(input) {
  // 只有只读命令才是并发安全的
  return this.isReadOnly?.(input) ?? false;
},

isReadOnly(input) {
  // 检查命令是否包含 cd（会改变工作目录）
  const compoundCommandHasCd = commandHasAnyCd(input.command);
  
  // 使用综合的只读约束检查
  const result = checkReadOnlyConstraints(input, compoundCommandHasCd);
  return result.behavior === 'allow';
}
```

### 2.4 权限检查流程

```typescript
export async function bashToolHasPermission(
  input: BashToolInput,
  toolUseContext: ToolUseContext
): Promise<PermissionResult> {
  const { command } = input;
  const appState = toolUseContext.getAppState();
  
  // 1. 检查权限模式
  const modeCheck = checkPermissionMode(appState.toolPermissionContext);
  if (!modeCheck.result) return modeCheck;
  
  // 2. 解析命令为 AST（用于安全分析）
  const parsed = await parseForSecurity(command);
  
  // 3. 检查每个子命令的权限
  if (parsed.kind === 'simple') {
    for (const subcommand of parsed.commands) {
      const subcheck = await checkSubcommandPermission(
        subcommand, 
        appState.toolPermissionContext
      );
      if (!subcheck.allowed) {
        return subcheck.result;
      }
    }
  }
  
  // 4. 检查沙箱要求
  const sandboxCheck = shouldUseSandbox(command, appState);
  if (sandboxCheck.required && !sandboxCheck.allowed) {
    return { behavior: 'deny', reason: 'sandbox_required' };
  }
  
  // 5. 检查路径约束
  const pathCheck = checkPathConstraints(command, appState);
  if (!pathCheck.allowed) return pathCheck.result;
  
  // 6. 检查 sed 编辑约束
  const sedCheck = checkSedConstraints(command);
  if (!sedCheck.allowed) return sedCheck.result;
  
  return { behavior: 'allow' };
}
```

### 2.5 后台任务机制

```typescript
// 进度显示常量
const PROGRESS_THRESHOLD_MS = 2000;        // 2秒后显示进度
const ASSISTANT_BLOCKING_BUDGET_MS = 15_000; // 助手模式15秒后自动后台化

// 禁止自动后台化的命令
const DISALLOWED_AUTO_BACKGROUND_COMMANDS = ['sleep'];

function isAutobackgroundingAllowed(command: string): boolean {
  const parts = splitCommand_DEPRECATED(command);
  const baseCommand = parts[0]?.trim();
  return !DISALLOWED_AUTO_BACKGROUND_COMMANDS.includes(baseCommand);
}

// 检测阻塞式 sleep 模式
export function detectBlockedSleepPattern(command: string): string | null {
  const parts = splitCommand_DEPRECATED(command);
  const first = parts[0]?.trim() ?? '';
  
  // 匹配 sleep N 模式（N >= 2秒）
  const m = /^sleep\s+(\d+)\s*$/.exec(first);
  if (!m) return null;
  
  const secs = parseInt(m[1]!, 10);
  if (secs < 2) return null; // 短暂 sleep 用于节流是允许的
  
  const rest = parts.slice(1).join(' ').trim();
  return rest 
    ? `sleep ${secs} followed by: ${rest}` 
    : `standalone sleep ${secs}`;
}
```

### 2.6 Sed 编辑模拟

BashTool 特殊处理 `sed -i` 命令，将其转换为可预览的文件编辑：

```typescript
async function applySedEdit(
  simulatedEdit: { filePath: string; newContent: string },
  toolUseContext: SimulatedSedEditContext,
  parentMessage?: AssistantMessage
): Promise<SimulatedSedEditResult> {
  const { filePath, newContent } = simulatedEdit;
  const absoluteFilePath = expandPath(filePath);
  const fs = getFsImplementation();
  
  // 1. 读取原始内容
  const encoding = detectFileEncoding(absoluteFilePath);
  const originalContent = await fs.readFile(absoluteFilePath, { encoding });
  
  // 2. 跟踪文件历史（支持撤销）
  if (fileHistoryEnabled() && parentMessage) {
    await fileHistoryTrackEdit(
      toolUseContext.updateFileHistoryState, 
      absoluteFilePath, 
      parentMessage.uuid
    );
  }
  
  // 3. 检测行尾符并写入新内容
  const endings = detectLineEndings(absoluteFilePath);
  writeTextContent(absoluteFilePath, newContent, encoding, endings);
  
  // 4. 通知 VS Code 文件变更
  notifyVscodeFileUpdated(absoluteFilePath, originalContent, newContent);
  
  // 5. 更新读取时间戳（防止过期写入）
  toolUseContext.readFileState.set(absoluteFilePath, {
    content: newContent,
    timestamp: getFileModificationTime(absoluteFilePath),
    offset: undefined,
    limit: undefined
  });
  
  return {
    data: { stdout: '', stderr: '', interrupted: false }
  };
}
```

---

## 3. FileEditTool：精确文本替换

### 3.1 设计哲学

FileEditTool 采用"搜索-替换"模式而非"全文覆写"：

```typescript
// 输入参数
const inputSchema = z.strictObject({
  file_path: z.string().describe('The absolute path to the file to modify'),
  old_string: z.string().describe('The exact string to replace'),
  new_string: z.string().describe('The new string to insert'),
  replace_all: z.boolean().optional().describe('Replace all occurrences')
});
```

**优势**：
- 更小的上下文开销（只传输变更部分）
- 更精确的错误检测（找不到 `old_string` 立即失败）
- 更好的冲突检测（并发编辑时能发现 `old_string` 不存在）

### 3.2 验证流程

```typescript
async validateInput(input: FileEditInput, toolUseContext: ToolUseContext) {
  const { file_path, old_string, new_string, replace_all = false } = input;
  const fullFilePath = expandPath(file_path);
  
  // 1. 检查无效操作
  if (old_string === new_string) {
    return {
      result: false,
      behavior: 'ask',
      message: 'No changes to make: old_string and new_string are exactly the same.',
      errorCode: 1
    };
  }
  
  // 2. 检查权限规则
  const denyRule = matchingRuleForInput(
    fullFilePath, 
    appState.toolPermissionContext, 
    'edit', 
    'deny'
  );
  if (denyRule !== null) {
    return {
      result: false,
      behavior: 'ask',
      message: 'File is in a directory that is denied by your permission settings.',
      errorCode: 2
    };
  }
  
  // 3. 安全：跳过 UNC 路径以防止 NTLM 凭证泄露
  if (fullFilePath.startsWith('\\\\') || fullFilePath.startsWith('//')) {
    return { result: true };
  }
  
  // 4. 检查文件大小
  const { size } = await fs.stat(fullFilePath);
  if (size > MAX_EDIT_FILE_SIZE) { // 1 GiB
    return {
      result: false,
      behavior: 'ask',
      message: `File is too large to edit (${formatFileSize(size)}).`,
      errorCode: 10
    };
  }
  
  // 5. 读取文件内容
  const fileBuffer = await fs.readFileBytes(fullFilePath);
  const encoding = detectEncoding(fileBuffer);
  const fileContent = fileBuffer.toString(encoding).replaceAll('\r\n', '\n');
  
  // 6. 检查文件是否存在
  if (fileContent === null) {
    // 空 old_string + 不存在文件 = 创建新文件
    if (old_string === '') return { result: true };
    
    // 尝试建议相似文件名
    const similarFilename = findSimilarFile(fullFilePath);
    return {
      result: false,
      behavior: 'ask',
      message: `File does not exist. Did you mean ${similarFilename}?`,
      errorCode: 4
    };
  }
  
  // 7. 检查文件是否已被读取
  const readTimestamp = toolUseContext.readFileState.get(fullFilePath);
  if (!readTimestamp || readTimestamp.isPartialView) {
    return {
      result: false,
      behavior: 'ask',
      message: 'File has not been read yet. Read it first before writing to it.',
      errorCode: 6
    };
  }
  
  // 8. 检查文件是否在读取后被修改
  const lastWriteTime = getFileModificationTime(fullFilePath);
  if (lastWriteTime > readTimestamp.timestamp) {
    // Windows 上时间戳可能因云同步/杀毒软件而改变，需要比较内容
    const isFullRead = readTimestamp.offset === undefined && 
                       readTimestamp.limit === undefined;
    if (isFullRead && fileContent === readTimestamp.content) {
      // 内容未变，安全继续
    } else {
      return {
        result: false,
        behavior: 'ask',
        message: 'File has been modified since read. Read it again.',
        errorCode: 7
      };
    }
  }
  
  // 9. 使用 findActualString 处理引号规范化
  const actualOldString = findActualString(fileContent, old_string);
  if (!actualOldString) {
    return {
      result: false,
      behavior: 'ask',
      message: `String to replace not found in file.\nString: ${old_string}`,
      errorCode: 8
    };
  }
  
  // 10. 检查多重匹配
  const matches = fileContent.split(actualOldString).length - 1;
  if (matches > 1 && !replace_all) {
    return {
      result: false,
      behavior: 'ask',
      message: `Found ${matches} matches. Set replace_all=true or provide more context.`,
      errorCode: 9
    };
  }
  
  return { result: true, meta: { actualOldString } };
}
```

### 3.3 原子写入流程

```typescript
async call(input: FileEditInput, context, _, parentMessage) {
  const { file_path, old_string, new_string, replace_all = false } = input;
  const absoluteFilePath = expandPath(file_path);
  
  // === 准备阶段（可以有 async 操作）===
  
  // 确保父目录存在
  await fs.mkdir(dirname(absoluteFilePath));
  
  // 备份文件（用于撤销）
  if (fileHistoryEnabled()) {
    await fileHistoryTrackEdit(
      updateFileHistoryState,
      absoluteFilePath,
      parentMessage.uuid
    );
  }
  
  // === 关键区间（避免 async 操作以保持原子性）===
  
  // 同步读取当前状态
  const { content: originalFileContents, fileExists, encoding, lineEndings } = 
    readFileForEdit(absoluteFilePath);
  
  // 再次确认文件未被修改
  if (fileExists) {
    const lastWriteTime = getFileModificationTime(absoluteFilePath);
    const lastRead = readFileState.get(absoluteFilePath);
    if (!lastRead || lastWriteTime > lastRead.timestamp) {
      throw new Error(FILE_UNEXPECTEDLY_MODIFIED_ERROR);
    }
  }
  
  // 处理引号风格
  const actualOldString = findActualString(originalFileContents, old_string) || old_string;
  const actualNewString = preserveQuoteStyle(old_string, actualOldString, new_string);
  
  // 生成 patch 和更新后的内容
  const { patch, updatedFile } = getPatchForEdit({
    filePath: absoluteFilePath,
    fileContents: originalFileContents,
    oldString: actualOldString,
    newString: actualNewString,
    replaceAll: replace_all
  });
  
  // 写入磁盘
  writeTextContent(absoluteFilePath, updatedFile, encoding, lineEndings);
  
  // === 后处理阶段 ===
  
  // 通知 LSP 服务器
  const lspManager = getLspServerManager();
  if (lspManager) {
    clearDeliveredDiagnosticsForFile(`file://${absoluteFilePath}`);
    lspManager.changeFile(absoluteFilePath, updatedFile);
  }
  
  // 通知 VS Code
  notifyVscodeFileUpdated(absoluteFilePath, originalFileContents, updatedFile);
  
  // 更新读取状态
  readFileState.set(absoluteFilePath, {
    content: updatedFile,
    timestamp: getFileModificationTime(absoluteFilePath),
    offset: undefined,
    limit: undefined
  });
  
  return {
    data: {
      filePath: absoluteFilePath,
      patch,
      updatedFile,
      linesChanged: countLinesChanged(patch)
    }
  };
}
```

---

## 4. FileReadTool：多格式文件读取

### 4.1 支持的格式

```typescript
// 输出类型的判别联合
const outputSchema = z.discriminatedUnion('type', [
  // 文本文件
  z.object({
    type: z.literal('text'),
    file: z.object({
      filePath: z.string(),
      content: z.string(),
      numLines: z.number(),
      startLine: z.number(),
      totalLines: z.number()
    })
  }),
  
  // 图像文件
  z.object({
    type: z.literal('image'),
    file: z.object({
      base64: z.string(),
      type: z.enum(['image/jpeg', 'image/png', 'image/gif', 'image/webp']),
      originalSize: z.number(),
      dimensions: z.object({
        originalWidth: z.number().optional(),
        originalHeight: z.number().optional(),
        displayWidth: z.number().optional(),
        displayHeight: z.number().optional()
      }).optional()
    })
  }),
  
  // Jupyter Notebook
  z.object({
    type: z.literal('notebook'),
    file: z.object({
      filePath: z.string(),
      cells: z.array(z.any())
    })
  }),
  
  // PDF 文档
  z.object({
    type: z.literal('pdf'),
    file: z.object({
      filePath: z.string(),
      base64: z.string(),
      originalSize: z.number()
    })
  }),
  
  // PDF 分页提取
  z.object({
    type: z.literal('parts'),
    file: z.object({
      filePath: z.string(),
      originalSize: z.number(),
      count: z.number(),
      outputDir: z.string()
    })
  }),
  
  // 文件未变更（缓存命中）
  z.object({
    type: z.literal('file_unchanged'),
    file: z.object({
      filePath: z.string()
    })
  })
]);
```

### 4.2 设备文件安全

```typescript
// 阻止会导致进程挂起的设备文件
const BLOCKED_DEVICE_PATHS = new Set([
  // 无限输出 - 永远无法到达 EOF
  '/dev/zero',
  '/dev/random',
  '/dev/urandom',
  '/dev/full',
  
  // 阻塞等待输入
  '/dev/stdin',
  '/dev/tty',
  '/dev/console',
  
  // 读取无意义
  '/dev/stdout',
  '/dev/stderr',
  
  // stdio 的 fd 别名
  '/dev/fd/0',
  '/dev/fd/1',
  '/dev/fd/2'
]);

function isBlockedDevicePath(filePath: string): boolean {
  if (BLOCKED_DEVICE_PATHS.has(filePath)) return true;
  
  // Linux 的 /proc/self/fd/0-2 是 stdio 的别名
  if (filePath.startsWith('/proc/') && 
      (filePath.endsWith('/fd/0') ||
       filePath.endsWith('/fd/1') ||
       filePath.endsWith('/fd/2'))) {
    return true;
  }
  
  return false;
}
```

### 4.3 Token 限制机制

```typescript
export class MaxFileReadTokenExceededError extends Error {
  constructor(
    public tokenCount: number,
    public maxTokens: number
  ) {
    super(
      `File content (${tokenCount} tokens) exceeds maximum allowed tokens (${maxTokens}). ` +
      `Use offset and limit parameters to read specific portions of the file.`
    );
    this.name = 'MaxFileReadTokenExceededError';
  }
}

// 读取时的 Token 估算
async function validateContentTokens(content: string, filePath: string): Promise<void> {
  const limits = getDefaultFileReadingLimits();
  
  // 快速估算（基于文件类型的字符/Token 比率）
  const roughEstimate = roughTokenCountEstimationForFileType(content, filePath);
  
  if (roughEstimate > limits.maxTokens * 1.5) {
    // 粗估过高，直接拒绝
    throw new MaxFileReadTokenExceededError(roughEstimate, limits.maxTokens);
  }
  
  if (roughEstimate > limits.maxTokens * 0.8) {
    // 粗估接近限制，使用精确计算
    const exactCount = await countTokensWithAPI(content);
    if (exactCount > limits.maxTokens) {
      throw new MaxFileReadTokenExceededError(exactCount, limits.maxTokens);
    }
  }
}
```

### 4.4 macOS 截图路径修复

```typescript
// macOS 截图文件名中的空格可能是普通空格或窄不间断空格（U+202F）
const THIN_SPACE = String.fromCharCode(8239);

function getAlternateScreenshotPath(filePath: string): string | undefined {
  const filename = path.basename(filePath);
  
  // 匹配 AM/PM 前的空格
  const amPmPattern = /^(.+)([ \u202F])(AM|PM)(\.png)$/;
  const match = filename.match(amPmPattern);
  if (!match) return undefined;
  
  // 尝试另一种空格字符
  const currentSpace = match[2];
  const alternateSpace = currentSpace === ' ' ? THIN_SPACE : ' ';
  
  return filePath.replace(
    `${currentSpace}${match[3]}${match[4]}`,
    `${alternateSpace}${match[3]}${match[4]}`
  );
}
```

---

## 5. GrepTool：智能正则搜索

### 5.1 基于 ripgrep 的实现

```typescript
export const GrepTool = buildTool({
  name: GREP_TOOL_NAME,
  searchHint: 'search file contents with regex (ripgrep)',
  maxResultSizeChars: 20_000,
  
  // 并发安全 + 只读
  isConcurrencySafe() { return true; },
  isReadOnly() { return true; },
  
  async call({
    pattern,
    path,
    glob,
    type,
    output_mode = 'files_with_matches',
    '-B': context_before,
    '-A': context_after,
    '-C': context_c,
    context,
    '-n': show_line_numbers = true,
    '-i': case_insensitive = false,
    head_limit,
    offset = 0,
    multiline = false
  }, { abortController, getAppState }) {
    const absolutePath = path ? expandPath(path) : getCwd();
    const args = ['--hidden'];
    
    // 排除版本控制目录
    const VCS_DIRECTORIES = ['.git', '.svn', '.hg', '.bzr', '.jj', '.sl'];
    for (const dir of VCS_DIRECTORIES) {
      args.push('--glob', `!${dir}`);
    }
    
    // 限制行长度防止 base64/压缩内容污染输出
    args.push('--max-columns', '500');
    
    // 多行模式
    if (multiline) {
      args.push('-U', '--multiline-dotall');
    }
    
    // 大小写敏感
    if (case_insensitive) {
      args.push('-i');
    }
    
    // 输出模式
    if (output_mode === 'files_with_matches') {
      args.push('-l');
    } else if (output_mode === 'count') {
      args.push('-c');
    }
    
    // 处理以 - 开头的模式
    if (pattern.startsWith('-')) {
      args.push('-e', pattern);
    } else {
      args.push(pattern);
    }
    
    // 执行 ripgrep
    const results = await ripGrep(args, absolutePath, abortController.signal);
    
    // 应用分页限制
    const { items, appliedLimit } = applyHeadLimit(results, head_limit, offset);
    
    return { data: formatResults(items, output_mode, appliedLimit) };
  }
});
```

### 5.2 输出分页机制

```typescript
const DEFAULT_HEAD_LIMIT = 250; // 默认限制

function applyHeadLimit<T>(
  items: T[],
  limit: number | undefined,
  offset: number = 0
): { items: T[]; appliedLimit: number | undefined } {
  // 显式传入 0 表示无限制
  if (limit === 0) {
    return { items: items.slice(offset), appliedLimit: undefined };
  }
  
  const effectiveLimit = limit ?? DEFAULT_HEAD_LIMIT;
  const sliced = items.slice(offset, offset + effectiveLimit);
  
  // 只在实际截断时报告限制
  const wasTruncated = items.length - offset > effectiveLimit;
  return {
    items: sliced,
    appliedLimit: wasTruncated ? effectiveLimit : undefined
  };
}
```

---

## 6. AgentTool：子代理启动器

### 6.1 输入 Schema

```typescript
const fullInputSchema = z.object({
  description: z.string().describe('A short (3-5 word) description of the task'),
  prompt: z.string().describe('The task for the agent to perform'),
  subagent_type: z.string().optional()
    .describe('The type of specialized agent to use'),
  model: z.enum(['sonnet', 'opus', 'haiku']).optional()
    .describe('Optional model override for this agent'),
  run_in_background: z.boolean().optional()
    .describe('Set to true to run this agent in the background'),
  name: z.string().optional()
    .describe('Name for the spawned agent (enables SendMessage addressing)'),
  team_name: z.string().optional()
    .describe('Team name for spawning'),
  mode: permissionModeSchema().optional()
    .describe('Permission mode for spawned teammate'),
  isolation: z.enum(['worktree', 'remote']).optional()
    .describe('Isolation mode: worktree or remote'),
  cwd: z.string().optional()
    .describe('Working directory override')
});
```

### 6.2 输出类型

```typescript
const outputSchema = z.union([
  // 同步完成
  agentToolResultSchema().extend({
    status: z.literal('completed'),
    prompt: z.string()
  }),
  
  // 异步启动
  z.object({
    status: z.literal('async_launched'),
    agentId: z.string(),
    description: z.string(),
    prompt: z.string(),
    outputFile: z.string(),
    canReadOutputFile: z.boolean().optional()
  })
]);

// 内部类型：团队成员生成
type TeammateSpawnedOutput = {
  status: 'teammate_spawned';
  prompt: string;
  teammate_id: string;
  agent_id: string;
  agent_type?: string;
  model?: string;
  name: string;
  color?: string;
  tmux_session_name: string;
  tmux_window_name: string;
  tmux_pane_id: string;
  team_name?: string;
  is_splitpane?: boolean;
  plan_mode_required?: boolean;
};

// 内部类型：远程启动
type RemoteLaunchedOutput = {
  status: 'remote_launched';
  taskId: string;
  sessionUrl: string;
  description: string;
  prompt: string;
  outputFile: string;
};
```

### 6.3 团队约束

```typescript
async call(input: AgentToolInput, toolUseContext, canUseTool, assistantMessage, onProgress?) {
  // 检查团队功能访问权限
  if (team_name && !isAgentSwarmsEnabled()) {
    throw new Error('Agent Teams is not yet available on your plan.');
  }
  
  // 团队成员不能嵌套生成其他团队成员
  // 团队名册是扁平的，嵌套会导致混乱
  if (isTeammate() && teamName && name) {
    throw new Error(
      'Teammates cannot spawn other teammates — the team roster is flat. ' +
      'To spawn a subagent instead, omit the `name` parameter.'
    );
  }
  
  // 进程内团队成员不能启动后台代理
  // 它们的生命周期与领导者进程绑定
  if (isInProcessTeammate() && teamName && run_in_background === true) {
    throw new Error(
      'In-process teammates cannot spawn background agents. ' +
      'Use run_in_background=false for synchronous subagents.'
    );
  }
  
  // ... 其余执行逻辑
}
```

### 6.4 工作树隔离

```typescript
// 当 isolation: 'worktree' 时，在独立的 git worktree 中运行代理
async function setupWorktreeIsolation(
  agentId: string,
  cwd: string
): Promise<{ worktreePath: string; cleanup: () => Promise<void> }> {
  // 创建临时工作树
  const worktreePath = await createAgentWorktree(agentId, cwd);
  
  return {
    worktreePath,
    cleanup: async () => {
      // 检查是否有未提交的更改
      if (await hasWorktreeChanges(worktreePath)) {
        // 提示用户处理更改
        console.warn(`Worktree ${worktreePath} has uncommitted changes`);
      }
      
      // 移除工作树
      await removeAgentWorktree(worktreePath);
    }
  };
}
```

---

## 7. 共享模式与最佳实践

### 7.1 路径扩展

所有工具都使用 `expandPath` 规范化路径：

```typescript
import { expandPath } from '../../utils/path.js';

// 在 validateInput 中
const fullFilePath = expandPath(file_path);

// 在 backfillObservableInput 中（用于权限钩子）
backfillObservableInput(input) {
  if (typeof input.file_path === 'string') {
    input.file_path = expandPath(input.file_path);
  }
}
```

### 7.2 UNC 路径安全

Windows UNC 路径会触发 SMB 认证，可能泄露 NTLM 凭证：

```typescript
// 所有工具的 validateInput 都应包含此检查
if (fullFilePath.startsWith('\\\\') || fullFilePath.startsWith('//')) {
  // 跳过文件系统操作，让权限检查处理
  return { result: true };
}
```

### 7.3 权限匹配器准备

```typescript
// 标准的 preparePermissionMatcher 实现
async preparePermissionMatcher({ file_path }) {
  return pattern => matchWildcardPattern(pattern, file_path);
}

// BashTool 的复杂实现（处理复合命令）
async preparePermissionMatcher({ command }) {
  const parsed = await parseForSecurity(command);
  if (parsed.kind !== 'simple') {
    return () => true; // 无法解析时，默认触发钩子
  }
  
  const subcommands = parsed.commands.map(c => c.argv.join(' '));
  return pattern => {
    const prefix = permissionRuleExtractPrefix(pattern);
    return subcommands.some(cmd => {
      if (prefix !== null) {
        return cmd === prefix || cmd.startsWith(`${prefix} `);
      }
      return matchWildcardPattern(pattern, cmd);
    });
  };
}
```

### 7.4 活动描述

用于 UI 显示当前正在执行的操作：

```typescript
getActivityDescription(input) {
  const summary = getToolUseSummary(input);
  return summary ? `Editing ${summary}` : 'Editing file';
}
```

---

## 8. 关键数据流图

```
┌─────────────────────────────────────────────────────────────────┐
│                         Tool Call Flow                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. inputSchema.parse(rawInput)                                 │
│           │                                                     │
│           ▼                                                     │
│  2. validateInput(parsedInput, context)                         │
│           │                                                     │
│           ├──▶ { result: false } → Error Response              │
│           │                                                     │
│           ▼                                                     │
│  3. checkPermissions(input, context)                            │
│           │                                                     │
│           ├──▶ 'deny' → Rejection Response                     │
│           ├──▶ 'ask'  → Permission Dialog                      │
│           │                                                     │
│           ▼                                                     │
│  4. call(input, context, canUseTool, message, onProgress)       │
│           │                                                     │
│           ├──▶ onProgress(progressData) [if async]             │
│           │                                                     │
│           ▼                                                     │
│  5. outputSchema.parse(result)                                  │
│           │                                                     │
│           ▼                                                     │
│  6. mapToolResultToToolResultBlockParam(result, toolUseId)      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 9. 下一步

本篇覆盖了五个核心工具的实现细节。这些工具是 Claude Code 与文件系统和 Shell 交互的基础。

接下来，我们将探索 MCP（模型上下文协议）集成，了解 Claude Code 如何扩展其工具集：

👉 **继续阅读**：[08-模型上下文协议(MCP)深度集成.md](/claudecode/08-mcp-integration)
