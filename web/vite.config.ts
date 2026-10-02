import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

// pin I12：两个服务、两个端口。只读面（datasets/experiments/health）与操作面
// （update-jobs）都自挂 /api/v1，路径段不重叠但前缀重叠，所以必须两条规则，
// 且更具体的 update-jobs 在前——Vite 按 proxy 键的插入顺序取首个匹配。
const READ_API_TARGET = process.env.STOCK_API_TARGET ?? "http://127.0.0.1:8321";
const OPS_API_TARGET = process.env.STOCK_OPS_API_TARGET ?? "http://127.0.0.1:8642";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api/v1/update-jobs": {
        target: OPS_API_TARGET,
        changeOrigin: false,
      },
      // STOCK_API_TARGET 只能指向一个源（pin I12）：默认指向只读服务，
      // 操作服务另有 STOCK_OPS_API_TARGET，二者不可合并成一个变量。
      "/api": {
        target: READ_API_TARGET,
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: "happy-dom",
    include: ["tests/**/*.spec.ts"],
  },
});
