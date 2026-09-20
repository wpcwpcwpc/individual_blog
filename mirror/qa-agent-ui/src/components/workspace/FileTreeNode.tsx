import { useCallback } from 'react'
import { ChevronRight, ChevronDown, Folder, File, Loader2 } from 'lucide-react'
import { cn } from '@/utils/cn'
import type { TreeEntry } from '@/types/api'
import { useWorkspaceStore } from '@/store/workspace'
import { getWorkspaceTree } from '@/api/workspace'

interface FileTreeNodeProps {
  entry: TreeEntry
  sessionId: string
  depth: number
}

export function FileTreeNode({ entry, sessionId, depth }: FileTreeNodeProps) {
  const ws = useWorkspaceStore((s) => s.workspaces[sessionId])
  const toggleExpand = useWorkspaceStore((s) => s.toggleExpand)
  const setChildren = useWorkspaceStore((s) => s.setChildren)
  const setLoading = useWorkspaceStore((s) => s.setLoading)
  const toggleSelect = useWorkspaceStore((s) => s.toggleSelect)

  const isDir = entry.type === 'dir'
  const isExpanded = ws?.expandedPaths.has(entry.path) ?? false
  const isLoading = ws?.loadingPaths.has(entry.path) ?? false
  const isSelected = ws?.selectedPaths.includes(entry.path) ?? false
  const children = ws?.childrenMap[entry.path]

  // Sort children: dirs first, then files, alphabetical within each group
  const sortedChildren = children
    ? [...children].sort((a, b) => {
        if (a.type !== b.type) return a.type === 'dir' ? -1 : 1
        return a.name.localeCompare(b.name)
      })
    : undefined

  const handleToggleExpand = useCallback(async () => {
    if (!isDir) return

    if (isExpanded) {
      // Collapse
      toggleExpand(sessionId, entry.path)
      return
    }

    // Expand — check cache first
    if (children !== undefined) {
      toggleExpand(sessionId, entry.path)
      return
    }

    // Need to load
    setLoading(sessionId, entry.path, true)
    try {
      const entries = await getWorkspaceTree(sessionId, entry.path)
      setChildren(sessionId, entry.path, entries)
      toggleExpand(sessionId, entry.path)
    } catch {
      // Loading failed — don't expand
    } finally {
      setLoading(sessionId, entry.path, false)
    }
  }, [isDir, isExpanded, children, sessionId, entry.path, toggleExpand, setChildren, setLoading])

  const handleCheckbox = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      toggleSelect(sessionId, entry.path)
    },
    [sessionId, entry.path, toggleSelect],
  )

  return (
    <div>
      {/* Node row */}
      <div
        className={cn(
          'flex items-center h-7 px-1 rounded-sm cursor-pointer select-none',
          'hover:bg-white/5 transition-colors',
        )}
        style={{ paddingLeft: `${depth * 16 + 4}px` }}
      >
        {/* Checkbox */}
        <div
          className="flex-shrink-0 mr-1 cursor-pointer"
          onClick={handleCheckbox}
        >
          <input
            type="checkbox"
            checked={isSelected}
            readOnly
            className="h-3.5 w-3.5 rounded border-gray-500 bg-transparent accent-violet-500 cursor-pointer"
          />
        </div>

        {/* Expand arrow / spacer (only for dirs) */}
        <div
          className="flex-shrink-0 w-4 h-4 flex items-center justify-center mr-0.5"
          onClick={handleToggleExpand}
        >
          {isDir && !isLoading && (
            isExpanded
              ? <ChevronDown className="w-3.5 h-3.5 text-gray-400" />
              : <ChevronRight className="w-3.5 h-3.5 text-gray-400" />
          )}
          {isDir && isLoading && (
            <Loader2 className="w-3.5 h-3.5 text-gray-400 animate-spin" />
          )}
        </div>

        {/* Icon */}
        <div className="flex-shrink-0 mr-1.5" onClick={handleToggleExpand}>
          {isDir
            ? <Folder className="w-4 h-4 text-amber-400/80" />
            : <File className="w-4 h-4 text-gray-400" />
          }
        </div>

        {/* Name */}
        <span
          className="truncate text-xs text-gray-300"
          onClick={handleToggleExpand}
        >
          {entry.name}
        </span>
      </div>

      {/* Children (recursive) */}
      {isDir && isExpanded && (
        <div>
          {sortedChildren && sortedChildren.length > 0 ? (
            sortedChildren.map((child) => (
              <FileTreeNode
                key={child.path}
                entry={child}
                sessionId={sessionId}
                depth={depth + 1}
              />
            ))
          ) : (
            children !== undefined && children.length === 0 && (
              <div
                className="text-xs text-gray-500 italic"
                style={{ paddingLeft: `${(depth + 1) * 16 + 28}px` }}
              >
                空目录
              </div>
            )
          )}
        </div>
      )}
    </div>
  )
}