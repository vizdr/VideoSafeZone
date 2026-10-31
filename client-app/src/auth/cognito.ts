// Sign-in against the Cognito user pool, over its plain JSON API -- no SDK, as in the old page.
// What this adds over the old page is the refresh: the ID token lasts an hour, and the old page
// never renewed it, so after an hour every API call answered 401. Here a token about to expire is
// renewed before the call that needs it (REFRESH_TOKEN_AUTH), so no timer has to survive a
// throttled background tab.
//
// Where the tokens live: the ID and access tokens in memory only; the refresh token in
// sessionStorage, so a reload signs the tab back in and closing the tab forgets it.
import { COGNITO_CLIENT_ID, COGNITO_REGION } from "../config";

export class AuthError extends Error {}

export interface AuthResult {
  IdToken: string;
  AccessToken: string;
  RefreshToken?: string;   // absent from a refresh unless the pool rotates refresh tokens
  ExpiresIn: number;       // seconds
}

interface Tokens {
  idToken: string;
  refreshToken: string;
  expiresAt: number;       // ms, this page's clock
}

export interface SessionDeps {
  fetch?: typeof fetch;
  storage?: () => Storage | null;
  now?: () => number;
}

const STORAGE_KEY = "vsz.refreshToken";
const EARLY_MS = 120_000;   // renew this long before the ID token expires

function defaultStorage(): Storage | null {
  try { return window.sessionStorage; } catch { return null; }   // blocked in some private modes
}

/** The payload of a JWT, unverified: only for showing who is signed in. */
export function jwtClaims(jwt: string): Record<string, unknown> {
  try {
    const part = jwt.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const bytes = Uint8Array.from(atob(part), c => c.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    return {};
  }
}

export class CognitoSession {
  private tokens: Tokens | null = null;
  private refreshing: Promise<void> | null = null;
  private listeners = new Set<() => void>();
  private readonly fetch: typeof fetch;
  private readonly storage: () => Storage | null;
  private readonly now: () => number;

  constructor(deps: SessionDeps = {}) {
    this.fetch = deps.fetch ?? ((...a) => fetch(...a));
    this.storage = deps.storage ?? defaultStorage;
    this.now = deps.now ?? Date.now;
  }

  get signedIn(): boolean { return this.tokens !== null; }

  get username(): string | null {
    if (!this.tokens) return null;
    const c = jwtClaims(this.tokens.idToken);
    return typeof c["cognito:username"] === "string" ? c["cognito:username"] : null;
  }

  /** Called whenever signed-in state changes, including a refresh that failed. */
  onChange(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  async signIn(username: string, password: string): Promise<void> {
    const res = await this.initiateAuth("USER_PASSWORD_AUTH", { USERNAME: username, PASSWORD: password });
    this.accept(res);
  }

  /** Signs the tab back in from a stored refresh token. False when there is none or it's dead. */
  async restore(): Promise<boolean> {
    const stored = this.read();
    if (!stored) return false;
    try {
      this.accept(await this.initiateAuth("REFRESH_TOKEN_AUTH", { REFRESH_TOKEN: stored }), stored);
      return true;
    } catch {
      this.signOut();
      return false;
    }
  }

  /** A current ID token, renewed first if it is about to expire. Throws AuthError when signed out. */
  async idToken(): Promise<string> {
    if (!this.tokens) throw new AuthError("not signed in");
    if (this.now() > this.tokens.expiresAt - EARLY_MS) await this.refresh();
    if (!this.tokens) throw new AuthError("signed out");
    return this.tokens.idToken;
  }

  /** Renews the ID token now. Concurrent callers share one request; a refused refresh signs out. */
  refresh(): Promise<void> {
    const current = this.tokens;
    if (!current) return Promise.reject(new AuthError("not signed in"));
    this.refreshing ??= this.initiateAuth("REFRESH_TOKEN_AUTH", { REFRESH_TOKEN: current.refreshToken })
      .then(res => this.accept(res, current.refreshToken))
      .catch(e => {
        if (e instanceof AuthError) this.signOut();   // refresh token expired or revoked
        throw e;
      })
      .finally(() => { this.refreshing = null; });
    return this.refreshing;
  }

  signOut(): void {
    const was = this.tokens !== null;
    this.tokens = null;
    try { this.storage()?.removeItem(STORAGE_KEY); } catch { /* storage blocked */ }
    if (was) this.emit();
  }

  private async initiateAuth(flow: string, params: Record<string, string>): Promise<AuthResult> {
    const r = await this.fetch(`https://cognito-idp.${COGNITO_REGION}.amazonaws.com/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
      },
      body: JSON.stringify({ AuthFlow: flow, ClientId: COGNITO_CLIENT_ID, AuthParameters: params }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new AuthError(data.message || `Sign-in failed (${r.status})`);
    if (!data.AuthenticationResult) {
      throw new AuthError(data.ChallengeName
        ? `Sign-in needs a step this page can't do yet (${data.ChallengeName})`
        : "Sign-in failed");
    }
    return data.AuthenticationResult;
  }

  private accept(res: AuthResult, previousRefresh?: string): void {
    const refreshToken = res.RefreshToken ?? previousRefresh;
    if (!refreshToken) throw new AuthError("Sign-in returned no refresh token");
    this.tokens = { idToken: res.IdToken, refreshToken, expiresAt: this.now() + res.ExpiresIn * 1000 };
    try { this.storage()?.setItem(STORAGE_KEY, refreshToken); } catch { /* storage blocked */ }
    this.emit();
  }

  private read(): string | null {
    try { return this.storage()?.getItem(STORAGE_KEY) ?? null; } catch { return null; }
  }

  private emit(): void { for (const fn of this.listeners) fn(); }
}
