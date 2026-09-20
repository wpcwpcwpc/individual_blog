import { useWorkspaceStore } from '@/store/workspace'
import { FileTreeNode } from './FileTreeNode'

interface FileTreeProps {
  sessionId: string
}

export function FileTree({ sessionId }: FileTreeProps) {
  const ws = useWorkspaceStore((s) => s.workspaces[sessionId])
  const rootEntries = ws?.childrenMap['']

  if (!rootEntries) return null

  // Sort: dirs first, then files, alphabetical within each group
  const sorted = [...rootEntries].sort((a, b) => {
    if (a.type !== b.type) return a.type === 'dir' ? -1 : 1
    return a.name.localeCompare(b.name)
  })

  return (
    <div className="flex-1 overflow-y-auto py-1">
      {sorted.map((entry) => (
        <FileTreeNode
          key={entry.path}
          entry={entry}
          sessionId={sessionId}
          depth={0}
        />
      ))}
    </div>
  )
}