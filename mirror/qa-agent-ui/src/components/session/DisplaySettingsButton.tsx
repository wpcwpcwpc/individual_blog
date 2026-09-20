import { Tooltip } from "@/components/ui/Tooltip";
import { useState, useRef, useEffect } from 'react';
import { Settings2, RotateCcw } from 'lucide-react';
import { cn } from '@/utils/cn';
import { useDisplayStore, FONT_SIZE_MAP, LINE_HEIGHT_MAP, CODE_FONT_OPTIONS, type FontSizePreset, type LineHeightPreset } from '@/store/display';
export function DisplaySettingsButton() {
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node) && btnRef.current && !btnRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);
  return <div className="relative">
      <Tooltip tip="显示设置"><button ref={btnRef} onClick={() => setOpen(v => !v)} className={cn('p-1.5 rounded transition-colors', open ? 'text-violet-400 bg-violet-500/10' : 'text-slate-500 hover:text-slate-300 hover:bg-slate-500/10')}>
        <Settings2 className="w-4 h-4" />
      </button></Tooltip>

      {open && <div ref={panelRef} className="absolute right-0 top-full mt-2 z-50 w-72 rounded-lg border border-[hsl(217.2_32.6%_17.5%)] bg-[hsl(222.2_84%_6%)] shadow-xl">
          <DisplaySettingsPanel />
        </div>}
    </div>;
}
function DisplaySettingsPanel() {
  const fontSize = useDisplayStore(s => s.fontSize);
  const lineHeight = useDisplayStore(s => s.lineHeight);
  const codeFont = useDisplayStore(s => s.codeFont);
  const setFontSize = useDisplayStore(s => s.setFontSize);
  const setLineHeight = useDisplayStore(s => s.setLineHeight);
  const setCodeFont = useDisplayStore(s => s.setCodeFont);
  const reset = useDisplayStore(s => s.reset);
  const fontSizeKeys = Object.keys(FONT_SIZE_MAP) as FontSizePreset[];
  const lineHeightKeys = Object.keys(LINE_HEIGHT_MAP) as LineHeightPreset[];
  return <div className="p-4 space-y-4">
      {/* Title */}
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">显示设置</h3>
        <Tooltip tip="恢复默认"><button onClick={reset} className="flex items-center gap-1 text-[10px] text-slate-500 hover:text-slate-300 transition-colors">
          <RotateCcw className="w-3 h-3" />
          重置
        </button></Tooltip>
      </div>

      {/* Font size */}
      <SettingRow label="字体大小">
        <div className="flex gap-1">
          {fontSizeKeys.map(key => <button key={key} onClick={() => setFontSize(key)} className={cn('px-3 py-1 rounded text-xs transition-colors', fontSize === key ? 'bg-violet-500/20 text-violet-300 border border-violet-500/40' : 'bg-slate-700/30 text-slate-400 border border-transparent hover:bg-slate-700/50 hover:text-slate-300')}>
              {FONT_SIZE_MAP[key].label}
            </button>)}
        </div>
      </SettingRow>

      {/* Line height */}
      <SettingRow label="行间距">
        <div className="flex gap-1">
          {lineHeightKeys.map(key => <button key={key} onClick={() => setLineHeight(key)} className={cn('px-3 py-1 rounded text-xs transition-colors', lineHeight === key ? 'bg-violet-500/20 text-violet-300 border border-violet-500/40' : 'bg-slate-700/30 text-slate-400 border border-transparent hover:bg-slate-700/50 hover:text-slate-300')}>
              {LINE_HEIGHT_MAP[key].label}
            </button>)}
        </div>
      </SettingRow>

      {/* Code font */}
      <SettingRow label="代码字体">
        <select value={codeFont} onChange={e => setCodeFont(e.target.value)} className="w-full text-xs bg-slate-700/30 border border-[hsl(217.2_32.6%_22%)] rounded px-2 py-1.5 text-slate-300 outline-none focus:border-violet-500/50">
          {CODE_FONT_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
        </select>
      </SettingRow>

      {/* Preview */}
      <div className="pt-2 border-t border-[hsl(217.2_32.6%_15%)]">
        <p className="text-[10px] text-slate-600 mb-2">预览</p>
        <div className="md-content rounded-md bg-[hsl(217.2_32.6%_10%)] border border-[hsl(217.2_32.6%_17.5%)] p-3">
          <p className="!m-0 text-slate-300" style={{
          fontSize: 'var(--md-font-size)',
          lineHeight: 'var(--md-line-height)'
        }}>
            这是一段<strong>示例文字</strong>，包含 <code>inline code</code> 样式。
          </p>
        </div>
      </div>
    </div>;
}
function SettingRow({
  label,
  children
}: {
  label: string;
  children: React.ReactNode;
}) {
  return <div className="space-y-1.5">
      <label className="text-[11px] text-slate-500 font-medium">{label}</label>
      {children}
    </div>;
}
