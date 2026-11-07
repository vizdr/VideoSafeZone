<script setup lang="ts">
// The one sign-in page for everyone (DEALER-PANEL-PLAN.md D17): email address, password with
// show/hide, "Forgotten password?", Log in -- then the role decides where the page goes (D7).
// Steps beside it: an invited person's first sign-in asks for their own password; a forgotten
// password is reset with a code Cognito emails. Microsoft / Apple sign-in is left out (D17).
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import Password from "openvue/password";
import { NewPasswordRequired } from "../auth/cognito";
import { useAuthStore } from "../stores/auth";

type Step = "sign-in" | "new-password" | "forgot" | "reset";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const step = ref<Step>("sign-in");
const email = ref("");
const password = ref("");
const newPassword = ref("");
const repeat = ref("");
const code = ref("");
const error = ref("");
const info = ref("");
const busy = ref(false);

const PASSWORD_RULE = "At least 10 characters, with upper- and lower-case letters and a number.";

function go(next: Step, message = ""): void {
  step.value = next;
  error.value = "";
  info.value = message;
  newPassword.value = repeat.value = code.value = "";
}

async function enter(): Promise<void> {
  // Back where the guard sent the person from; only an in-app path.
  const next = route.query.next;
  await router.replace(typeof next === "string" && next.startsWith("/") ? next : "/live");
}

async function run(work: () => Promise<void>): Promise<void> {
  error.value = "";
  busy.value = true;
  try {
    await work();
  } catch (e) {
    if (e instanceof NewPasswordRequired) {
      go("new-password");
    } else {
      error.value = e instanceof Error ? e.message : String(e);
    }
  } finally {
    busy.value = false;
  }
}

function checkNewPassword(): boolean {
  if (newPassword.value !== repeat.value) {
    error.value = "The two passwords aren't the same.";
    return false;
  }
  return true;
}

const signIn = () => run(async () => {
  await auth.signIn(email.value.trim(), password.value);
  await enter();
});

const choosePassword = () => checkNewPassword() && run(async () => {
  await auth.completeNewPassword(newPassword.value);
  await enter();
});

const sendCode = () => run(async () => {
  await auth.forgotPassword(email.value.trim());
  go("reset", "If that address has an account, a code is on its way to it.");
});

const resetPassword = () => checkNewPassword() && run(async () => {
  await auth.confirmForgotPassword(email.value.trim(), code.value.trim(), newPassword.value);
  password.value = "";
  go("sign-in", "Your password is changed: log in with it.");
});
</script>

<template>
  <main class="login">
    <div class="login-card">
      <h1>VideoSafeZone</h1>

      <form v-if="step === 'sign-in'" @submit.prevent="signIn">
        <label for="email">Email address</label>
        <InputText id="email" v-model="email" type="text" inputmode="email" autocomplete="username"
                   required fluid />
        <label for="password">Password</label>
        <Password v-model="password" input-id="password" :feedback="false" toggle-mask fluid
                  :input-props="{ autocomplete: 'current-password', required: true }" />
        <button type="button" class="link" @click="go('forgot')">Forgotten password?</button>
        <Button type="submit" label="Log in" :loading="busy" fluid />
      </form>

      <form v-else-if="step === 'new-password'" @submit.prevent="choosePassword">
        <p class="lead">Welcome. Choose your own password to finish signing in.</p>
        <label for="new-password">New password</label>
        <Password v-model="newPassword" input-id="new-password" :feedback="false" toggle-mask fluid
                  :input-props="{ autocomplete: 'new-password', required: true }" />
        <label for="repeat">Repeat it</label>
        <Password v-model="repeat" input-id="repeat" :feedback="false" toggle-mask fluid
                  :input-props="{ autocomplete: 'new-password', required: true }" />
        <p class="hint">{{ PASSWORD_RULE }}</p>
        <Button type="submit" label="Save and log in" :loading="busy" fluid />
        <button type="button" class="link" @click="go('sign-in')">Back to log in</button>
      </form>

      <form v-else-if="step === 'forgot'" @submit.prevent="sendCode">
        <p class="lead">We'll email you a code to set a new password.</p>
        <label for="forgot-email">Email address</label>
        <InputText id="forgot-email" v-model="email" type="text" inputmode="email" autocomplete="username"
                   required fluid />
        <Button type="submit" label="Send code" :loading="busy" fluid />
        <button type="button" class="link" @click="go('sign-in')">Back to log in</button>
      </form>

      <form v-else @submit.prevent="resetPassword">
        <label for="code">Code from the email</label>
        <InputText id="code" v-model="code" inputmode="numeric" autocomplete="one-time-code" required fluid />
        <label for="reset-password">New password</label>
        <Password v-model="newPassword" input-id="reset-password" :feedback="false" toggle-mask fluid
                  :input-props="{ autocomplete: 'new-password', required: true }" />
        <label for="reset-repeat">Repeat it</label>
        <Password v-model="repeat" input-id="reset-repeat" :feedback="false" toggle-mask fluid
                  :input-props="{ autocomplete: 'new-password', required: true }" />
        <p class="hint">{{ PASSWORD_RULE }}</p>
        <Button type="submit" label="Set password" :loading="busy" fluid />
        <button type="button" class="link" @click="go('sign-in')">Back to log in</button>
      </form>

      <Message v-if="info" severity="info" size="small" variant="simple" class="info">{{ info }}</Message>
      <Message v-if="error" severity="error" size="small" variant="simple" role="alert">{{ error }}</Message>
    </div>
  </main>
</template>

<style scoped>
.login {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 1rem;
  box-sizing: border-box;
}
.login-card {
  width: min(24rem, 100%);
  padding: 2rem;
  background: var(--p-surface-800);
  border: 1px solid var(--p-content-border-color);
  border-radius: 0.75rem;
  box-sizing: border-box;
}
.login-card form { display: flex; flex-direction: column; gap: 0.6rem; }
.login-card h1 { margin: 0 0 1.25rem; font-size: 1.6rem; text-align: center; }
.login-card label { font-size: 0.9rem; color: var(--p-text-muted-color); }
.login-card :deep(.p-button) { margin-top: 0.75rem; }
.lead { margin: 0 0 0.25rem; }
.hint { margin: 0; font-size: 0.8rem; color: var(--p-text-muted-color); }
.link {
  align-self: flex-start;
  padding: 0;
  background: none;
  border: 0;
  color: var(--p-primary-color);
  font: inherit;
  font-size: 0.9rem;
  text-decoration: underline;
  cursor: pointer;
}
.link:focus-visible { outline: 2px solid var(--p-primary-color); outline-offset: 2px; }
.info, [role="alert"] { margin-top: 0.75rem; }
</style>
