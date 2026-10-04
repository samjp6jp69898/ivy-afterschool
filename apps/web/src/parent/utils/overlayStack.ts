/**
 * 家長端疊層（ParentBottomSheet、ConfirmDialog）共用的堆疊與 body 捲動鎖。
 * 只有最上層處理 Esc 與焦點鎖；捲動鎖計數跨實例共享，第一個記錄 body 原值、最後一個還原。
 */
const stack: symbol[] = []
let scrollLockPrev = ''

export function pushOverlay(id: symbol): void {
  if (stack.includes(id)) return
  if (stack.length === 0) {
    scrollLockPrev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
  stack.push(id)
}

export function removeOverlay(id: symbol): void {
  const index = stack.indexOf(id)
  if (index < 0) return
  stack.splice(index, 1)
  if (stack.length === 0) document.body.style.overflow = scrollLockPrev
}

export function isTopOverlay(id: symbol): boolean {
  return stack[stack.length - 1] === id
}
