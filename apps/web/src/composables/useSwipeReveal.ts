// FRONTEND-186：左滑露出動作按鈕的手勢（Pointer Events 一套涵蓋滑鼠與觸控）。
// 移植 ivy FE:src/composables/useSwipeReveal.ts::useSwipeReveal：鬆手只切換開合，
// 業務動作（例如取消接送）由呼叫端在露出按鈕的 click handler 觸發，避免誤觸。
//
// 用法（卡片元件內）：
//   const { dragX, isOpen, onPointerDown, onPointerMove, onPointerUp, onPointerCancel, close } =
//     useSwipeReveal({ disabled: () => readonly })
//   // 按鈕 click：emit('cancel', item); close()
import { ref, type Ref } from 'vue'

/** 露出按鈕的預設寬度（px），與消費端按鈕 CSS 寬度對齊 */
const DEFAULT_REVEAL_WIDTH = 84
/** 拖曳距離 / revealWidth 達此比例鬆手才開啟，否則回彈關閉 */
const DEFAULT_OPEN_THRESHOLD_RATIO = 0.45
/** 方向尚未判定前，垂直位移超過此值且大於水平位移即視為捲動 */
const SCROLL_SLOP_PX = 10

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches === true
  )
}

export interface SwipeRevealOptions {
  /** 可傳 getter：露出的按鈕數量會隨 props 變化時，每次讀取都拿當下寬度 */
  revealWidth?: number | (() => number)
  openThresholdRatio?: number
  /** 回 true 時忽略所有手勢（唯讀使用者、已結束的卡片） */
  disabled?: () => boolean
}

export function useSwipeReveal(opts: SwipeRevealOptions = {}): {
  dragX: Ref<number>
  dragging: Ref<boolean>
  isOpen: Ref<boolean>
  /** true＝回彈不做過場（使用者偏好 reduced motion），消費端綁定 CSS transition 用 */
  reboundInstant: Ref<boolean>
  onPointerDown(e: PointerEvent): void
  onPointerMove(e: PointerEvent): void
  onPointerUp(e: PointerEvent): void
  onPointerCancel(e: PointerEvent): void
  close(): void
} {
  const {
    revealWidth = DEFAULT_REVEAL_WIDTH,
    openThresholdRatio = DEFAULT_OPEN_THRESHOLD_RATIO,
    disabled = () => false,
  } = opts

  const dragX = ref(0)
  const dragging = ref(false)
  const isOpen = ref(false)
  const reboundInstant = ref(false)

  let startX = 0
  let startY = 0
  let baseOffset = 0
  let activePointerId: number | null = null
  let horizontalLocked = false

  function currentRevealWidth(): number {
    return typeof revealWidth === 'function' ? revealWidth() : revealWidth
  }

  function restingX(): number {
    return isOpen.value ? -currentRevealWidth() : 0
  }

  function isActivePointer(e: PointerEvent): boolean {
    return dragging.value && e.pointerId === activePointerId
  }

  function releaseCapture(e: PointerEvent): void {
    const target = e.currentTarget
    if (target instanceof HTMLElement) target.releasePointerCapture?.(e.pointerId)
  }

  /** 結束本次手勢、不改變 isOpen，dragX 回到目前開合狀態的位置 */
  function abandon(e: PointerEvent): void {
    releaseCapture(e)
    dragging.value = false
    activePointerId = null
    dragX.value = restingX()
  }

  function onPointerDown(e: PointerEvent): void {
    if (disabled()) return
    // 已有另一根手指在拖曳：忽略新的 pointerdown，避免劫持進行中的手勢
    if (dragging.value && e.pointerId !== activePointerId) return

    dragging.value = true
    startX = e.clientX
    startY = e.clientY
    baseOffset = restingX()
    activePointerId = e.pointerId
    horizontalLocked = false
    reboundInstant.value = prefersReducedMotion()

    const target = e.currentTarget
    if (target instanceof HTMLElement) target.setPointerCapture?.(e.pointerId)
  }

  function onPointerMove(e: PointerEvent): void {
    if (!isActivePointer(e)) return
    if (disabled()) {
      abandon(e)
      return
    }
    const dx = e.clientX - startX
    const dy = e.clientY - startY
    if (!horizontalLocked) {
      if (Math.abs(dy) > SCROLL_SLOP_PX && Math.abs(dy) > Math.abs(dx)) {
        abandon(e)
        return
      }
      if (Math.abs(dx) > SCROLL_SLOP_PX) horizontalLocked = true
    }
    dragX.value = Math.min(0, Math.max(-currentRevealWidth(), baseOffset + dx))
  }

  function onPointerUp(e: PointerEvent): void {
    if (!isActivePointer(e)) return
    if (disabled()) {
      abandon(e)
      return
    }
    isOpen.value = Math.abs(dragX.value) / currentRevealWidth() >= openThresholdRatio
    abandon(e)
  }

  /** pointer 被系統取消（例如瀏覽器接管捲動）：維持鬆手前的開合狀態 */
  function onPointerCancel(e: PointerEvent): void {
    if (!isActivePointer(e)) return
    abandon(e)
  }

  /** 供消費端在業務動作完成後主動收合 */
  function close(): void {
    isOpen.value = false
    dragX.value = 0
  }

  return {
    dragX,
    dragging,
    isOpen,
    reboundInstant,
    onPointerDown,
    onPointerMove,
    onPointerUp,
    onPointerCancel,
    close,
  }
}
