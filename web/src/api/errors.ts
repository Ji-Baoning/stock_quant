import { ApiError } from "./client";

export interface DisplayError {
  code: string;
  message: string;
}

/** 展示纪律（§10.2）：只输出稳定 error code 与安全摘要；不渲染堆栈/路径/环境。 */
export function toDisplayError(error: unknown): DisplayError {
  if (error instanceof ApiError) {
    return { code: error.code, message: error.safeMessage };
  }
  return { code: "unknown_error", message: "请求失败，无更多信息" };
}
