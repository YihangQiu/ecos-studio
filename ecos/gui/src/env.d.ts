/// <reference types="vite/client" />

declare const __ECOS_GUI_ROOT__: string

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}
