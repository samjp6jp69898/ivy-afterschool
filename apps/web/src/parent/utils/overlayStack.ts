/**
 * 家長端疊層（ParentBottomSheet、ConfirmDialog）共用的堆疊、body 捲動鎖與焦點還原。
 * 只有最上層處理 Esc 與焦點鎖；捲動鎖由第一個疊層記錄 body 原值、最後一個還原。
 * 焦點還原：移除最上層時還原到它開啟前的元素；移除非最上層（例如同一 tick 先關下層）時不動焦點，
 * 並把其上疊層「開啟前元素在被移除疊層內」的還原目標改成被移除疊層自己的還原目標，
 * 讓整串疊層關完後焦點回到最底層開啟前的元素，不受宣告順序影響。
 */
interface Entry {
  id: symbol
  prevFocus: HTMLElement | null
  root: () => HTMLElement | null
}

const stack: Entry[] = []
let scrollLockPrev = ''

export function pushOverlay(id: symbol, root: () => HTMLElement | null = () => null): void {
  if (stack.some((e) => e.id === id)) return
  if (stack.length === 0) {
    scrollLockPrev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
  const active = document.activeElement
  stack.push({ id, prevFocus: active instanceof HTMLElement ? active : null, root })
}

export function removeOverlay(id: symbol): void {
  const index = stack.findIndex((e) => e.id === id)
  if (index < 0) return
  const [entry] = stack.splice(index, 1)
  if (!entry) return
  if (index < stack.length) {
    const root = entry.root()
    for (const above of stack.slice(index)) {
      if (root && above.prevFocus && root.contains(above.prevFocus)) above.prevFocus = entry.prevFocus
    }
  } else if (entry.prevFocus?.isConnected) {
    entry.prevFocus.focus({ preventScroll: true })
  }
  if (stack.length === 0) document.body.style.overflow = scrollLockPrev
}

export function isTopOverlay(id: symbol): boolean {
  return stack[stack.length - 1]?.id === id
}
