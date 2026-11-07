import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { session } from "../api";

/** Signed-in state for the views, mirrored from the CognitoSession (which also signs out by itself
 *  when a refresh is refused). */
export const useAuthStore = defineStore("auth", () => {
  const signedIn = ref(session.signedIn);
  const username = ref(session.username);
  const claims = ref(session.claims);
  session.onChange(() => {
    signedIn.value = session.signedIn;
    username.value = session.username;
    claims.value = session.claims;
  });

  /** The side the person is on (DEALER-PANEL-PLAN.md D4): system_admin, system_owner, dealer,
   *  customer -- or nothing, under the old pool, until Phase 3's switch. */
  const role = computed(() => (typeof claims.value.vsz_role === "string" ? claims.value.vsz_role : null));

  return {
    signedIn,
    username,
    role,
    signIn: (user: string, password: string) => session.signIn(user, password),
    completeNewPassword: (password: string) => session.completeNewPassword(password),
    forgotPassword: (email: string) => session.forgotPassword(email),
    confirmForgotPassword: (email: string, code: string, password: string) =>
      session.confirmForgotPassword(email, code, password),
    changePassword: (current: string, next: string) => session.changePassword(current, next),
    changeEmail: (email: string) => session.changeEmail(email),
    confirmEmail: (code: string) => session.confirmEmail(code),
    restore: () => session.restore(),
    signOut: () => session.signOut(),
  };
});
