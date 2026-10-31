// Calls to API Gateway with the Cognito ID token. Every call resolves to { ok, status, data } --
// the shape the old page read from fetch() -- and throws only for a network failure or a lost
// sign-in (AuthError), which callers report the way the old page did.

export interface ApiResult<T = Record<string, unknown>> {
  ok: boolean;
  status: number;
  data: T & { error?: string; message?: string };
}

export interface ApiDeps {
  base: string;
  /** A current ID token (CognitoSession.idToken). */
  token: () => Promise<string>;
  /** Renews the token after a 401 (CognitoSession.refresh). */
  refresh?: () => Promise<void>;
  fetch?: typeof fetch;
}

export class ApiClient {
  private readonly fetch: typeof fetch;

  constructor(private readonly deps: ApiDeps) {
    this.fetch = deps.fetch ?? ((...a) => fetch(...a));
  }

  async request<T = Record<string, unknown>>(
    method: "GET" | "POST" | "PUT",
    path: string,
    body?: unknown,
    opts: { keepalive?: boolean } = {},
  ): Promise<ApiResult<T>> {
    const send = async () => this.fetch(this.deps.base + path, {
      method,
      keepalive: opts.keepalive,
      headers: {
        Authorization: await this.deps.token(),
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let r = await send();
    // The authorizer refused the token (clock skew, or revoked early): renew once and retry.
    if (r.status === 401 && this.deps.refresh) {
      await this.deps.refresh();
      r = await send();
    }
    // API Gateway's own errors are JSON too, but a proxy in between may not be.
    const data = await r.json().catch(() => ({}));
    return { ok: r.ok, status: r.status, data };
  }
}

/** The message to show for a failed call: the Lambda's `error`, API Gateway's `message`, else the status. */
export function errorText(res: ApiResult<unknown>): string {
  return res.data.error || res.data.message || String(res.status);
}
