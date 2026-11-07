// Sign-in against the Cognito user pool, over its plain JSON API -- no SDK, as in the old page.
// What this adds over the old page is the refresh: the ID token lasts an hour, and the old page
// never renewed it, so after an hour every API call answered 401. Here a token about to expire is
// renewed before the call that needs it (REFRESH_TOKEN_AUTH), so no timer has to survive a
// throttled background tab.
//
// Where the tokens live: the ID and access tokens in memory only; the refresh token in
// sessionStorage, so a reload signs the tab back in and closing the tab forgets it.
//
// DEALER-PANEL-PLAN.md D17, D18 add what invited users and the Account page need: the new-password
// step after an invitation's temporary password, the forgotten-password steps, and changing one's
// password and email address -- the last two with the access token, which is why it is kept now.
import { COGNITO_CLIENT_ID, COGNITO_REGION } from "../config";

export class AuthError extends Error {}

/** The sign-in worked, but an invited person has to choose their own password first. */
export class NewPasswordRequired extends AuthError {
  constructor() { super("Choose your own password to finish signing in."); }
}

// Cognito puts its trigger's refusal (vsz-directory, D4) behind this prefix; the person reads the rest.
const TRIGGER_PREFIX = /^PreTokenGeneration failed with error\s*/;

export interface AuthResult {
  IdToken: string;
  AccessToken: string;
  RefreshToken?: string;   // absent from a refresh unless the pool rotates refresh tokens
  ExpiresIn: number;       // seconds
}

interface Tokens {
  idToken: string;
  accessToken: string;
  refreshToken: string;
  expiresAt: number;       // ms, this page's clock
}

/** The parts of Cognito's answers this page reads. */
interface CognitoAnswer {
  AuthenticationResult?: AuthResult;
  ChallengeName?: string;
  Session?: string;
  ChallengeParameters?: Record<string, string>;
  message?: string;
}

interface Challenge {
  session: string;
  username: string;        // Cognito's own user name for the challenge: USER_ID_FOR_SRP
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
  private challenge: Challenge | null = null;
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

  /** The signed-in person's email address, else their Cognito user name. */
  get username(): string | null {
    if (!this.tokens) return null;
    const c = jwtClaims(this.tokens.idToken);
    if (typeof c.email === "string") return c.email;
    return typeof c["cognito:username"] === "string" ? c["cognito:username"] : null;
  }

  /** The ID token's claims -- vsz_role and the rest (D4) -- for what the page shows. The API
   *  checks them again on every call; hiding is never the check. */
  get claims(): Record<string, unknown> {
    return this.tokens ? jwtClaims(this.tokens.idToken) : {};
  }

  /** Called whenever signed-in state changes, including a refresh that failed. */
  onChange(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** Throws NewPasswordRequired when an invited person signs in with a temporary password:
   *  completeNewPassword() then finishes the sign-in. */
  async signIn(username: string, password: string): Promise<void> {
    this.challenge = null;
    const res = await this.initiateAuth("USER_PASSWORD_AUTH", { USERNAME: username, PASSWORD: password });
    this.accept(res);
  }

  get awaitingNewPassword(): boolean { return this.challenge !== null; }

  async completeNewPassword(newPassword: string): Promise<void> {
    const c = this.challenge;
    if (!c) throw new AuthError("Sign in again with the temporary password.");
    const data = await this.call("RespondToAuthChallenge", {
      ChallengeName: "NEW_PASSWORD_REQUIRED", ClientId: COGNITO_CLIENT_ID, Session: c.session,
      ChallengeResponses: { USERNAME: c.username, NEW_PASSWORD: newPassword },
    });
    if (!data.AuthenticationResult) throw new AuthError("Sign-in failed");
    this.challenge = null;
    this.accept(data.AuthenticationResult);
  }

  /** Cognito emails a code -- or, for an address it doesn't know, quietly nothing (the client
   *  hides which addresses exist). */
  async forgotPassword(email: string): Promise<void> {
    await this.call("ForgotPassword", { ClientId: COGNITO_CLIENT_ID, Username: email });
  }

  async confirmForgotPassword(email: string, code: string, newPassword: string): Promise<void> {
    await this.call("ConfirmForgotPassword", {
      ClientId: COGNITO_CLIENT_ID, Username: email, ConfirmationCode: code, Password: newPassword,
    });
  }

  async changePassword(current: string, next: string): Promise<void> {
    await this.call("ChangePassword", {
      AccessToken: await this.accessToken(), PreviousPassword: current, ProposedPassword: next,
    });
  }

  /** Cognito sends a code to the new address; until it is confirmed the old one stays in use
   *  (AttributesRequireVerificationBeforeUpdate on the pool). */
  async changeEmail(email: string): Promise<void> {
    await this.call("UpdateUserAttributes", {
      AccessToken: await this.accessToken(), UserAttributes: [{ Name: "email", Value: email }],
    });
  }

  /** Confirms the new address, then renews the tokens so the page shows it at once. */
  async confirmEmail(code: string): Promise<void> {
    await this.call("VerifyUserAttribute", {
      AccessToken: await this.accessToken(), AttributeName: "email", Code: code,
    });
    await this.refresh();
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
    return (await this.current()).idToken;
  }

  /** A current access token, for the calls a person makes on their own account. */
  async accessToken(): Promise<string> {
    return (await this.current()).accessToken;
  }

  private async current(): Promise<Tokens> {
    if (!this.tokens) throw new AuthError("not signed in");
    if (this.now() > this.tokens.expiresAt - EARLY_MS) await this.refresh();
    if (!this.tokens) throw new AuthError("signed out");
    return this.tokens;
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
    this.challenge = null;
    try { this.storage()?.removeItem(STORAGE_KEY); } catch { /* storage blocked */ }
    if (was) this.emit();
  }

  private async call(action: string, body: object): Promise<CognitoAnswer> {
    const r = await this.fetch(`https://cognito-idp.${COGNITO_REGION}.amazonaws.com/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": `AWSCognitoIdentityProviderService.${action}`,
      },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const message = typeof data.message === "string" ? data.message.replace(TRIGGER_PREFIX, "") : "";
      throw new AuthError(message || `Request failed (${r.status})`);
    }
    return data;
  }

  private async initiateAuth(flow: string, params: Record<string, string>): Promise<AuthResult> {
    const data = await this.call("InitiateAuth", { AuthFlow: flow, ClientId: COGNITO_CLIENT_ID, AuthParameters: params });
    if (data.ChallengeName === "NEW_PASSWORD_REQUIRED" && typeof data.Session === "string") {
      this.challenge = { session: data.Session, username: data.ChallengeParameters?.USER_ID_FOR_SRP ?? params.USERNAME };
      throw new NewPasswordRequired();
    }
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
    this.tokens = {
      idToken: res.IdToken, accessToken: res.AccessToken, refreshToken,
      expiresAt: this.now() + res.ExpiresIn * 1000,
    };
    try { this.storage()?.setItem(STORAGE_KEY, refreshToken); } catch { /* storage blocked */ }
    this.emit();
  }

  private read(): string | null {
    try { return this.storage()?.getItem(STORAGE_KEY) ?? null; } catch { return null; }
  }

  private emit(): void { for (const fn of this.listeners) fn(); }
}
