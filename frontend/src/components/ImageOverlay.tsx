import {useEffect, useRef, type ReactNode} from 'react'

export function Overlay({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    box.current?.querySelector<HTMLButtonElement>('button')?.focus()
    return () => previous?.focus()
  }, [])
  return <div className="overlay" onClick={event => { if (event.target === event.currentTarget) onClose() }}>
    <div ref={box} className="overlay-panel" role="dialog" aria-modal="true" aria-label={title} onKeyDown={event => {
      if (event.key === 'Escape') onClose()
      if (event.key === 'Tab') {
        const nodes = box.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input')
        if (!nodes?.length) return
        const first = nodes[0], last = nodes[nodes.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }}><div className="section-head"><h3>{title}</h3><button type="button" onClick={onClose}>关闭</button></div>{children}</div>
  </div>
}
