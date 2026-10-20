type Options = {
  now: () => number;
  fetchToken: (signal: AbortSignal) => Promise<string>;
  deliver: (token: string) => void;
  status: (state: 'fetching' | 'sent' | 'ready' | 'error', error?: string) => void;
  schedule: (fn: () => void, ms: number) => number;
  cancel: (id: number) => void;
};

// One instance per iframe/connection lifetime. JWT expiry is a cache hint only;
// signature/authorization remain PiChat's responsibility.
export class EmbedIdentity {
  private token = '';
  private expires = 0;
  private confirmed = false;
  private active = true;
  private generation = 0;
  private pending: Promise<void> | null = null;
  private abort: AbortController | null = null;
  private timers: number[] = [];
  constructor(private options: Options) {}
  private clearDeliveries() {
    this.generation++;
    this.timers.forEach(id => this.options.cancel(id));
    this.timers = [];
  }
  private send() {
    this.clearDeliveries();
    this.confirmed = false;
    const generation = this.generation, token = this.token;
    const deliver = () => {
      if (this.active && generation === this.generation && this.token === token
          && this.expires > this.options.now() + 30000) this.options.deliver(token);
    };
    this.options.status('sent');
    deliver();
    // A synchronous receipt can arrive while the token promise is settling.
    if (!this.active || generation !== this.generation) return;
    for (const ms of [250,750,1500]) this.timers.push(this.options.schedule(deliver, ms));
    this.timers.push(this.options.schedule(() => {
      if (this.active && generation === this.generation) {
        this.clearDeliveries();
        this.options.status('error', 'PiChat 身份已签发，但会话尚未就绪。可继续等待或重试连接。');
      }
    },10000));
  }
  frameLoaded(): Promise<void> {
    // load fires after child scripts/auth/WS may already have become ready.
    // It is not an auth rejection: never rotate a valid credential here.
    if (this.confirmed && this.expires > this.options.now() + 30000) return Promise.resolve();
    return this.request();
  }
  request(force = false): Promise<void> {
    if (!this.active) return Promise.resolve();
    if (this.pending) return this.pending;
    if (!force && this.token && this.expires > this.options.now() + 30000) {
      this.send();return Promise.resolve();
    }
    this.clearDeliveries();this.token = '';this.expires = 0;this.confirmed = false;
    this.options.status('fetching');
    const abort = new AbortController();this.abort = abort;
    const cancelled = new Promise<never>((_,reject) => {
      abort.signal.addEventListener('abort',()=>reject(new Error('Identity cancelled')), {once:true});
    });
    const timer = this.options.schedule(()=>abort.abort(),15000);
    this.pending = Promise.race([this.options.fetchToken(abort.signal),cancelled])
      .then(token => {
        if (!this.active || this.abort !== abort || abort.signal.aborted) return;
        const part = token.split('.')[1];
        const encoded = part.replace(/-/g,'+').replace(/_/g,'/');
        const expires = Number(JSON.parse(atob(encoded)).exp) * 1000;
        if (!Number.isFinite(expires) || expires <= this.options.now() + 30000)
          throw new Error('Invalid identity expiry');
        this.token = token;this.expires = expires;this.send();
      }).catch(() => {
        if (this.active && this.abort === abort) {
          this.clearDeliveries();this.token = '';this.expires = 0;
          this.options.status('error','PiChat 身份获取失败或超时，请重试连接。');
        }
      }).finally(() => {
        this.options.cancel(timer);
        if (this.abort === abort) { this.abort = null;this.pending = null; }
      });
    return this.pending;
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
