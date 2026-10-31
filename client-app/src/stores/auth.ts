import { defineStore } from "pinia";
import { ref } from "vue";
import { session } from "../api";

/** Signed-in state for the views, mirrored from the CognitoSession (which also signs out by itself
 *  when a refresh is refused). */
export const useAuthStore = defineStore("auth", () => {
  const signedIn = ref(session.signedIn);
  const username = ref(session.username);
  session.onChange(() => {
    signedIn.value = session.signedIn;
    username.value = session.username;
  });

  return {
    signedIn,
    username,
    signIn: (user: string, password: string) => session.signIn(user, password),
    restore: () => session.restore(),
    signOut: () => session.signOut(),
  };
});
