export const EMBED_IDENTITY_TIMEOUT_MS = 30_000;
export const EMBED_READY_TIMEOUT_MS = 60_000;
export const EMBED_SLOW_NOTICE_MS = 10_000;
export const EMBED_RETRY_DELAYS_MS = [2_000, 5_000] as const;

export class EmbedIdentityFailure extends Error {
  constructor(message: string, readonly retryable = false) { super(message); }
}

type Options = {
  now: () => number;
  fetchToken: (signal: AbortSignal) => Promise<string>;
  deliver: (token: string) => void;
  status: (state: 'fetching' | 'sent' | 'waiting' | 'retrying' | 'ready' | 'error', message?: string) => void;
  schedule: (fn: () => void, ms: number) => number;
  cancel: (id: number) => void;
};

// One owner per iframe/scope. Recovery never reloads the document or clears a
// draft; only the user's explicit reconnect may rebuild it. No VPN/TLS bypass.
export class EmbedIdentity {
  private token = '';
  private expires = 0;
  private confirmed = false;
  private waiting = false;
  private active = true;
  private generation = 0;
  private pending: Promise<void> | null = null;
  private pendingRecovery = false;
  private abort: AbortController | null = null;
  private timers: number[] = [];
  private failures = 0;
  private retryScheduled = false;
  private identityStopped = false;
  constructor(private options: Options) {}
  private clearDeliveries() {
    this.generation++;
    this.timers.forEach(id => this.options.cancel(id));
    this.timers = [];
    this.waiting = false;
    this.retryScheduled = false;
  }
  private send() {
    const token = this.token;
    const deliver = () => {
      if (this.active && this.token === token
          && this.expires > this.options.now() + 30000) this.options.deliver(token);
    };
    // Repeated child requests/load events do not restart the deadline.
    if (this.waiting || this.confirmed) { deliver(); return; }
    this.clearDeliveries();
    this.waiting = true;
    const generation = this.generation;
    const current = () => this.active && generation === this.generation;
    this.options.status('sent');
    deliver();
    // A synchronous receipt can arrive while the token promise is settling.
    if (!this.active || generation !== this.generation) return;
    for (const ms of [250,750,1500,5000,15000,30000])
      this.timers.push(this.options.schedule(() => {if(current())deliver();}, ms));
    this.timers.push(this.options.schedule(() => {
      if (current()) this.options.status('waiting', '连接较慢，正在自动恢复；当前输入和会话会保留。');
    }, EMBED_SLOW_NOTICE_MS));
    this.timers.push(this.options.schedule(() => {
      if (this.active && generation === this.generation) {
        this.clearDeliveries();
        this.options.status('error', 'PiChat 会话在 60 秒内未就绪。面板仍保留，可继续等待；重试连接会重新加载面板。');
      }
    },EMBED_READY_TIMEOUT_MS));
  }
  frameLoaded(): Promise<void> {
    // load fires after child scripts/auth/WS may already have become ready.
    // It is not an auth rejection: never rotate a valid credential here.
    if (this.confirmed && this.expires > this.options.now() + 30000) return Promise.resolve();
    return this.request();
  }
  request(force = false): Promise<void> {
    if (!this.active) return Promise.resolve();
    // Child auth polling must not skip backoff or restart an exhausted budget.
    if (this.retryScheduled || this.identityStopped) return Promise.resolve();
    if (this.pending) {
      if (!force || this.pendingRecovery) return this.pending;
      // Retire normal in-flight issuance on explicit rejection. Duplicate
      // recovery requests join the replacement, even if fetch ignores abort.
      const previous = this.abort;
      this.abort = null; this.pending = null;
      previous?.abort();
    }
    if (!force && this.token && this.expires > this.options.now() + 30000) {
      this.send();return Promise.resolve();
    }
    this.clearDeliveries();this.token = '';this.expires = 0;this.confirmed = false;
    this.options.status('fetching');
    const abort = new AbortController();this.abort = abort;this.pendingRecovery = force;
    const cancelled = new Promise<never>((_,reject) => {
      abort.signal.addEventListener('abort',()=>reject(new Error('Identity cancelled')), {once:true});
    });
    const timer = this.options.schedule(()=>abort.abort(),EMBED_IDENTITY_TIMEOUT_MS);
    this.pending = Promise.race([this.options.fetchToken(abort.signal),cancelled])
      .then(token => {
        if (!this.active || this.abort !== abort || abort.signal.aborted) return;
        let expires = 0;
        try {
          const encoded = token.split('.')[1].replace(/-/g,'+').replace(/_/g,'/');
          expires = Number(JSON.parse(atob(encoded)).exp) * 1000;
        } catch { /* invalid credentials must not trigger a retry loop */ }
        if (!Number.isFinite(expires) || expires <= this.options.now() + 30000)
          throw new EmbedIdentityFailure('Invalid identity expiry');
        this.failures = 0;
        this.token = token;this.expires = expires;this.send();
      }).catch(error => {
        if (this.active && this.abort === abort) {
          this.clearDeliveries();this.token = '';this.expires = 0;
          const retryable = !(error instanceof EmbedIdentityFailure) || error.retryable;
          const delay = EMBED_RETRY_DELAYS_MS[this.failures];
          if (retryable && delay !== undefined) {
            this.failures++;
            this.retryScheduled = true;
            const generation = this.generation;
            this.options.status('retrying', `身份连接暂时失败，${delay / 1000} 秒后自动重试（${this.failures}/2）。`);
            this.timers.push(this.options.schedule(() => {
              if (this.active && generation === this.generation) {
                this.retryScheduled = false;
                void this.request();
              }
            }, delay));
          } else {
            this.identityStopped = true;
            this.options.status('error','PiChat 身份获取失败或超时，请检查连接配置或重试连接。');
          }
        }
      }).finally(() => {
        this.options.cancel(timer);
        if (this.abort === abort) { this.abort = null;this.pending = null;this.pendingRecovery = false; }
      });
    return this.pending;
  }
  // Resume after a network/browser interruption without resetting healthy or
  // already in-progress handshakes.
  recover() {
    if (!this.active || this.confirmed || this.pending || this.waiting || this.retryScheduled) return;
    this.failures = 0;
    this.identityStopped = false;
    void this.request();
  }
  ready() {
    // A receipt from the previous identity must not end the new fetch's spinner.
    if (!this.active || !this.token || this.expires <= this.options.now() + 30000) return;
    this.confirmed = true;
    this.clearDeliveries();this.options.status('ready');
  }
  dispose() {
    this.active = false;this.clearDeliveries();this.abort?.abort();
    this.token = '';this.expires = 0;
  }
}
