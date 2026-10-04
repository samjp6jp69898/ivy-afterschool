<script setup lang="ts">
/**
 * 加入官方帳號好友卡片。url 非 https:// 開頭（含 null / 空字串）一律不渲染任何元素；
 * 不偵測是否已加好友。顏色全用 --m3-*，不使用 LINE 綠。
 */
import { computed } from 'vue'
import M3Card from './m3/M3Card.vue'
import M3Icon from './m3/M3Icon.vue'

const props = defineProps<{ url: string | null }>()

const safeUrl = computed(() => (typeof props.url === 'string' && props.url.startsWith('https://') ? props.url : null))
</script>

<template>
  <M3Card
    v-if="safeUrl"
    class="add-friend"
    variant="outlined"
  >
    <div class="add-friend__head">
      <span
        class="add-friend__icon"
        aria-hidden="true"
      >
        <M3Icon name="chat" />
      </span>
      <div class="add-friend__text">
        <p class="m3-title-medium">
          加入官方帳號好友
        </p>
        <p class="add-friend__desc m3-body-medium">
          加入後才能收到 LINE 通知（到班、作業完成、接送回覆等）
        </p>
      </div>
    </div>
    <div class="add-friend__actions">
      <a
        class="add-friend__link m3-label-large"
        :href="safeUrl"
        target="_blank"
        rel="noopener noreferrer"
      >
        <M3Icon
          name="person_add"
          :size="18"
        />
        <span>加入好友</span>
      </a>
    </div>
  </M3Card>
</template>

<style scoped>
.add-friend__head {
  display: flex;
  align-items: flex-start;
  gap: 16px;
}

.add-friend__icon {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-primary-container);
  color: var(--m3-on-primary-container);
}

.add-friend__text {
  flex: 1;
  min-width: 0;
}

.add-friend__text p {
  margin: 0;
}

.add-friend__desc {
  margin-top: 4px !important;
  color: var(--m3-on-surface-variant);
}

.add-friend__actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.add-friend__link {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  box-sizing: border-box;
  min-height: 48px;
  padding: 0 24px 0 16px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
  text-decoration: none;
  -webkit-tap-highlight-color: transparent;
}

.add-friend__link:hover {
  filter: brightness(0.96);
}

.add-friend__link:focus-visible {
  outline: 2px solid var(--m3-primary);
  outline-offset: 2px;
}
</style>
