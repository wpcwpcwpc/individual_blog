import { X } from 'lucide-react'

interface SkillTagsProps {
  skills: string[]
  onRemove?: (name: string) => void
  /** 'input' = 可删除标签（输入框区域），'bubble' = 只读标签（消息气泡） */
  mode?: 'input' | 'bubble'
}

export default function SkillTags({ skills, onRemove, mode = 'input' }: SkillTagsProps) {
  if (!skills.length) return null

  return (
    <div className={`flex items-center gap-1.5 overflow-x-auto flex-wrap ${
      mode === 'input' ? 'px-3 py-1.5 border-b border-[hsl(217.2_32.6%_17.5%)]' : 'gap-y-1'
    }`}>
      {skills.map(name => (
        <span
          key={name}
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs whitespace-nowrap flex-shrink-0 border ${
            mode === 'bubble'
              ? 'bg-blue-500/10 text-blue-300 border-blue-500/20'
              : 'bg-violet-500/15 text-violet-300 border-violet-500/20'
          }`}
        >
          <span className="text-[10px]">{mode === 'bubble' ? '📊' : '🏷️'}</span>
          <span className="max-w-[180px] truncate">{name}</span>
          {mode === 'input' && onRemove && (
            <button
              type="button"
              onClick={() => onRemove(name)}
              className="ml-0.5 p-0.5 rounded-full hover:bg-white/10 text-violet-400 hover:text-violet-200 transition-colors"
            >
              <X className="w-3 h-3" />
            </button>
          )}
        </span>
      ))}
    </div>
  )
}