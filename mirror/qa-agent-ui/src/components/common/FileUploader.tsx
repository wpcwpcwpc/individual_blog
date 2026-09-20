import { Tooltip } from "@/components/ui/Tooltip";
import { useRef, useState } from 'react';
import { Paperclip, X, Loader2, File as FileIcon, AlertTriangle, Plus } from 'lucide-react';
import type { UploadedFileRef } from '@/types/api';
import { uploadFile, deleteUpload } from '@/api/sessionUploads';
import { convertImageToText } from '@/api/vision';
import { cn } from '@/utils/cn';

interface Props {
  /** Session id the upload is scoped to. Files are isolated per session.
   *  When null AND onStageFiles is provided, the component defers upload:
   *  validates + stages the File locally (no session created, no network).
   *  The parent uploads it after creating the session ("开始生成"). */
  sessionId?: string | null;
  /** Currently selected uploaded file (null = none). Immediate mode only. */
  value?: UploadedFileRef | null;
  /** Change callback (immediate mode): notifies parent of new upload /
   *  removal / clear. Optional in staged mode (use onStageFiles instead). */
  onChange?: (file: UploadedFileRef | null) => void;
  /** Disable interactions (e.g. while agent is running). */
  disabled?: boolean;
  /** Currently selected model name. Kept for backward compat with callers
   *  (RefineChat/MessageInputBar); no longer used for image upload gating
   *  — image conversion uses an independent VISION slot via /vision/convert,
   *  so the session agent model is irrelevant. */
  selectedModel?: string;
  /** Staged files (deferred mode). Held by the parent so they can be uploaded
   *  after session creation. */
  stagedFiles?: File[];
  /** Staged-mode change callback. When provided AND sessionId is null,
   *  file selection validates + calls this instead of uploading. */
  onStageFiles?: (files: File[]) => void;
  /** Busy state notification (for disabling parent submit during convert /
   *  upload). Fired true when this uploader starts work, false when done
   *  (success or failure). Parents use this to disable 开始生成 / send. */
  onBusyChange?: (busy: boolean) => void;
  /** Progress callback for multi-image serial convert. Fires (done, total)
   *  during image conversion only; text files do not fire this. */
  onProgress?: (done: number, total: number) => void;
  /** 紧凑模式（输入框工具栏内嵌）：单行 h-6 控件，与模型下拉同规格
   *  （h-6 text-[11px] bordered）；label 与格式说明收进 Tooltip。默认 false（表单竖排布局）。 */
  compact?: boolean;
}

/** Max single-file size (MB). MUST match backend settings.upload_max_size_mb. */
const MAX_SIZE_MB = 1.0;
const MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024;

/** Max staged files (multi-file batch upload). Mirror backend MAX_BATCH_FILES. */
const MAX_STAGED_FILES = 5;

/** Allowed upload extensions (mirror backend ALLOWED_EXTENSIONS for non-image,
 *  plus image extensions which are routed through /vision/convert first). */
const ALLOWED_EXT = new Set(['txt', 'md', 'csv', 'xlsx', 'docx', 'png', 'jpg', 'jpeg', 'gif', 'webp']);
const FORBIDDEN_EXT = new Set(['xmind']); // xmind explicitly unsupported
const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp']);
function getExt(filename: string): string {
  return filename.slice(filename.lastIndexOf('.') + 1).toLowerCase();
}
/** Whether the given filename is an image (vision model image-to-text path). */
function isImageFile(name: string | undefined): boolean {
  if (!name) return false;
  return IMAGE_EXT.has(getExt(name));
}

interface ConvertFailure {
  file: File;
  error: string;
}

/** Common FileUploader — multi-file staged upload with format/size
 *  pre-validation and image-to-text conversion via /vision/convert.
 *
 *  Two modes:
 *  - Immediate (sessionId set): upload on select, delete hits the server.
 *    Still single-file (backward compat for MessageInputBar / RefineChat).
 *  - Staged (sessionId null + onStageFiles wired): validate + hold the File[]
 *    locally; the parent uploads it after creating the session. Supports
 *    multi-file (≤5), single-select-append pattern.
 *
 *  Images (png/jpg/jpeg/gif/webp): on select, call /vision/convert SERIALLY
 *  (not Promise.all — VISION slot may rate-limit + 30-60s per image). On
 *  success wrap returned text as .txt File and append to stagedFiles. On
 *  failure show interactive modal: cancel all / retry / skip. */
export function FileUploader({
  sessionId,
  value,
  onChange,
  disabled,
  stagedFiles,
  onStageFiles,
  onBusyChange,
  onProgress,
  compact
}: Props) {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [convertFailure, setConvertFailure] = useState<ConvertFailure | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const stagedMode = !sessionId && !!onStageFiles;

  const stagedList = stagedFiles ?? [];
  const atLimit = stagedList.length >= MAX_STAGED_FILES;

  const runConvert = async (file: File): Promise<File> => {
    // Caller owns busy state (setUploading + onBusyChange) since multi-file
    // selection may call runConvert in a loop and we don't want each call to
    // toggle busy off then back on between images.
    setProgress({ done: 0, total: 1 });
    onProgress?.(0, 1);
    try {
      const result = await convertImageToText(file);
      onProgress?.(1, 1);
      setProgress({ done: 1, total: 1 });
      const txtBlob = new Blob([result.text], { type: 'text/plain' });
      return new File([txtBlob], result.suggested_filename, {
        type: 'text/plain',
        lastModified: Date.now(),
      });
    } finally {
      // Progress reset is caller-controlled too; we just clear our own.
      // setUploading/onBusyChange owned by caller.
    }
  };

  const appendStaged = (file: File) => {
    onStageFiles?.([...stagedList, file]);
  };

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    // CRITICAL: snapshot FileList into a real array BEFORE clearing the input.
    // FileList is a live object — clearing input.value empties it, so reading
    // it afterwards yields length 0 and the handler silently no-ops.
    const pickedFiles: File[] = e.target.files ? Array.from(e.target.files) : [];
    if (inputRef.current) inputRef.current.value = '';
    if (pickedFiles.length === 0) return;
    setError('');

    // Hard stop: total would exceed MAX_STAGED_FILES even before processing.
    if (stagedList.length + pickedFiles.length > MAX_STAGED_FILES) {
      setError(
        `最多上传 ${MAX_STAGED_FILES} 个文件（当前 ${stagedList.length} 个，本次选择 ${pickedFiles.length} 个）`
      );
      return;
    }

    // Pass 1: validate every picked file (ext + size). If any fails, stop
    // before doing any convert/upload — atomic at the selection level so the
    // user gets one clear error instead of a partial batch with one failure.
    for (const f of pickedFiles) {
      const ext = getExt(f.name);
      if (FORBIDDEN_EXT.has(ext)) {
        setError(`xmind 格式暂不支持上传（${f.name}）`);
        return;
      }
      if (!ALLOWED_EXT.has(ext)) {
        setError(`文件格式不支持: .${ext}（${f.name}）`);
        return;
      }
      if (f.size > MAX_SIZE_BYTES) {
        setError(`文件大小超过上限 ${MAX_SIZE_MB}MB（${f.name}）`);
        return;
      }
    }

    // Pass 2: split into images (serial convert) vs text (direct append).
    const imageFiles = pickedFiles.filter(f => IMAGE_EXT.has(getExt(f.name)));
    const textFiles = pickedFiles.filter(f => !IMAGE_EXT.has(getExt(f.name)));

    // Text files: append all at once.
    if (textFiles.length > 0 && stagedMode) {
      onStageFiles?.([...stagedList, ...textFiles]);
    }
    // Immediate mode (single-file backward compat) for non-staged path:
    // only take the first text file (images handled below). Immediate mode
    // callers (MessageInputBar/RefineChat) are single-file by design.
    if (textFiles.length > 0 && !stagedMode) {
      const targetSid = sessionId ?? null;
      if (!targetSid) {
        setError('会话未就绪，无法上传');
        onChange?.(null);
        return;
      }
      setUploading(true);
      onBusyChange?.(true);
      try {
        const result = await uploadFile(targetSid, textFiles[0]);
        onChange?.(result);
      } catch (err: unknown) {
        const msg = (err as {
          response?: { data?: { detail?: string } };
          message?: string;
        })?.response?.data?.detail ?? (err as { message?: string })?.message ?? '上传失败';
        setError(typeof msg === 'string' ? msg : '上传失败');
        onChange?.(null);
      } finally {
        setUploading(false);
        onBusyChange?.(false);
      }
    }

    // Images: serial convert (VISION slot may rate-limit + 30-60s per image,
    // not Promise.all). Each success appends a .txt File to stagedFiles.
    // Use a local accumulator so multiple image converts in one selection
    // don't overwrite each other before state settles.
    if (imageFiles.length === 0) return;
    if (!stagedMode) {
      setError('图片仅支持暂存模式上传（用例生成页）');
      return;
    }
    const baseStaged = [...stagedList, ...textFiles];
    const convertedTxtFiles: File[] = [];
    for (let i = 0; i < imageFiles.length; i++) {
      const f = imageFiles[i];
      // Live progress: (done=i, total=imageFiles.length).
      setProgress({ done: i, total: imageFiles.length });
      onProgress?.(i, imageFiles.length);
      setUploading(true);
      onBusyChange?.(true);
      try {
        const txtFile = await runConvert(f);
        convertedTxtFiles.push(txtFile);
        // Reflect the just-completed convert in progress UI.
        setProgress({ done: i + 1, total: imageFiles.length });
        onProgress?.(i + 1, imageFiles.length);
        // Append progressively so user sees chips fill in.
        onStageFiles?.([...baseStaged, ...convertedTxtFiles]);
      } catch (err: unknown) {
        // Serial convert surfaced one failure — hand off to interactive
        // modal. The user can cancel / retry / skip; subsequent images in
        // this selection are NOT processed until modal resolves. Skip
        //; retry stays on this file; cancel aborts all.
        const msg = (err as {
          response?: { data?: { detail?: string } };
          message?: string;
        })?.response?.data?.detail ?? (err as { message?: string })?.message ?? '图片解析失败';
        setConvertFailure({ file: f, error: typeof msg === 'string' ? msg : '图片解析失败' });
        // Persist already-converted ones (they are real .txt Files, no reason
        // to discard them) and stop here — the modal will decide next step.
        onStageFiles?.([...baseStaged, ...convertedTxtFiles]);
        // Note: we do NOT call setUploading(false)/onBusyChange(false) here
        // because the modal is now the active UI; handleRetry/Skip/CancelAll
        // will reset busy state when they complete.
        return;
      } finally {
        setUploading(false);
        onBusyChange?.(false);
      }
    }
    // All images converted successfully. Reset progress.
    setProgress(null);
  };

  const handleRetry = async () => {
    if (!convertFailure) return;
    const { file } = convertFailure;
    setConvertFailure(null);
    setUploading(true);
    onBusyChange?.(true);
    try {
      const txtFile = await runConvert(file);
      appendStaged(txtFile);
      setProgress(null);
    } catch (err: unknown) {
      const msg = (err as {
        response?: { data?: { detail?: string } };
        message?: string;
      })?.response?.data?.detail ?? (err as { message?: string })?.message ?? '图片解析失败';
      setConvertFailure({ file, error: typeof msg === 'string' ? msg : '图片解析失败' });
    } finally {
      setUploading(false);
      onBusyChange?.(false);
    }
  };

  const handleSkip = () => {
    setConvertFailure(null);
  };

  const handleCancelAll = () => {
    setConvertFailure(null);
    onStageFiles?.([]);
  };

  const handleDeleteStaged = (idx: number) => {
    onStageFiles?.(stagedList.filter((_, i) => i !== idx));
    setError('');
  };

  const handleDeleteImmediate = async () => {
    if (!value) return;
    const targetSid = sessionId ?? null;
    if (!targetSid) {
      onChange?.(null);
      setError('');
      return;
    }
    setUploading(true);
    onBusyChange?.(true);
    try {
      await deleteUpload(targetSid, value.file_id);
      onChange?.(null);
      setError('');
    } catch (err: unknown) {
      const msg = (err as {
        response?: { data?: { detail?: string } };
        message?: string;
      })?.response?.data?.detail ?? (err as { message?: string })?.message ?? '删除失败';
      setError(typeof msg === 'string' ? msg : '删除失败');
    } finally {
      setUploading(false);
      onBusyChange?.(false);
    }
  };

  const triggerPicker = () => {
    if (disabled || uploading || atLimit) return;
    inputRef.current?.click();
  };

  // Determine loading text. Only image convert shows progress; immediate
  // upload shows generic "上传中...".
  const loadingText = uploading
    ? (progress
        ? `图片 ${progress.done}/${progress.total} 解析中...`
        : (isImageFile(stagedList.at(-1)?.name) ? '图片解析中...' : '上传中...'))
    : '';

  // ── Compact mode（输入框工具栏内嵌）────
  // 与模型下拉同规格（h-6 text-[11px] bordered），
  // 已选文件渲染为内联 chip（文件名 + 删除），说明文案收进 Tooltip。
  if (compact) {
    const hint = `上传文件（可选，≤${MAX_SIZE_MB}MB，≤${MAX_STAGED_FILES} 个）· 支持 txt/md/csv/xlsx/docx/图片`;
    const errorStyle = { borderColor: 'var(--error-color, #ef4444)' }; // hex: error 回退色，同文件既有约定
    const trigger = value ? (
      <div className={cn(
        'flex items-center gap-1 h-6 px-1.5 rounded text-[11px] border max-w-[190px] transition-colors',
      )} style={{
        backgroundColor: 'hsl(var(--card))',
        borderColor: 'hsl(var(--border))',
        ...(error ? errorStyle : {}),
      }}>
        {uploading
          ? <Loader2 className="w-3 h-3 animate-spin shrink-0" />
          : <FileIcon className="w-3 h-3 shrink-0 text-[hsl(var(--muted-foreground))]" />}
        <span className="truncate min-w-0 text-[hsl(var(--foreground))]">{value.name}</span>
        <Tooltip tip="删除上传文件"><button type="button" onClick={handleDeleteImmediate} disabled={uploading || disabled} className={cn(
          'shrink-0 p-0.5 rounded transition-colors hover:bg-[hsl(var(--accent))]',
          'disabled:opacity-40 disabled:cursor-not-allowed',
        )}>
          <X className="w-3 h-3 text-[hsl(var(--muted-foreground))]" />
        </button></Tooltip>
      </div>
    ) : (
      <button type="button" onClick={triggerPicker} disabled={disabled || uploading || atLimit} className={cn(
        'flex items-center gap-1 h-6 px-2 rounded text-[11px] border transition-colors',
        'hover:bg-[hsl(var(--accent))]',
        'disabled:opacity-40 disabled:cursor-not-allowed',
      )} style={{
        backgroundColor: 'hsl(var(--card))',
        borderColor: 'hsl(var(--border))',
        color: 'hsl(var(--muted-foreground))',
        ...(error ? errorStyle : {}),
      }}>
        {uploading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Paperclip className="w-3 h-3" />}
        <span className="select-none">{uploading ? loadingText : '上传'}</span>
      </button>
    );
    return <div className="flex items-center min-w-0">
      <input ref={inputRef} type="file" multiple className="hidden" onChange={handleFileSelect} disabled={disabled || uploading} />
      <Tooltip tip={error
        ? <span style={{ color: 'var(--error-color, #ef4444)' }}>{error}</span>
        : hint}>
        {trigger}
      </Tooltip>
    </div>;
  }

  return <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium" style={{
        color: 'var(--text-secondary)'
      }}>
          上传文件 <span className="text-[11px]" style={{
        color: 'var(--text-muted)'
        }}>
            (可选，≤{MAX_SIZE_MB}MB，≤{MAX_STAGED_FILES} 个)
          </span>
        </label>
        {stagedMode && stagedList.length > 0 && <span className="text-[11px] px-1.5 py-0.5 rounded font-mono" style={{
        backgroundColor: 'var(--surface-4)',
        color: 'var(--text-muted)'
      }}>
            待上传 {stagedList.length}
          </span>}
        {!stagedMode && value && <span className="text-[11px] px-1.5 py-0.5 rounded font-mono" style={{
        backgroundColor: 'var(--surface-4)',
        color: 'var(--text-muted)'
      }}>
            已传 1
          </span>}
      </div>

      {stagedMode && stagedList.length > 0 ? (
        // ── Multi-file staged chip list + 继续添加 ──
        <>
          <div className="space-y-1">
            {stagedList.map((f, i) => (
              <div key={`${f.name}-${i}`} className="flex items-center gap-2-1.5 rounded-md border" style={{
                backgroundColor: 'var(--surface-deep)',
                borderColor: 'var(--border-color)'
              }}>
                <FileIcon className="w-3.5 h-3.5 shrink-0" style={{
                  color: 'var(--text-muted)'
                }} />
                <Tooltip tip={f.name}><span className="flex-1 truncate text-xs" style={{
                  color: 'var(--text-primary)'
                }}>
                  {f.name}
                </span></Tooltip>
                <Tooltip tip="删除该文件"><button type="button" onClick={() => handleDeleteStaged(i)} disabled={uploading || disabled} className={cn('shrink-0 p-0.5 rounded transition-colors', 'hover:bg-[var(--surface-4)]', 'disabled:opacity-40 disabled:cursor-not-allowed')} style={{
                  color: 'var(--text-muted)'
                }}>
                  {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <X className="w-3.5 h-3.5" />}
                </button></Tooltip>
              </div>
            ))}
          </div>
          <button type="button" onClick={triggerPicker} disabled={disabled || uploading || atLimit} className={cn('w-full flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-md border text-xs transition-colors', 'disabled:opacity-50 disabled:cursor-not-allowed', 'hover:border-violet-500/50')} style={{
            backgroundColor: 'var(--surface-deep)',
            borderColor: 'var(--border-color)',
            color: 'var(--text-muted)'
          }}>
            {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
            {atLimit ? `已达上限 ${MAX_STAGED_FILES} 个` : `继续添加（还剩 ${MAX_STAGED_FILES - stagedList.length} 个）`}
          </button>
        </>
      ) : !stagedMode && value ? (
        // ── Immediate-mode single file chip ──
        <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-md border" style={{
          backgroundColor: 'var(--surface-deep)',
          borderColor: 'var(--border-color)'
        }}>
          <FileIcon className="w-3.5 h-3.5 shrink-0" style={{
            color: 'var(--text-muted)'
          }} />
          <Tooltip tip={value.name}><span className="flex-1 truncate text-xs" style={{
            color: 'var(--text-primary)'
          }}>
            {value.name}
          </span></Tooltip>
          <Tooltip tip="删除上传文件"><button type="button" onClick={handleDeleteImmediate} disabled={uploading || disabled} className={cn('shrink-0 p-0.5 rounded transition-colors', 'hover:bg-[var(--surface-4)]', 'disabled:opacity-40 disabled:cursor-not-allowed')} style={{
            color: 'var(--text-muted)'
          }}>
            {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <X className="w-3.5 h-3.5" />}
          </button></Tooltip>
        </div>
      ) : (
        // ── Upload trigger ──
        <button type="button" onClick={triggerPicker} disabled={disabled || uploading || atLimit} className={cn('w-full flex items-center gap-2 px-3 py-2 rounded-md border text-sm transition-colors', 'disabled:opacity-50 disabled:cursor-not-allowed', 'hover:border-violet-500/50')} style={{
          backgroundColor: 'var(--surface-deep)',
          borderColor: 'var(--border-color)',
          color: 'var(--text-muted)'
        }}>
          {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Paperclip className="w-3.5 h-3.5" />}
          <span className="text-xs">
            {uploading ? loadingText : (atLimit ? `已达上限 ${MAX_STAGED_FILES} 个` : '支持 txt/md/csv/xlsx/docx/图片')}
          </span>
        </button>
      )}

      <input ref={inputRef} type="file" multiple className="hidden" onChange={handleFileSelect} disabled={disabled || uploading} />

      {error && <p className="text-[11px]" style={{
      color: 'var(--error-color, #ef4444)'
    }}>{error}</p>}

      {convertFailure && (
        // ── Image convert failure interactive modal ──
        // Lightweight inline modal (no shared Modal component in this repo).
        // Three actions: cancel all / retry / skip (D6 spec).
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ backgroundColor: 'rgba(0,0,0,0.4)' }} role="dialog" aria-modal="true">
          <div className="w-full max-w-sm rounded-md border p-4 mx-4 space-y-3" style={{
            backgroundColor: 'var(--surface-base, var(--surface-deep))',
            borderColor: 'var(--border-color)'
          }}>
            <div className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" style={{ color: 'var(--error-color, #ef4444)' }} />
              <div className="flex-1">
                <p className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
                  图片解析失败
                </p>
                <p className="text-xs mt-1 break-all" style={{ color: 'var(--text-secondary)' }}>
                  文件：{convertFailure.file.name}
                </p>
                <p className="text-xs mt-1 break-all" style={{ color: 'var(--error-color, #ef4444)' }}>
                  原因：{convertFailure.error}
                </p>
              </div>
            </div>
            <div className="flex gap-2 pt-1">
              <button type="button" onClick={handleCancelAll} className="flex-1 px-3 py-1.5 rounded-md text-xs transition-colors" style={{
                backgroundColor: 'var(--surface-4)',
                color: 'var(--text-muted)'
              }}>
                取消整次
              </button>
              <button type="button" onClick={handleSkip} className="flex-1 px-3 py-1.5 rounded-md text-xs transition-colors" style={{
                backgroundColor: 'var(--surface-4)',
                color: 'var(--text-primary)'
              }}>
                跳过该图
              </button>
              <button type="button" onClick={handleRetry} className="flex-1 px-3 py-1.5 rounded-md text-xs text-white transition-colors" style={{
                backgroundColor: 'var(--accent-color, #7c3aed)'
              }}>
                重试该图
              </button>
            </div>
          </div>
        </div>
      )}
    </div>;
}
