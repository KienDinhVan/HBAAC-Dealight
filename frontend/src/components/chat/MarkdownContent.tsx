import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { cn } from '@/lib/utils'

interface MarkdownContentProps {
  content: string
  streaming?: boolean
  className?: string
}

export function MarkdownContent({ content, streaming = false, className }: MarkdownContentProps) {
  return (
    <div className={cn('markdown-body text-sm leading-relaxed', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Paragraphs
          p: ({ children }) => (
            <p className="mb-3 last:mb-0 text-inherit leading-relaxed">{children}</p>
          ),

          // Headings
          h1: ({ children }) => (
            <h1 className="text-base font-bold text-zinc-50 mt-5 mb-2 first:mt-0">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-sm font-bold text-zinc-100 mt-4 mb-1.5 first:mt-0">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-sm font-semibold text-zinc-200 mt-3 mb-1 first:mt-0">{children}</h3>
          ),

          // Lists
          ul: ({ children }) => (
            <ul className="mb-3 space-y-1 pl-4 list-none">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="mb-3 space-y-1 pl-4 list-decimal marker:text-zinc-500">{children}</ol>
          ),
          li: ({ children }) => (
            <li className="flex gap-2 text-inherit">
              <span className="mt-2 h-1 w-1 flex-shrink-0 rounded-full bg-zinc-500" />
              <span>{children}</span>
            </li>
          ),

          // Inline code
          code: ({ className: codeClass, children, ...props }) => {
            const isBlock = Boolean(codeClass)
            if (isBlock) {
              // strip the "language-" prefix for a cleaner look
              const lang = codeClass?.replace('language-', '') ?? ''
              return (
                <code className={cn('block font-mono text-xs text-emerald-300 leading-relaxed', codeClass)} {...props}>
                  {lang && (
                    <span className="block text-[10px] text-zinc-500 mb-2 uppercase tracking-widest select-none">
                      {lang}
                    </span>
                  )}
                  {children}
                </code>
              )
            }
            return (
              <code
                className="px-1 py-0.5 rounded bg-zinc-800 border border-zinc-700 font-mono text-xs text-amber-300"
                {...props}
              >
                {children}
              </code>
            )
          },

          // Code blocks
          pre: ({ children }) => (
            <pre className="mb-3 overflow-x-auto rounded-xl bg-zinc-950 border border-zinc-700/40 px-4 py-3 font-mono text-xs leading-relaxed">
              {children}
            </pre>
          ),

          // Blockquote
          blockquote: ({ children }) => (
            <blockquote className="mb-3 border-l-2 border-indigo-500/60 pl-3 text-zinc-400 italic">
              {children}
            </blockquote>
          ),

          // Bold / italic
          strong: ({ children }) => (
            <strong className="font-semibold text-zinc-50">{children}</strong>
          ),
          em: ({ children }) => (
            <em className="italic text-zinc-300">{children}</em>
          ),

          // Horizontal rule
          hr: () => <hr className="my-4 border-zinc-700" />,

          // Tables (GFM)
          table: ({ children }) => (
            <div className="mb-3 overflow-x-auto rounded-xl border border-zinc-700/60">
              <table className="w-full text-xs">{children}</table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-zinc-800/60">{children}</thead>
          ),
          tbody: ({ children }) => (
            <tbody className="divide-y divide-zinc-800">{children}</tbody>
          ),
          tr: ({ children }) => <tr>{children}</tr>,
          th: ({ children }) => (
            <th className="px-3 py-2 text-left font-semibold text-zinc-300 whitespace-nowrap">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="px-3 py-2 text-zinc-400 align-top">{children}</td>
          ),

          // Links
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-indigo-400 underline underline-offset-2 hover:text-indigo-300"
            >
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>

      {/* Blinking cursor while streaming */}
      {streaming && (
        <span className="inline-block w-px h-4 bg-zinc-300 ml-0.5 animate-blink align-middle" />
      )}
    </div>
  )
}
