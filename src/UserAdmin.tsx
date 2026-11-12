import {sessionFetch as fetch} from "./sessionFetch";
import {dateText} from "./api";
import { useEffect, useState } from "react";

type User = { username: string; phone: string; pichat_user_id: string | null; created_by: string | null; created_at: string; updated_by: string | null; updated_at: string | null };
type Listing = { admins: string[]; users: User[] };

async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`/api/auth/users${path}`, {
    method,
    // The login header marks the request as coming from our own page.
    headers: { "Content-Type": "application/json", "X-GeoAgent-Login": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `用户管理请求失败（HTTP ${response.status}）`);
  return data;
}

const time = (value: string) => dateText(value);

export default function UserAdmin({ onClose }: { onClose: () => void }) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [phone, setPhone] = useState("");
  const [editing, setEditing] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function action(fn: () => Promise<string | void>) {
    setBusy(true);
    setMessage("");
    try {
      const done = await fn();
      setListing(await request<Listing>(""));
      if (done) setMessage(done);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "请求失败");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { void action(async () => {}); }, []);

  return <div className="integration-overlay">
    <section className="integration-dialog" role="dialog" aria-modal="true" aria-label="用户管理">
      <header>
        <h2>用户管理</h2>
        <button onClick={onClose} aria-label="关闭用户管理">×</button>
      </header>
      <p>所有用户的研究功能相同，只有管理员能创建、改密和删除用户。不填密码时，初始密码与账号相同；管理员改密不需要原密码，改密或删除后该用户需重新登录。</p>
      <p>创建时会同步在 PiChat 建立该用户的独立身份，右侧智能问答按人区分；PiChat 同步失败则不创建。手机号不填时自动生成 +999 开头的占位号码。</p>

      <h3>创建用户</h3>
      <form className="integration-unlock" onSubmit={(event) => {
        event.preventDefault();
        void action(async () => {
          const result = await request<{ username: string; default_password: boolean }>("", "POST", { username: username.trim(), password, phone });
          setUsername("");
          setPassword("");
          setPhone("");
          return `已创建 ${result.username} 并同步到 PiChat${result.default_password ? "，初始密码与账号相同" : ""}`;
        });
      }}>
        <label>账号<input value={username} maxLength={64} required autoComplete="off" placeholder="2-64 位字母、数字或 _.@-" onChange={(event) => setUsername(event.target.value)}/></label>
        <label>密码<input type="password" value={password} maxLength={1024} autoComplete="new-password" placeholder="留空则与账号相同" onChange={(event) => setPassword(event.target.value)}/></label>
        <label>手机号<input value={phone} maxLength={40} autoComplete="off" placeholder="选填，留空自动生成" onChange={(event) => setPhone(event.target.value)}/></label>
        <button className="primary" disabled={busy || username.trim().length < 2}>{busy ? "创建中…" : "创建用户"}</button>
      </form>

      <h3>用户列表</h3>
      {listing ? <ul className="integration-keys">
        {listing.admins.map((name) => <li key={`admin-${name}`}>
          <span>{name}<small>管理员 · 由部署配置管理，不能在页面修改</small></span>
        </li>)}
        {listing.users.map((user) => <li key={user.username}>
          <span>{user.username}<small>普通用户 · {user.phone.startsWith("+999") ? "占位手机号" : user.phone} · PiChat {user.pichat_user_id ?? "未同步"} · {user.created_by ?? "未知"} 创建于 {time(user.created_at)}{user.updated_at ? ` · ${user.updated_by ?? "未知"} 修改于 ${time(user.updated_at)}` : ""}</small></span>
          {editing === user.username ? <form className="integration-unlock" onSubmit={(event) => {
            event.preventDefault();
            void action(async () => {
              const result = await request<{ default_password: boolean }>(`/${encodeURIComponent(user.username)}/password`, "PUT", { password: newPassword });
              setEditing("");
              setNewPassword("");
              return `${user.username} 的密码已修改${result.default_password ? "为与账号相同" : ""}`;
            });
          }}>
            <input type="password" aria-label={`${user.username} 的新密码`} value={newPassword} maxLength={1024} autoComplete="new-password" placeholder="留空则重置为账号" autoFocus onChange={(event) => setNewPassword(event.target.value)}/>
            <button className="primary" disabled={busy}>保存</button>
            <button type="button" disabled={busy} onClick={() => { setEditing(""); setNewPassword(""); }}>取消</button>
          </form> : <span className="integration-unlock">
            <button disabled={busy} onClick={() => { setEditing(user.username); setNewPassword(""); setMessage(""); }}>修改密码</button>
            <button disabled={busy} onClick={() => {
              if (window.confirm(`删除用户 ${user.username}？该用户将立即退出且无法再登录。`)) {
                void action(async () => {
                  await request(`/${encodeURIComponent(user.username)}`, "DELETE");
                  return `已删除 ${user.username}`;
                });
              }
            }}>删除</button>
          </span>}
        </li>)}
        {!listing.users.length ? <li><span><small>还没有普通用户</small></span></li> : null}
      </ul> : <p>{busy ? "正在读取用户…" : "用户列表尚未加载。"}</p>}
      {message ? <p role="status">{message}</p> : null}
    </section>
  </div>;
}
