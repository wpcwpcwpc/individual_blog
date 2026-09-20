import { Tooltip } from "@/components/ui/Tooltip";
import { useRef, useEffect, useState, useCallback, forwardRef, useImperativeHandle } from 'react';
import { Check } from 'lucide-react';
import type { SkillInfo } from '@/types/api';
interface SkillPickerProps {
  skills: SkillInfo[];
  selectedSkills: string[];
  searchQuery: string;
  onSelect: (name: string) => void;
  onClose: () => void;
}

/** Effort 圆点指示器 */
function EffortDots({
  effort
}: {
  effort: number;
}) {
  const color = effort <= 2 ? 'text-emerald-400' : effort <= 3 ? 'text-amber-400' : 'text-orange-400';
  return <Tooltip tip={`Effort: ${effort}/5`}><span className={`inline-flex gap-0.5 ${color}`}>
      {Array.from({
        length: 5
      }, (_, i) => <span key={i} className="text-[8px] leading-none">
          {i < effort ? '●' : '○'}
        </span>)}
    </span></Tooltip>;
}

/** 模糊搜索过滤 */
function filterSkills(query: string, skills: SkillInfo[]): SkillInfo[] {
  if (!query) return skills;
  const q = query.toLowerCase();
  return skills.filter(s => s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q) || s.when_to_use.toLowerCase().includes(q));
}
const SkillPicker = forwardRef<HTMLDivElement, SkillPickerProps>(function SkillPicker({
  skills,
  selectedSkills,
  searchQuery,
  onSelect,
  onClose
}, forwardedRef) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const filtered = filterSkills(searchQuery, skills);

  // Reset highlight when filter changes
  useEffect(() => {
    setHighlightedIndex(0);
  }, [searchQuery]);

  // Click outside to close
  useEffect(() => {
    function handleMouseDown(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    }
    document.addEventListener('mousedown', handleMouseDown);
    return () => document.removeEventListener('mousedown', handleMouseDown);
  }, [onClose]);

  // Keyboard navigation — called from parent's onKeyDown
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (filtered.length === 0) {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
      return;
    }
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setHighlightedIndex(prev => (prev + 1) % filtered.length);
        break;
      case 'ArrowUp':
        e.preventDefault();
        setHighlightedIndex(prev => (prev - 1 + filtered.length) % filtered.length);
        break;
      case 'Enter':
        e.preventDefault();
        if (filtered[highlightedIndex]) {
          onSelect(filtered[highlightedIndex].name);
        }
        break;
      case 'Escape':
        e.preventDefault();
        onClose();
        break;
    }
  }, [filtered, highlightedIndex, onSelect, onClose]);

  // Expose handleKeyDown for parent component via ref
  useImperativeHandle(forwardedRef, () => {
    const el = panelRef.current!;
    (el as HTMLDivElement & {
      _onKeyDown?: typeof handleKeyDown;
    })._onKeyDown = handleKeyDown;
    return el;
  }, [handleKeyDown]);
  return <div ref={panelRef} className="absolute bottom-full left-0 right-0 mb-1 z-50
                 bg-[hsl(222.2_84%_6.5%)] border border-[hsl(217.2_32.6%_17.5%)]
                 rounded-lg shadow-xl shadow-black/50 overflow-hidden" style={{
    maxHeight: '320px'
  }}>
      {filtered.length === 0 ? <div className="px-3 py-4 text-center text-xs text-slate-500">
          无匹配的 Skill
        </div> : <div className="overflow-y-auto" style={{
      maxHeight: '320px'
    }}>
          {filtered.map((skill, index) => {
        const isSelected = selectedSkills.includes(skill.name);
        const isHighlighted = index === highlightedIndex;
        return <button key={skill.name} type="button" className={`w-full text-left px-3 py-2.5 transition-colors border-b border-[hsl(217.2_32.6%_14%)] last:border-b-0
                  ${isHighlighted ? 'bg-[hsl(217.2_32.6%_14%)]' : ''}
                  ${isSelected ? 'bg-violet-500/10' : ''}
                  hover:bg-[hsl(217.2_32.6%_14%)]`} onClick={() => onSelect(skill.name)} onMouseEnter={() => setHighlightedIndex(index)}>
                {/* Row 1: Name + Effort */}
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5">
                    {isSelected && <Check className="w-3 h-3 text-violet-400 flex-shrink-0" />}
                    <span className={`text-xs font-medium ${isSelected ? 'text-violet-300' : 'text-slate-200'}`}>
                      {skill.name}
                    </span>
                  </div>
                  <EffortDots effort={skill.effort} />
                </div>

                {/* Row 2: Description */}
                <p className="text-[11px] text-slate-500 mt-0.5 line-clamp-1">
                  {skill.description}
                </p>

                {/* Row 3: When to use */}
                {skill.when_to_use && <p className="text-[11px] text-slate-600 mt-0.5">
                    ▸ {skill.when_to_use}
                  </p>}
              </button>;
      })}
        </div>}
    </div>;
});
export default SkillPicker;
