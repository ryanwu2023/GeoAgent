export type ModelProvider = "disabled" | "deepseek" | "openai-compatible" | "pichat";
export type ModelConfig = {
  provider: ModelProvider;
  base_url: string;
  model: string;
  timeout_seconds: number;
  api_key_configured: boolean;
  configured: boolean;
  source: "saved" | "environment" | "none" | "error";
  protocol: string;
  config_error?: string;
};
export type ModelDraft = Pick<ModelConfig, "provider" | "base_url" | "model" | "timeout_seconds"> & { api_key: string };
export type ModelTest = {
  ok: boolean;
  model?: string;
  message: string;
  translation?: { ok: boolean; original: string; text: string; issues: string[] };
  source: "draft";
};
export type ModelRuntime = { translation?: { message?: string }; model?: { configured: boolean; model?: string } };
export type ModelSettingsState = {
  config: ModelConfig | null;
  draft: ModelDraft;
  runtime: ModelRuntime | null;
  phase: "loading" | "idle" | "testing" | "saving";
  requestId: number | null;
  result: ModelTest | null;
  notice: string;
  error: string;
};

export function providerDraft(provider: ModelProvider): ModelDraft {
  return {
    provider,
    base_url: provider === "deepseek" ? "https://api.deepseek.com" : "",
    model: provider === "deepseek" ? "deepseek-flash" : "",
    timeout_seconds: 90,
    api_key: "",
  };
}

export function publicModelDraft(config: ModelConfig): ModelDraft {
  // Explicitly pick public fields: a credential must never be hydrated from GET.
  return {
    provider: config.provider, base_url: config.base_url, model: config.model,
    timeout_seconds: config.timeout_seconds, api_key: "",
  };
}

export function normalizeModelBaseUrl(value: string, provider: ModelProvider): string {
  const invalid = "服务地址需为不含凭据、空白、查询参数或片段的 HTTP(S) API 地址。";
  if (/[\u0000-\u001f\u007f]/.test(value)) throw new Error(invalid);
  const raw = value.trim();
  if (raw.length > 1000 || /[?#\\\s]/.test(raw)) throw new Error(invalid);
  const parts = /^(https?):\/\/([^/]+)(\/.*)?$/i.exec(raw);
  if (!parts || parts[2].includes("@")) throw new Error(invalid);
  // Keep the original host/path while validating: URL() silently normalizes
  // dot segments, encoded hostnames and abbreviated IPv4, unlike our backend.
  const authority = /^(\[[^\]]+\]|[^:]+)(?::(\d+))?$/.exec(parts[2]);
  if (!authority) throw new Error(invalid);
  const scheme = parts[1].toLowerCase();
  const host = authority[1].replace(/^\[|\]$/g, "").toLowerCase();
  const port = authority[2] === undefined ? undefined : Number(authority[2]);
  if (!host || host.includes("%") || (port !== undefined && (port < 1 || port > 65535))) throw new Error(invalid);
  let parsed: URL;
  try { parsed = new URL(raw); } catch { throw new Error(invalid); }
  const ipv4 = host.split(".");
  const ipv4Loopback = ipv4.length === 4 && ipv4[0] === "127"
    && ipv4.every(part => /^(0|[1-9]\d{0,2})$/.test(part) && Number(part) <= 255);
  const loopback = host === "localhost" || ipv4Loopback
    || (host.includes(":") && parsed.hostname === "[::1]");
  if (scheme === "http" && !loopback) {
    throw new Error("非本机回环的模型服务必须使用 HTTPS，避免明文发送凭据。");
  }
  let path = (parts[3] || "").replace(/\/+$/, "");
  if (path.includes("%") || path.split("/").some(part => part === "." || part === "..")) {
    throw new Error("请输入明确的 API 基址，不使用编码或相对路径。");
  }
  if (provider === "pichat") {
    if (!["", "/api/v1", "/api/v1/chat"].includes(path)) {
      throw new Error("PiChat 请填写后台服务根地址或 /api/v1/chat，不是 /chat 网页地址。");
    }
    path = "";
  } else {
    if (path.endsWith("/chat/completions")) path = path.slice(0, -"/chat/completions".length);
    if (path.split("/").some(part => part === "chat" || part === "admin")) {
      throw new Error("请输入模型 API 基址，不是聊天网页或管理页面地址。");
    }
  }
  const hostname = host.includes(":") ? `[${host}]` : host;
  const defaultPort = scheme === "https" ? 443 : 80;
  return `${scheme}://${hostname}${port && port !== defaultPort ? `:${port}` : ""}${path}`;
}

export function canKeepModelKey(config: ModelConfig | null, draft: ModelDraft): boolean {
  if (!config?.api_key_configured || config.provider !== draft.provider || draft.provider === "disabled") return false;
  try {
    return normalizeModelBaseUrl(config.base_url, config.provider) === normalizeModelBaseUrl(draft.base_url, draft.provider);
  } catch {
    return false;
  }
}

export function modelDraftError(config: ModelConfig | null, draft: ModelDraft): string {
  if (draft.provider === "disabled") return "";
  try {
    normalizeModelBaseUrl(draft.base_url, draft.provider);
  } catch (error) {
    return error instanceof Error ? error.message : "请填写有效的模型 API 地址。";
  }
  if (draft.provider !== "pichat" && !draft.model.trim()) return "请明确填写模型名称。";
  if (!Number.isFinite(draft.timeout_seconds) || draft.timeout_seconds < 5 || draft.timeout_seconds > 300) return "超时秒数须为 5 到 300 之间的数值。";
  if (!draft.api_key.trim() && !canKeepModelKey(config, draft)) {
    return "当前提供方与地址没有可沿用的凭据，请输入新的 API Key。";
  }
  return "";
}

export const initialModelSettings: ModelSettingsState = {
  config: null, draft: providerDraft("disabled"), runtime: null, phase: "loading",
  requestId: null, result: null, notice: "", error: "",
};
type ModelAction =
  | { type: "loading"; requestId: number }
  | { type: "loaded"; requestId: number; config: ModelConfig; runtime: ModelRuntime | null }
  | { type: "edit"; patch: Partial<ModelDraft> }
  | { type: "provider"; provider: ModelProvider }
  | { type: "started"; requestId: number; operation: "testing" | "saving" }
  | { type: "tested"; requestId: number; result: ModelTest }
  | { type: "saved"; requestId: number; config: ModelConfig }
  | { type: "failed"; requestId: number; error: string };

export function modelSettingsReducer(state: ModelSettingsState, action: ModelAction): ModelSettingsState {
  switch (action.type) {
    case "loading":
      return { ...state, phase: "loading", requestId: action.requestId, result: null, notice: "", error: "" };
    case "loaded":
      if (state.requestId !== action.requestId) return state;
      return { ...state, config: action.config, draft: publicModelDraft(action.config), runtime: action.runtime,
        phase: "idle", requestId: null, result: null, error: "", notice: "" };
    case "provider":
      return { ...state, draft: providerDraft(action.provider), phase: "idle", requestId: null,
        result: null, notice: "", error: "" };
    case "edit": {
      const changedEndpoint = action.patch.base_url !== undefined && action.patch.base_url !== state.draft.base_url;
      return { ...state, draft: { ...state.draft, ...action.patch, ...(changedEndpoint ? { api_key: "" } : {}) },
        phase: "idle", requestId: null, result: null, notice: "", error: "" };
    }
    case "started":
      return { ...state, phase: action.operation, requestId: action.requestId, result: null, notice: "", error: "" };
    case "tested":
      if (state.requestId !== action.requestId || state.phase !== "testing") return state;
      return { ...state, result: action.result, phase: "idle", requestId: null, error: "" };
    case "saved":
      if (state.requestId !== action.requestId || state.phase !== "saving") return state;
      return { ...state, config: action.config, draft: publicModelDraft(action.config), result: null,
        phase: "idle", requestId: null, error: "",
        notice: action.config.provider === "disabled" ? "后台模型已禁用；不会启动批量翻译。" : "已保存并立即生效，未验证；保存不会自动启动批量翻译。" };
    case "failed":
      if (state.requestId !== action.requestId) return state;
      return { ...state, phase: "idle", requestId: null, error: action.error };
  }
}
