import { useEffect, useState } from "react";
import ModelSettings from "./ModelSettings";

type Config = {
  origin: string;
  agent_id: string;
  phone: string;
  entry: string;
  product_user_id: string;
  app_key_configured: boolean;
};
type Key = { id: string; label: string; created_at: string; revoked_at: string | null };
type Section = "embed" | "model" | "mcp";
const sections: { id: Section; title: string }[] = [
  { id: "embed", title: "PiChat 嵌入连接" },
  { id: "model", title: "后台模型（翻译/摘要）" },
  { id: "mcp", title: "GeoAgent 对外 MCP" },
];

async function fetchJson<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
    signal,
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(`配置服务返回非 JSON 响应（HTTP ${response.status}），请检查后端日志`);
  }
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : "配置请求失败");
  }
  return data;
}

function request<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal) {
  return fetchJson<T>(`/api/integrations${path}`, method, body, signal);
}

export default function IntegrationSettings({
  onClose,
  onSaved,
  initialSection = "embed",
}: {
  onClose: () => void;
  onSaved: () => void;
  initialSection?: "embed" | "model";
}) {
  const [section, setSection] = useState<Section>(initialSection);
  const [config, setConfig] = useState<Config | null>(null);
  const [secret, setSecret] = useState("");
  const [keys, setKeys] = useState<Key[]>([]);
  const [label, setLabel] = useState("PiChat GeoAgent");
  const [issued, setIssued] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try {
      await fn();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "请求失败");
    } finally {
      setBusy(false);
    }
  }

  async function load() {
    const [cfg, list] = await Promise.all([
      request<Config>(""),
      request<Key[]>("/mcp-keys"),
    ]);
    setConfig(cfg);
    setKeys(list);
  }

  useEffect(() => {
    setSection(initialSection);
  }, [initialSection]);

  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    void Promise.all([
      request<Config>("", "GET", undefined, controller.signal),
      request<Key[]>("/mcp-keys", "GET", undefined, controller.signal),
    ]).then(([cfg, list]) => {
      if (controller.signal.aborted) return;
      setConfig(cfg);
      setKeys(list);
    }).catch((error) => {
      if (!controller.signal.aborted) setMessage(error instanceof Error ? error.message : "配置读取失败");
    }).finally(() => {
      if (!controller.signal.aborted) setBusy(false);
    });
    return () => controller.abort();
  }, []);

  return <div className="integration-overlay">
    <section className="integration-dialog" role="dialog" aria-modal="true" aria-label="连接与 MCP 配置">
      <header>
        <h2>连接与 MCP 配置</h2>
        <button onClick={onClose} aria-label="关闭配置">×</button>
      </header>
      <p>三类连接各自独立：右侧问答的嵌入身份、GeoAgent 后台模型、供外部 Agent 读取的 MCP。</p>
      <div className="mcp-tabs" role="tablist" aria-label="连接类型">
        {sections.map((item, index) => <button
          key={item.id}
          id={`integration-tab-${item.id}`}
          role="tab"
          aria-selected={section === item.id}
          aria-controls={`integration-section-${item.id}`}
          tabIndex={section === item.id ? 0 : -1}
          onClick={() => { setSection(item.id); setMessage(""); }}
          onKeyDown={(event) => {
            const target = event.key === "ArrowRight" ? (index + 1) % sections.length
              : event.key === "ArrowLeft" ? (index + sections.length - 1) % sections.length
              : event.key === "Home" ? 0 : event.key === "End" ? sections.length - 1 : null;
            if (target === null) return;
            event.preventDefault();
            setSection(sections[target].id);
            setMessage("");
            document.getElementById(`integration-tab-${sections[target].id}`)?.focus();
          }}
        >{item.title}</button>)}
      </div>

      {section === "embed" ? <div id="integration-section-embed" role="tabpanel" aria-labelledby="integration-tab-embed">
        <h3>PiChat 嵌入连接</h3>
        <p>用于右侧智能问答的身份签发与会话连接。应用 Key 保存在后端，不是后台翻译/摘要模型的 API Key。</p>
        <p>此页配置保存后立即生效，无需重新发布；应用密钥不会下发到嵌入面板。</p>
        <div className="integration-unlock">
          <button disabled={busy} onClick={() => void action(load)}>刷新配置</button>
        </div>
        {config ? <>
          <div className="integration-fields">
            {([
              ["origin", "PiChat 服务地址"], ["agent_id", "Agent ID"],
              ["phone", "嵌入用户手机号"], ["entry", "产品入口 entry"],
              ["product_user_id", "产品用户 ID"],
            ] as const).map(([field, title]) => <label key={field}>{title}
              <input value={config[field]} onChange={(event) => setConfig({ ...config, [field]: event.target.value })}/>
            </label>)}
            <label>PiChat 应用 Key
              <input type="password" autoComplete="new-password" value={secret}
                onChange={(event) => setSecret(event.target.value)}
                placeholder={config.app_key_configured ? "已配置；留空保持原值" : "请输入 PiChat 签发的应用 Key"}/>
            </label>
          </div>
          <div className="integration-unlock">
            <button disabled={busy} className="primary" onClick={() => void action(async () => {
              setConfig(await request<Config>("/pichat", "PUT", { ...config, app_key: secret }));
              setSecret("");
              onSaved();
              setMessage("PiChat 配置已保存，嵌入面板将重新连接");
            })}>保存 PiChat 配置</button>
            <button disabled={busy} onClick={() => void action(async () => {
              const data = await fetchJson<{ token?: string; detail?: string }>("/api/pichat/embed-identity");
              if (!data.token) throw new Error(data.detail || "连接验证失败");
              setMessage("PiChat 身份签发验证通过；请关闭配置查看嵌入面板连接（使用已保存配置）");
            })}>验证 PiChat 连接</button>
          </div>
        </> : <p>{busy ? "正在读取嵌入配置…" : "嵌入配置尚未加载，请刷新重试。"}</p>}
      </div> : null}

      {section === "model" ? <div id="integration-section-model" role="tabpanel" aria-labelledby="integration-tab-model">
        <ModelSettings onSaved={onSaved}/>
      </div> : null}

      {section === "mcp" ? <div id="integration-section-mcp" role="tabpanel" aria-labelledby="integration-tab-mcp">
        <h3>GeoAgent 对外 MCP</h3>
        <p>供外部 Agent 只读查询本项目业务数据，不是接入外部新闻的 MCP 数据源。随项目启动 · Streamable HTTP。</p>
        <div className="integration-unlock">
          <button disabled={busy} onClick={() => void action(load)}>刷新 MCP Key 列表</button>
        </div>
        <label>MCP 地址<input readOnly value={`${window.location.origin}/mcp`}/></label>
        <p>在 Agent 中填写此地址，认证选择 Bearer Token。Key 授权读取本项目全部快照及业务工具，请只交给可信 Agent。</p>
        <div className="integration-unlock">
          <label>Key 名称<input value={label} onChange={(event) => setLabel(event.target.value)}/></label>
          <button disabled={busy || !config || !label.trim()} onClick={() => void action(async () => {
            const result = await request<{ key: string }>("/mcp-keys", "POST", { label });
            setIssued(result.key);
            setKeys(await request<Key[]>("/mcp-keys"));
          })}>创建 MCP Key</button>
        </div>
        {issued ? <div className="integration-issued">
          <strong>新 Key 仅显示一次，请立即保存</strong>
          <input aria-label="新 MCP Key" readOnly value={issued}/>
          <button onClick={() => setIssued("")}>已保存，隐藏</button>
        </div> : null}
        <ul className="integration-keys">{keys.map((key) => <li key={key.id}>
          <span>{key.label}<small>{key.id} · {key.revoked_at ? "已撤销" : "启用"}</small></span>
          {key.revoked_at ? <button disabled={busy} onClick={() => void action(async () => {
            await request(`/mcp-keys/${key.id}/record`, "DELETE");
            setKeys(await request<Key[]>("/mcp-keys"));
            setMessage("已从列表移除，后端保留撤销记录；不会恢复访问权限");
          })}>删除记录</button> : <button disabled={busy} onClick={() => {
            if (window.confirm(`撤销 ${key.label}？使用此 Key 的 Agent 将立即失去访问权限。`)) {
              void action(async () => {
                await request(`/mcp-keys/${key.id}`, "DELETE");
                setKeys(await request<Key[]>("/mcp-keys"));
                setIssued("");
              });
            }
          }}>撤销</button>}
        </li>)}</ul>
      </div> : null}
      {message ? <p role="status">{message}</p> : null}
    </section>
  </div>;
}
