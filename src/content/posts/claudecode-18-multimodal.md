---
title: "多模态支持"
summary: "Claude Code 的多模态支持采用分层处理架构："
publishedAt: 2026-08-24
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 18
seriesGroup: 工程支撑
source: "18-多模态支持.md"
sourceSha256: 131e83a433df
---
> **摘要**：深入分析 Claude Code 的多模态支持系统，包括图像处理管道、剪贴板集成、自动压缩与缩放策略、跨平台实现以及 Vision API 的完整集成架构。

## 目录
- [架构概览](#架构概览)
- [图像处理管道](#图像处理管道)
- [剪贴板集成](#剪贴板集成)
- [图像存储系统](#图像存储系统)
- [API 限制与验证](#api-限制与验证)
- [跨平台支持](#跨平台支持)
- [设计亮点](#设计亮点)
- [总结](#总结)

---

## 架构概览

Claude Code 的多模态支持采用分层处理架构：

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户输入层                                │
├─────────────────┬─────────────────┬─────────────────────────────┤
│   剪贴板粘贴     │   文件拖放       │   @文件引用                 │
│  (Ctrl+V)       │  (Drag & Drop)  │  (@image.png)              │
├─────────────────┴─────────────────┴─────────────────────────────┤
│                        处理管道层                                │
│  格式检测 │ 尺寸压缩 │ 质量调整 │ 格式转换 │ Base64编码          │
├─────────────────────────────────────────────────────────────────┤
│                        存储与验证层                              │
│  会话缓存 │ 磁盘持久化 │ API限制验证 │ 错误处理                 │
├─────────────────────────────────────────────────────────────────┤
│                        API 集成层                                │
│  ImageBlockParam │ Content Block 构建 │ Vision API 调用         │
└─────────────────────────────────────────────────────────────────┘
```

### 核心类型定义

```typescript
// 图像块参数（Anthropic SDK 类型）
interface ImageBlockParam {
  type: 'image'
  source: {
    type: 'base64'
    media_type: 'image/png' | 'image/jpeg' | 'image/gif' | 'image/webp'
    data: string  // Base64 编码数据
  }
}

// 图像尺寸信息
interface ImageDimensions {
  originalWidth?: number   // 原始宽度
  originalHeight?: number  // 原始高度
  displayWidth?: number    // 显示/压缩后宽度
  displayHeight?: number   // 显示/压缩后高度
}

// 带尺寸信息的图像
interface ImageWithDimensions {
  base64: string
  mediaType: string
  dimensions?: ImageDimensions
}
```

---

## 图像处理管道

### 核心限制常量

```typescript
// src/constants/apiLimits.ts

// API 强制的 Base64 尺寸限制（5MB）
export const API_IMAGE_MAX_BASE64_SIZE = 5 * 1024 * 1024

// 目标原始尺寸（留出 Base64 编码膨胀空间）
// Base64 编码后膨胀 33%，因此原始尺寸限制为 3.75MB
export const IMAGE_TARGET_RAW_SIZE = (API_IMAGE_MAX_BASE64_SIZE * 3) / 4

// 客户端最大尺寸限制
export const IMAGE_MAX_WIDTH = 2000
export const IMAGE_MAX_HEIGHT = 2000
```

### 智能压缩策略

图像压缩器实现了多级降级策略：

```typescript
// src/utils/imageResizer.ts

export async function maybeResizeAndDownsampleImageBuffer(
  imageBuffer: Buffer,
  originalSize: number,
  ext: string,
): Promise<ResizeResult> {
  // 1. 空缓冲区检查
  if (imageBuffer.length === 0) {
    throw new ImageResizeError('Image file is empty (0 bytes)')
  }
  
  const sharp = await getImageProcessor()
  const image = sharp(imageBuffer)
  const metadata = await image.metadata()
  
  // 2. 检查是否需要处理
  const { width, height } = metadata
  if (
    originalSize <= IMAGE_TARGET_RAW_SIZE &&
    width <= IMAGE_MAX_WIDTH &&
    height <= IMAGE_MAX_HEIGHT
  ) {
    return { buffer: imageBuffer, mediaType, dimensions: {...} }
  }
  
  // 3. 多级压缩策略
  const strategies = [
    // PNG 优先：保持透明度
    () => sharp(imageBuffer).png({ compressionLevel: 9, palette: true }),
    // JPEG 渐进降质
    () => sharp(imageBuffer).jpeg({ quality: 80 }),
    () => sharp(imageBuffer).jpeg({ quality: 60 }),
    () => sharp(imageBuffer).jpeg({ quality: 40 }),
    () => sharp(imageBuffer).jpeg({ quality: 20 }),
  ]
  
  // 4. 尺寸约束下压缩
  for (const strategy of strategies) {
    const result = await strategy().resize(width, height, {
      fit: 'inside',
      withoutEnlargement: true,
    }).toBuffer()
    
    if (result.length <= IMAGE_TARGET_RAW_SIZE) {
      return { buffer: result, ... }
    }
  }
  
  // 5. 最后手段：缩小尺寸 + 激进压缩
  const smallerWidth = Math.min(width, 1000)
  return sharp(imageBuffer)
    .resize(smallerWidth, proportionalHeight, {...})
    .jpeg({ quality: 20 })
    .toBuffer()
}
```

### 压缩策略流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                     图像处理流程                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  输入图像                                                       │
│      │                                                          │
│      ▼                                                          │
│  ┌──────────────────┐                                          │
│  │ 尺寸 & 大小检查   │                                          │
│  └────────┬─────────┘                                          │
│           │                                                     │
│     ┌─────┴─────┐                                              │
│     │ 在限制内？ │                                              │
│     └─────┬─────┘                                              │
│      是   │   否                                                │
│      │    │                                                     │
│      ▼    ▼                                                     │
│  直接返回  ┌──────────────────┐                                 │
│           │ 仅超尺寸？         │                                │
│           └─────────┬────────┘                                 │
│                否   │   是                                      │
│                │    │                                           │
│                ▼    ▼                                           │
│           尝试压缩  仅调整尺寸                                   │
│                │                                                │
│                ▼                                                │
│  ┌─────────────────────────────────────────┐                   │
│  │ PNG 压缩 (level=9, palette=true)        │                   │
│  │     ↓ 仍超限？                           │                   │
│  │ JPEG 80% → 60% → 40% → 20%              │                   │
│  │     ↓ 仍超限？                           │                   │
│  │ 缩小到 1000px + JPEG 20%                │                   │
│  └─────────────────────────────────────────┘                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 错误分类与恢复

```typescript
// 错误类型分类（用于分析）
const ERROR_TYPES = {
  MODULE_LOAD: 1,     // Sharp 模块加载失败
  PROCESSING: 2,      // 图像处理错误（格式、损坏）
  UNKNOWN: 3,
  PIXEL_LIMIT: 4,     // 像素/尺寸超限
  MEMORY: 5,          // 内存分配失败
  TIMEOUT: 6,         // 处理超时
  VIPS: 7,            // libvips 错误
  PERMISSION: 8,      // 权限错误
}

function classifyImageError(error: unknown): number {
  // 优先检查 Node.js 错误码
  if (error instanceof Error) {
    const code = (error as { code?: string }).code
    if (code === 'MODULE_NOT_FOUND' || code === 'ERR_DLOPEN_FAILED') {
      return ERROR_TYPE_MODULE_LOAD
    }
    // ... 其他错误码检查
  }
  
  // 回退到消息匹配
  const message = errorMessage(error)
  if (message.includes('unsupported image format')) {
    return ERROR_TYPE_PROCESSING
  }
  // ...
}
```

---

## 剪贴板集成

### 跨平台剪贴板命令

```typescript
// src/utils/imagePaste.ts

function getClipboardCommands() {
  const platform = process.platform as 'darwin' | 'linux' | 'win32'
  
  const commands = {
    darwin: {
      // macOS: 使用 osascript
      checkImage: `osascript -e 'the clipboard as «class PNGf»'`,
      saveImage: `osascript -e 'set png_data to (the clipboard as «class PNGf»)'...`,
      getPath: `osascript -e 'get POSIX path of (the clipboard as «class furl»)'`,
    },
    linux: {
      // Linux: 支持 X11 (xclip) 和 Wayland (wl-paste)
      checkImage: 'xclip -selection clipboard -t TARGETS -o | grep image/...',
      saveImage: 'xclip -selection clipboard -t image/png -o > ...',
    },
    win32: {
      // Windows: PowerShell
      checkImage: 'powershell -NoProfile -Command "(Get-Clipboard -Format Image)..."',
      saveImage: `powershell ... $img.Save('path', ImageFormat::Png)`,
    },
  }
  
  return commands[platform]
}
```

### 原生加速路径（macOS）

对于 macOS，Claude Code 提供了高性能的原生剪贴板读取：

```typescript
// Native NSPasteboard reader（~5ms cold, sub-ms warm）
// vs osascript fallback（~1.5s）

export async function getImageFromClipboard(): Promise<ImageWithDimensions | null> {
  // 快速路径：原生 NSPasteboard 读取
  if (feature('NATIVE_CLIPBOARD_IMAGE') && process.platform === 'darwin') {
    try {
      const { getNativeModule } = await import('image-processor-napi')
      const readClipboard = getNativeModule()?.readClipboardImage
      
      if (readClipboard) {
        const native = readClipboard(IMAGE_MAX_WIDTH, IMAGE_MAX_HEIGHT)
        if (!native) return null
        
        // 原生路径已限制尺寸，但可能仍超大小限制
        const buffer: Buffer = native.png
        if (buffer.length > IMAGE_TARGET_RAW_SIZE) {
          // 需要进一步压缩
          const resized = await maybeResizeAndDownsampleImageBuffer(...)
          return { base64: resized.buffer.toString('base64'), ... }
        }
        
        return {
          base64: buffer.toString('base64'),
          mediaType: 'image/png',
          dimensions: {
            originalWidth: native.originalWidth,
            originalHeight: native.originalHeight,
            displayWidth: native.width,
            displayHeight: native.height,
          },
        }
      }
    } catch (e) {
      // 回退到 osascript
    }
  }
  
  // 标准路径：使用系统命令
  const { commands, screenshotPath } = getClipboardCommands()
  // ...
}
```

### 路径与格式检测

```typescript
// 支持的图像扩展名
export const IMAGE_EXTENSION_REGEX = /\.(png|jpe?g|gif|webp)$/i

// 处理 shell 转义的路径
function stripBackslashEscapes(path: string): string {
  if (process.platform === 'win32') {
    return path // Windows 路径使用反斜杠
  }
  
  // 处理双反斜杠和 shell 转义
  const salt = randomBytes(8).toString('hex')
  const placeholder = `__DOUBLE_BACKSLASH_${salt}__`
  return path
    .replace(/\\\\/g, placeholder)
    .replace(/\\(.)/g, '$1')
    .replace(new RegExp(placeholder, 'g'), '\\')
}

// BMP 格式自动转换
if (imageBuffer[0] === 0x42 && imageBuffer[1] === 0x4d) {
  const sharp = await getImageProcessor()
  imageBuffer = await sharp(imageBuffer).png().toBuffer()
}
```

---

## 图像存储系统

### 会话级图像缓存

```typescript
// src/utils/imageStore.ts

const IMAGE_STORE_DIR = 'image-cache'
const MAX_STORED_IMAGE_PATHS = 200

// 内存中的路径缓存
const storedImagePaths = new Map<number, string>()

function getImageStoreDir(): string {
  return join(getClaudeConfigHomeDir(), IMAGE_STORE_DIR, getSessionId())
}

// 缓存图像路径（快速，无 I/O）
export function cacheImagePath(content: PastedContent): string | null {
  if (content.type !== 'image') return null
  
  const imagePath = getImagePath(content.id, content.mediaType || 'image/png')
  evictOldestIfAtCap()  // LRU 驱逐
  storedImagePaths.set(content.id, imagePath)
  return imagePath
}

// 异步存储到磁盘
export async function storeImage(content: PastedContent): Promise<string | null> {
  await ensureImageStoreDir()
  
  const imagePath = getImagePath(content.id, content.mediaType)
  const fh = await open(imagePath, 'w', 0o600)  // 安全权限
  try {
    await fh.writeFile(content.content, { encoding: 'base64' })
    await fh.datasync()  // 确保写入磁盘
  } finally {
    await fh.close()
  }
  
  storedImagePaths.set(content.id, imagePath)
  return imagePath
}
```

### LRU 驱逐策略

```typescript
function evictOldestIfAtCap(): void {
  while (storedImagePaths.size >= MAX_STORED_IMAGE_PATHS) {
    // Map 保持插入顺序，第一个即为最旧
    const oldest = storedImagePaths.keys().next().value
    if (oldest !== undefined) {
      storedImagePaths.delete(oldest)
    } else {
      break
    }
  }
}
```

### 会话清理

```typescript
// 清理旧会话的图像缓存
export async function cleanupOldImageCaches(): Promise<void> {
  const baseDir = join(getClaudeConfigHomeDir(), IMAGE_STORE_DIR)
  const currentSessionId = getSessionId()
  
  const sessionDirs = await fsImpl.readdir(baseDir)
  
  for (const sessionDir of sessionDirs) {
    if (sessionDir.name === currentSessionId) continue
    
    const sessionPath = join(baseDir, sessionDir.name)
    await fsImpl.rm(sessionPath, { recursive: true, force: true })
  }
  
  // 如果目录为空，删除父目录
  const remaining = await fsImpl.readdir(baseDir)
  if (remaining.length === 0) {
    await fsImpl.rmdir(baseDir)
  }
}
```

---

## API 限制与验证

### API 边界验证

```typescript
// src/utils/imageValidation.ts

// 超大图像信息
type OversizedImage = {
  index: number
  size: number
}

// 尺寸错误类
export class ImageSizeError extends Error {
  constructor(oversizedImages: OversizedImage[], maxSize: number) {
    const message = oversizedImages.length === 1
      ? `Image base64 size (${formatFileSize(size)}) exceeds API limit (${formatFileSize(maxSize)}).`
      : `${oversizedImages.length} images exceed the API limit...`
    super(message)
    this.name = 'ImageSizeError'
  }
}

// API 请求前验证
export function validateImagesForAPI(messages: unknown[]): void {
  const oversizedImages: OversizedImage[] = []
  let imageIndex = 0
  
  for (const msg of messages) {
    // 仅检查用户消息
    if (m.type !== 'user') continue
    
    const content = m.message?.content
    if (!Array.isArray(content)) continue
    
    for (const block of content) {
      if (isBase64ImageBlock(block)) {
        imageIndex++
        const base64Size = block.source.data.length
        
        if (base64Size > API_IMAGE_MAX_BASE64_SIZE) {
          logEvent('tengu_image_api_validation_failed', {
            base64_size_bytes: base64Size,
            max_bytes: API_IMAGE_MAX_BASE64_SIZE,
          })
          oversizedImages.push({ index: imageIndex, size: base64Size })
        }
      }
    }
  }
  
  if (oversizedImages.length > 0) {
    throw new ImageSizeError(oversizedImages, API_IMAGE_MAX_BASE64_SIZE)
  }
}
```

### 类型守卫

```typescript
function isBase64ImageBlock(
  block: unknown,
): block is { type: 'image'; source: { type: 'base64'; data: string } } {
  if (typeof block !== 'object' || block === null) return false
  const b = block as Record<string, unknown>
  if (b.type !== 'image') return false
  if (typeof b.source !== 'object' || b.source === null) return false
  const source = b.source as Record<string, unknown>
  return source.type === 'base64' && typeof source.data === 'string'
}
```

### Token 估算

```typescript
// src/services/tokenEstimation.ts

if (block.type === 'image' || block.type === 'document') {
  // 图像 Token 计算参考：
  // https://platform.claude.com/docs/en/build-with-claude/vision#calculate-image-costs
  
  // 文档：Base64 PDF 在 source.data 中
  // 注意：1MB PDF ≈ 1.33M Base64 字符
  // ...
}
```

---

## 跨平台支持

### 图像处理器抽象

```typescript
// src/tools/FileReadTool/imageProcessor.ts

// Sharp 实例接口
type SharpInstance = {
  metadata(): Promise<{ width: number; height: number; format: string }>
  resize(width: number, height: number, options?: {...}): SharpInstance
  jpeg(options?: { quality?: number }): SharpInstance
  png(options?: { compressionLevel?: number; palette?: boolean }): SharpInstance
  webp(options?: { quality?: number }): SharpInstance
  toBuffer(): Promise<Buffer>
}

let imageProcessorModule: { default: SharpFunction } | null = null

export async function getImageProcessor(): Promise<SharpFunction> {
  if (imageProcessorModule) {
    return imageProcessorModule.default
  }
  
  if (isInBundledMode()) {
    try {
      // 优先使用原生模块
      const imageProcessor = await import('image-processor-napi')
      const sharp = imageProcessor.sharp || imageProcessor.default
      imageProcessorModule = { default: sharp }
      return sharp
    } catch {
      console.warn('Native image processor not available, falling back to sharp')
    }
  }
  
  // 回退到 sharp
  const imported = await import('sharp')
  const sharp = unwrapDefault(imported)
  imageProcessorModule = { default: sharp }
  return sharp
}
```

### 平台特定临时路径

```typescript
const baseTmpDir =
  process.env.CLAUDE_CODE_TMPDIR ||
  (platform === 'win32' 
    ? process.env.TEMP || 'C:\\Temp' 
    : '/tmp')

const screenshotFilename = 'claude_cli_latest_screenshot.png'
```

---

## 设计亮点

### 1. 渐进式压缩策略

```
保真度高 ───────────────────────────────────────► 保真度低
┌────────┬────────┬────────┬────────┬────────┬────────┐
│ PNG 9  │ JPEG   │ JPEG   │ JPEG   │ JPEG   │ 缩小+  │
│ palette│ 80%    │ 60%    │ 40%    │ 20%    │ JPEG20 │
└────────┴────────┴────────┴────────┴────────┴────────┘
   ↑                                              ↑
 透明度                                        最后手段
```

### 2. 双层缓存架构

```
┌─────────────────────────────────────────────────────────────┐
│                    图像缓存系统                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  L1: 内存缓存                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Map<imageId, filePath>                              │   │
│  │ - 即时访问 (O(1))                                   │   │
│  │ - LRU 驱逐 (max 200)                                │   │
│  │ - 进程内共享                                        │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│                    异步写入                                 │
│                          ▼                                  │
│  L2: 磁盘存储                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ~/.claude/image-cache/<sessionId>/<imageId>.png     │   │
│  │ - 安全权限 (0o600)                                  │   │
│  │ - 会话隔离                                          │   │
│  │ - 自动清理（旧会话）                                │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3. 原生加速对比

| 方法 | 冷启动 | 热启动 | 平台 |
|------|--------|--------|------|
| 原生 NSPasteboard | ~5ms | <1ms | macOS |
| osascript | ~1.5s | ~1.5s | macOS |
| xclip/wl-paste | ~100ms | ~50ms | Linux |
| PowerShell | ~200ms | ~100ms | Windows |

### 4. 错误恢复策略

```
┌─────────────────────────────────────────────────────────────┐
│                    错误处理流程                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Sharp 处理失败                                             │
│       │                                                     │
│       ▼                                                     │
│  ┌────────────────┐                                        │
│  │ 错误分类        │ → 记录分析事件                         │
│  └────────┬───────┘                                        │
│           │                                                 │
│           ▼                                                 │
│  ┌────────────────────────────────────────────┐            │
│  │ Base64 大小 ≤ 5MB 且 尺寸 ≤ 2000x2000？    │            │
│  └────────────────────┬───────────────────────┘            │
│              是       │      否                             │
│              │        │                                     │
│              ▼        ▼                                     │
│        返回原图    抛出用户友好错误                         │
│        (无压缩)   (建议手动调整)                            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 总结

### 核心文件索引

```
src/
├── constants/
│   └── apiLimits.ts           # API 限制常量
├── utils/
│   ├── imagePaste.ts          # 剪贴板集成
│   ├── imageResizer.ts        # 压缩与缩放
│   ├── imageStore.ts          # 会话存储
│   └── imageValidation.ts     # API 验证
└── tools/FileReadTool/
    └── imageProcessor.ts      # Sharp 抽象层
```

### 关键设计决策

1. **Base64 大小限制**：API 强制 5MB Base64（≈3.75MB 原始），而非尺寸限制
2. **渐进式降级**：从 PNG 无损到 JPEG 有损，最后缩小尺寸
3. **原生加速**：macOS 上使用 NSPasteboard，比 osascript 快 300 倍
4. **会话隔离**：每个会话独立的图像缓存目录
5. **LRU 驱逐**：内存中最多保留 200 个图像引用
6. **格式兼容**：自动将 BMP 转换为 PNG（WSL2 兼容）

### 与其他系统的集成

- **FileReadTool**：读取图像文件时使用相同的压缩管道
- **消息系统**：图像作为 ContentBlock 嵌入用户消息
- **权限系统**：图像粘贴可通过键绑定触发（chat:imagePaste）
- **错误处理**：ImageSizeError 和 ImageResizeError 集成到错误恢复流程
