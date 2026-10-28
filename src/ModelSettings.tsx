import {sessionFetch as fetch} from "./sessionFetch";
import { useEffect, useReducer, useRef } from "react";
import {
  canKeepModelKey, initialModelSettings, modelDraftError, modelSettingsReducer,
  type ModelConfig, type ModelDraft, type ModelProvider, type ModelRuntime, type ModelSettingsState, type ModelTest,
} from "./model_settings";

async function modelRequest<T>(path: string, method: string, signal: AbortSignal, draft?: ModelDraft): Promise<T> {
  const response = await fetch(path, {
    method, signal, cache: "no-store", headers: { "Content-Type": "application/json" },
    body: draft ? JSON.stringify(draft) : undefined,
  });
  const data = await response.json().catch(() => { throw new Error(`后台模型服务返回非 JSON 响应（HTTP ${response.status}）`); });
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `后台模型请求失败（HTTP ${response.status}）`);
  return data;
}

type FormProps = {
  state: ModelSettingsState;
  onEdit: (patch: Partial<ModelDraft>) => void;
  onProvider: (provider: ModelProvider) => void;
  onReload: () => void;
  onTest: () => void;
  onSave: () => void;
};

export function ModelSettingsForm({ state, onEdit, onProvider, onReload, onTest, onSave }: FormProps) {
  const { config, draft, phase, result } = state;
  const locked = phase === "loading" || phase === "saving";
  const pending = locked || phase === "testing";
  const keepKey = canKeepModelKey(config, draft);
  const validation = modelDraftError(config, draft);
  const enabled = draft.provider !== "disabled";
  const sources = { saved: "项目保存配置", environment: "后端环境变量", none: "无配置", error: "配置读取失败（未生效）" };
  return <div className="model-settings">
    <h3>后台模型（翻译/摘要）</h3>
    <p>由 GeoAgent 后端调用，用于中文翻译、页面摘要及模型分析。PiChat 嵌入问答与这里的后台凭据独立，能对话不代表翻译已配置。</p>
    <div className="integration-unlock model-actions">
      <button disabled={pending} onClick={onReload}>重新载入已保存配置（丢弃草稿）</button>
    </div>
    {phase === "loading" ? <p role="status">正在读取后台模型配置…</p> : null}
    {state.error ? <p role="alert">{state.error}</p> : null}
    {config ? <>
      {config.config_error ? <p role="alert">{config.config_error}</p> : null}
      <p className="model-config-status">当前生效：{config.config_error ? "模型配置不可用，其他项目功能可继续使用" : config.provider === "disabled" ? "后台模型已禁用" : config.configured ? "已配置（不代表已验证）" : "未配置完整"}
        {" · "}{sources[config.source]}{" · "}协议：{config.protocol || "无"}</p>
      <div className="integration-fields">
        <label>模型提供方
          <select aria-label="模型提供方" value={draft.provider} disabled={locked} onChange={event => onProvider(event.target.value as ModelProvider)}>
            <option value="disabled">禁用后台模型</option>
            <option value="deepseek">DeepSeek（官方 API）</option>
            <option value="openai-compatible">OpenAI 兼容 API（自定义）</option>
            <option value="pichat">PiChat 后台 API</option>
          </select>
        </label>
        {enabled ? <label>超时（秒）
          <input type="number" min="5" max="300" step="1" value={draft.timeout_seconds} disabled={locked}
            onChange={event => onEdit({ timeout_seconds: Number(event.target.value) })}/>
        </label> : null}
        {enabled ? <label className="model-wide-field">{draft.provider === "pichat" ? "PiChat 后台服务根地址" : "模型 API 地址"}
          <input type="url" value={draft.base_url} disabled={locked} autoComplete="off"
            placeholder={draft.provider === "pichat" ? "https://pichat.example.com（不含 /chat）" : "https://api.example.com/v1"}
            onChange={event => onEdit({ base_url: event.target.value })}/>
        </label> : null}
        {enabled && draft.provider !== "pichat" ? <label>模型名称（手动指定）
          <input value={draft.model} disabled={locked} placeholder="填写服务端支持的模型 ID"
            onChange={event => onEdit({ model: event.target.value })}/>
        </label> : null}
        {enabled ? <label>后台模型 API Key
          <input type="password" autoComplete="new-password" value={draft.api_key} disabled={locked}
            placeholder={keepKey ? "此提供方和地址已有凭据；留空保留" : "请输入新凭据（不会沿用其他地址的 Key）"}
            onChange={event => { const key = event.target.value; onEdit({ api_key: key }); }}/>
        </label> : null}
      </div>
      {draft.provider === "disabled" ? <p>保存后明确禁用后台模型。不会影响 PiChat 嵌入会话和对外 MCP，也不会执行模型测试或批量翻译。</p> : <>
        <p>密钥仅提交到 GeoAgent 后端保存；公开配置不返回密钥，不写入浏览器本地存储。切换提供方或地址会清除输入中的 Key；空 Key 仅在与当前已保存提供方和地址一致时保留旧凭据。</p>
        {draft.provider === "deepseek" ? <p>DeepSeek 预设：<code>https://api.deepseek.com</code> · <code>deepseek-flash</code> · 90 秒；翻译采用非思考调用。模型名称可手动修改，未执行模型自动发现。<a href="https://api-docs.deepseek.com/" target="_blank" rel="noreferrer">官方 API 文档</a></p> : null}
        {draft.provider === "openai-compatible" ? <p>请根据服务商文档手动填写地址和模型；未自动发现或验证可用模型。</p> : null}
        {draft.provider === "pichat" ? <p>使用 PiChat 后台根地址与具备后台调用权限的 Key，不是 /chat 页面，也不是嵌入专用 App Key。Agent 由 PiChat 应用权限路由，此处不选择模型。</p> : null}
        {validation ? <p className="model-validation" role="status">{validation}</p> : null}
        <p>测试草稿会真实调用一次小样本翻译，可能消耗额度；不保存配置、不启动批量。保存只让配置立即生效，不做测试，也不自动批量翻译。</p>
        <p>现有自动翻译流程仍会在应用重启或导入新快照时尝试处理最新快照；启用模型后，请留意后续批量调用的额度。</p>
      </>}
      <div className="integration-unlock model-actions">
        <button disabled={pending || !enabled || Boolean(validation)} onClick={onTest}>
          {phase === "testing" ? "正在测试草稿…" : "测试草稿（小样本翻译）"}
        </button>
        <button className="primary" disabled={pending || Boolean(validation)} onClick={onSave}>
          {phase === "saving" ? "正在保存…" : draft.provider === "disabled" ? "保存并禁用后台模型" : "保存后台模型配置"}
        </button>
      </div>
      {phase === "testing" ? <p role="status">正在测试当前草稿。编辑任何字段会使这次测试结果失效，不会自动保存。</p> : null}
      {result ? <section className="model-test-result" aria-label="草稿翻译测试结果">
        <p role="status">{result.ok && result.translation?.ok ? "草稿小样本翻译通过（仅测试，不修改配置）" : "草稿测试未通过"}
          ：{result.message}{result.model ? ` · 本次模型：${result.model}` : ""}</p>
        {result.translation ? <>
          <p><strong>测试原文：</strong>{result.translation.original}</p>
          <p><strong>译文：</strong>{result.translation.text || "未返回译文"}</p>
          {result.translation.issues?.length ? <ul>{result.translation.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul> : null}
        </> : null}
      </section> : null}
      {state.notice ? <p role="status">{state.notice}</p> : null}
      <p>打开或重新载入时的翻译状态：{state.runtime?.translation?.message || "未提供，不能据此判断正在运行或已经完成。"}</p>
      <p>此配置影响整个项目，沿用站点现有访问保护；不是个人浏览器设置。</p>
    </> : null}
  </div>;
}

export default function ModelSettings({ onSaved }: { onSaved: () => void }) {
  const [state, dispatch] = useReducer(modelSettingsReducer, initialModelSettings);
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const begin = () => {
    controller.current?.abort();
    controller.current = new AbortController();
    return { requestId: ++sequence.current, signal: controller.current.signal };
  };

  async function reload() {
    const { requestId, signal } = begin();
    dispatch({ type: "loading", requestId });
    try {
      const [config, runtime] = await Promise.all([
        modelRequest<ModelConfig>("/api/integrations/model", "GET", signal),
        modelRequest<ModelRuntime>("/api/status", "GET", signal).catch(() => null),
      ]);
      if (!signal.aborted) dispatch({ type: "loaded", requestId, config, runtime });
    } catch (error) {
      if (!signal.aborted) dispatch({ type: "failed", requestId, error: error instanceof Error ? error.message : "后台模型配置读取失败" });
    }
  }

  useEffect(() => {
    void reload();
    return () => controller.current?.abort();
  }, []);

  function edit(patch: Partial<ModelDraft>) {
    controller.current?.abort();
    dispatch({ type: "edit", patch });
  }

  async function execute(operation: "testing" | "saving") {
    if (modelDraftError(state.config, state.draft) || (operation === "testing" && state.draft.provider === "disabled")) return;
    const { requestId, signal } = begin();
    const draft = { ...state.draft, model: state.draft.provider === "pichat" ? "" : state.draft.model };
    dispatch({ type: "started", requestId, operation });
    try {
      if (operation === "testing") {
        const result = await modelRequest<ModelTest>("/api/integrations/model/test", "POST", signal, draft);
        if (!signal.aborted) dispatch({ type: "tested", requestId, result });
      } else {
        const config = await modelRequest<ModelConfig>("/api/integrations/model", "PUT", signal, draft);
        if (!signal.aborted) {
          dispatch({ type: "saved", requestId, config });
          onSaved();
        }
      }
    } catch (error) {
      if (!signal.aborted) {
        const message = error instanceof Error ? error.message : "后台模型请求失败";
        dispatch({ type: "failed", requestId, error: draft.api_key ? message.split(draft.api_key).join("[已隐藏]") : message });
      }
    }
  }

  return <ModelSettingsForm state={state} onEdit={edit}
    onProvider={provider => { controller.current?.abort(); dispatch({ type: "provider", provider }); }}
    onReload={() => void reload()} onTest={() => void execute("testing")} onSave={() => void execute("saving")}/>;
}
