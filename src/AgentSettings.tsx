import { useEffect, useState } from "react";
import { sessionFetch as fetch } from "./sessionFetch";
import { draftToPayload, settingsToDraft, validateAgentSettings, type AgentSettings as AgentConfig, type AgentSettingsDraft } from "./agent_settings";

async function requestAgentSettings(method = "GET", body?: AgentConfig): Promise<AgentConfig> {
  const response = await fetch("/api/qa-agent/settings", {
    method,
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `Agent 配置请求失败（HTTP ${response.status}）`);
  return data as AgentConfig;
}

export default function AgentSettings({ onSaved }: { onSaved: () => void }) {
  const [draft, setDraft] = useState<AgentSettingsDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function load() {
    setBusy(true); setError(""); setMessage("");
    try { setDraft(settingsToDraft(await requestAgentSettings())); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Agent 配置读取失败"); }
    finally { setBusy(false); }
  }

  useEffect(() => { void load(); }, []);
  const validation = draft ? validateAgentSettings(draft) : "";
  const edit = (field: keyof AgentSettingsDraft, value: string) => draft && setDraft({ ...draft, [field]: value });

  return <div className="agent-settings">
    <h3>智能问答 Agent</h3>
    <p>控制本地模型问答的子 Agent 并行数、两级超时和单次问答估算费用。只影响本地 Agent 链路，不改变 PiChat。</p>
    <div className="integration-unlock"><button disabled={busy} onClick={() => void load()}>重新载入配置</button></div>
    {error ? <p role="alert">{error}</p> : null}
    {!draft ? <p role="status">{busy ? "正在读取 Agent 配置…" : "尚未读取到配置。"}</p> : <>
      <div className="integration-fields">
        <label>单个子 Agent 超时（秒）
          <input type="number" min="5" max="300" step="1" value={draft.per_agent_timeout_seconds} disabled={busy}
            onChange={event => edit("per_agent_timeout_seconds", event.target.value)}/>
        </label>
        <label>多 Agent 总超时（秒）
          <input type="number" min="10" max="900" step="1" value={draft.total_timeout_seconds} disabled={busy}
            onChange={event => edit("total_timeout_seconds", event.target.value)}/>
        </label>
        <label>最大并行子 Agent 数
          <input type="number" min="1" max="3" step="1" value={draft.max_parallel} disabled={busy}
            onChange={event => edit("max_parallel", event.target.value)}/>
        </label>
        <label>输入价格（人民币／百万 Token）
          <input type="number" min="0" step="0.01" placeholder="留空不估算" value={draft.input_price_per_million} disabled={busy}
            onChange={event => edit("input_price_per_million", event.target.value)}/>
        </label>
        <label>输出价格（人民币／百万 Token）
          <input type="number" min="0" step="0.01" placeholder="留空不估算" value={draft.output_price_per_million} disabled={busy}
            onChange={event => edit("output_price_per_million", event.target.value)}/>
        </label>
        <label>单次问答费用上限（人民币）
          <input type="number" min="0.01" step="0.01" placeholder="留空表示无限制" value={draft.max_cost_cny} disabled={busy}
            onChange={event => edit("max_cost_cny", event.target.value)}/>
        </label>
      </div>
      <p>费用由输入、输出 Token 数和所填单价估算，不代表服务商账单。设置上限后，系统会在并发调用前预留预算；预算不足时跳过后续子 Agent，但仍生成最终回答。</p>
      <p>留空费用上限表示不限制；若填写上限，输入和输出单价都必须填写，单价可为 0。</p>
      {validation ? <p className="model-validation" role="status">{validation}</p> : null}
      <div className="integration-unlock">
        <button className="primary" disabled={busy || Boolean(validation)} onClick={() => void (async () => {
          if (!draft || validation) return;
          setBusy(true); setError(""); setMessage("");
          try {
            const saved = await requestAgentSettings("PUT", draftToPayload(draft));
            setDraft(settingsToDraft(saved));
            setMessage("智能问答 Agent 配置已保存并立即用于后续本地问答。");
            onSaved();
          } catch (reason) { setError(reason instanceof Error ? reason.message : "Agent 配置保存失败"); }
          finally { setBusy(false); }
        })()}>{busy ? "正在保存…" : "保存 Agent 配置"}</button>
      </div>
      {message ? <p role="status">{message}</p> : null}
    </>}
  </div>;
}
