/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

// https://vitejs.dev/config/
export default defineConfig(({ command, mode }) => {
  const isProd = command === 'build' && mode !== 'development'

  return {
    base: './',
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      }
    },

    plugins: [
      vue({
        template: {
          compilerOptions: {
            // 优化编译性能
            hoistStatic: true,
            cacheHandlers: true
          }
        }
      }),
      tailwindcss()
    ],
    clearScreen: false,
    server: {
      port: 1420,
      strictPort: true,
      watch: {
        ignored: ['**/src-tauri/**'],
      },
      // 配置 /data 路径服务 feature 图片等静态资源
      fs: {
        allow: ['..']
      }
    },
    esbuild: {
      // 生产构建时移除所有 console.* 调用和 debugger 语句
      drop: isProd ? ['console', 'debugger'] : [],
      pure: isProd ? ['console.log', 'console.info', 'console.debug', 'console.trace'] : [],
      legalComments: 'none',
    },
    build: {
      // Tauri 环境优化
      target: 'esnext',
      minify: 'esbuild',
      // 减少代码分割来提升加载速度
      rollupOptions: {
        output: {
          manualChunks: {
            'vue-vendor': ['vue', 'vue-router'],
            'primevue-vendor': ['primevue']
          }
        }
      },
      // 减少 chunk 大小警告阈值
      chunkSizeWarningLimit: 1000,
      // 优化资源内联
      assetsInlineLimit: 4096
    },
    optimizeDeps: {
      include: ['vue', 'vue-router', 'primevue']
    },
    test: {
      environment: 'node',
      globals: true,
    },
  }
})

