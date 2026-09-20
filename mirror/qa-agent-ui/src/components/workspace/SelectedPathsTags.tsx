import { Folder, File, X } from 'lucide-react'
import { useWorkspaceStore } from '@/store/workspace'
import type { TreeEntry } from '@/types/api'

interface SelectedPathsTagsProps {
  sessionId: string
}

export function SelectedPathsTags({ sessionId }: SelectedPathsTagsProps) {
  const ws = useWorkspaceStore((s) => s.workspaces[sessionId])
  const removeSelect = useWorkspaceStore((s) => s.removeSelect)

  const selectedPaths = ws?.selectedPaths ?? []
  if (selectedPaths.length === 0) return null

  // Determine type of each path from childrenMap
  const getType = (path: string): TreeEntry['type'] => {
    if (!ws) return 'file'
    for (const entries of Object.values(ws.childrenMap)) {
      const found = entries.find((e) => e.path === path)
      if (found) return found.type
    }
    return 'file'
  }

  return (
    <div className="flex items-center gap-1.5 px-3 py-1.5 overflow-x-auto border-b border-[hsl(217.2_32.6%_17.5%)]">
      {selectedPaths.map((path) => {
        const type = getType(path)
        return (
          <span
            key={path}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-violet-500/15 text-violet-300 border border-violet-500/20 whitespace-nowrap flex-shrink-0"
          >
            {type === 'dir'
              ? <Folder className="w-3 h-3 text-amber-400/70" />
              : <File className="w-3 h-3 text-gray-400" />
            }
            <span className="max-w-[180px] truncate">{path}</span>
            <button
              onClick={() => removeSelect(sessionId, path)}
              className="ml-0.5 p-0.5 rounded-full hover:bg-white/10 text-violet-400 hover:text-violet-200"
            >
              <X className="w-3 h-3" />
            </button>
          </span>
        )
      })}
    </div>
  )
}