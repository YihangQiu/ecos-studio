<template>
  <div class="chat-inspector-panel flex flex-col h-full w-full min-w-0 max-w-full bg-(--bg-primary) overflow-hidden">
    <div class="h-10 shrink-0 flex items-center gap-2 px-3 border-b border-(--border-color)">
      <button type="button" @click="activeTab = 'chat'" :class="tabClass(activeTab === 'chat')" title="AI Chat">
        <i class="ri-chat-3-line text-base"></i>
      </button>
      <button type="button" @click="activeTab = 'inspector'" :class="tabClass(activeTab === 'inspector')"
        title="Inspector">
        <i class="ri-layout-column-line text-base"></i>
      </button>
    </div>

    <div class="flex-1 min-h-0 overflow-hidden flex flex-col">
      <div v-if="activeTab === 'chat'" class="h-full min-h-0">
        <AIChatPanel />
      </div>

      <div v-else class="flex flex-col h-full min-h-0">
        <StepConfigPanel class="h-full min-h-0 flex-1 min-w-0 overflow-hidden" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import AIChatPanel from './AIChatPanel.vue'
import StepConfigPanel from './StepConfigPanel.vue'

const activeTab = ref<'chat' | 'inspector'>('chat')

function tabClass(active: boolean) {
  return [
    'h-8 w-9 rounded flex items-center justify-center transition-all cursor-pointer border',
    active
      ? 'text-(--accent-color) bg-(--accent-color)/20 border-(--accent-color)/50'
      : 'text-(--text-secondary) border-transparent hover:bg-(--bg-hover)',
  ]
}
</script>

<style scoped>
/* Do not use contain: size — it can zero out nested flex height and black out content */
.chat-inspector-panel {
  box-sizing: border-box;
}
</style>
