<script setup lang="ts">
// The Account page (DEALER-PANEL-PLAN.md D18), after Videoloft's. Its Account details tab: the email
// address and the password, each with Edit. Plan & Billing and Additional users come in Phase 9;
// Opt-ins and the "Convert to SSO" card are left out (D17, D18).
import { ref } from "vue";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import Password from "openvue/password";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();

type EmailStep = "shown" | "new" | "code";
const emailStep = ref<EmailStep>("shown");
const newEmail = ref("");
const emailCode = ref("");
const emailError = ref("");
const emailInfo = ref("");

const editingPassword = ref(false);
const current = ref("");
const next = ref("");
const repeat = ref("");
const passwordError = ref("");
const passwordInfo = ref("");

const busy = ref(false);

async function attempt(work: () => Promise<void>, failed: (m: string) => void): Promise<void> {
  busy.value = true;
  try {
    await work();
  } catch (e) {
    failed(e instanceof Error ? e.message : String(e));
  } finally {
    busy.value = false;
  }
}

function editEmail(): void {
  emailStep.value = "new";
  newEmail.value = emailCode.value = emailError.value = emailInfo.value = "";
}

const sendEmailCode = () => attempt(async () => {
  emailError.value = "";
  await auth.changeEmail(newEmail.value.trim());
  emailStep.value = "code";
  emailInfo.value = `A code is on its way to ${newEmail.value.trim()}. Until you confirm it, you keep using your current address.`;
}, m => { emailError.value = m; });

const confirmEmail = () => attempt(async () => {
  emailError.value = "";
  await auth.confirmEmail(emailCode.value.trim());
  emailStep.value = "shown";
  emailInfo.value = "Your email address is changed: use it to log in from now on.";
}, m => { emailError.value = m; });

function editPassword(): void {
  editingPassword.value = true;
  current.value = next.value = repeat.value = passwordError.value = passwordInfo.value = "";
}

const changePassword = () => {
  passwordError.value = "";
  if (next.value !== repeat.value) {
    passwordError.value = "The two new passwords aren't the same.";
    return;
  }
  return attempt(async () => {
    await auth.changePassword(current.value, next.value);
    editingPassword.value = false;
    passwordInfo.value = "Your password is changed.";
  }, m => { passwordError.value = m; });
};
</script>

<template>
  <section class="view account">
    <nav class="tabs" aria-label="Account">
      <span class="tab active" aria-current="page">Account details</span>
    </nav>

    <article class="card">
      <h2>Account details<span class="dot">.</span></h2>

      <div class="row">
        <div class="row-main">
          <strong>Email:</strong> <span data-test="email">{{ auth.username }}</span>
        </div>
        <button v-if="emailStep === 'shown'" type="button" class="link" @click="editEmail">Edit</button>
      </div>
      <form v-if="emailStep === 'new'" class="edit" @submit.prevent="sendEmailCode">
        <label for="new-email">New email address</label>
        <InputText id="new-email" v-model="newEmail" type="email" autocomplete="email" required />
        <div class="actions">
          <Button type="submit" label="Send code" :loading="busy" />
          <Button type="button" label="Cancel" severity="secondary" @click="emailStep = 'shown'" />
        </div>
      </form>
      <form v-else-if="emailStep === 'code'" class="edit" @submit.prevent="confirmEmail">
        <label for="email-code">Code from the email</label>
        <InputText id="email-code" v-model="emailCode" inputmode="numeric" autocomplete="one-time-code" required />
        <div class="actions">
          <Button type="submit" label="Confirm" :loading="busy" />
          <Button type="button" label="Cancel" severity="secondary" @click="emailStep = 'shown'" />
        </div>
      </form>
      <Message v-if="emailInfo" severity="info" size="small" variant="simple">{{ emailInfo }}</Message>
      <Message v-if="emailError" severity="error" size="small" variant="simple" role="alert">{{ emailError }}</Message>

      <div class="row">
        <div class="row-main"><strong>Password:</strong> ********</div>
        <button v-if="!editingPassword" type="button" class="link" @click="editPassword">Edit</button>
      </div>
      <form v-if="editingPassword" class="edit" @submit.prevent="changePassword">
        <label for="current-password">Current password</label>
        <Password v-model="current" input-id="current-password" :feedback="false" toggle-mask
                  :input-props="{ autocomplete: 'current-password', required: true }" />
        <label for="next-password">New password</label>
        <Password v-model="next" input-id="next-password" :feedback="false" toggle-mask
                  :input-props="{ autocomplete: 'new-password', required: true }" />
        <label for="repeat-password">Repeat it</label>
        <Password v-model="repeat" input-id="repeat-password" :feedback="false" toggle-mask
                  :input-props="{ autocomplete: 'new-password', required: true }" />
        <p class="hint">At least 10 characters, with upper- and lower-case letters and a number.</p>
        <div class="actions">
          <Button type="submit" label="Change password" :loading="busy" />
          <Button type="button" label="Cancel" severity="secondary" @click="editingPassword = false" />
        </div>
      </form>
      <Message v-if="passwordInfo" severity="info" size="small" variant="simple">{{ passwordInfo }}</Message>
      <Message v-if="passwordError" severity="error" size="small" variant="simple" role="alert">{{ passwordError }}</Message>
    </article>
  </section>
</template>

<style scoped>
.account { max-width: 60rem; }
.tabs {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1.25rem;
  padding: 0.35rem;
  background: var(--p-surface-900);
  border-radius: 0.6rem;
}
.tab { padding: 0.45rem 1rem; border-radius: 0.45rem; color: var(--p-text-muted-color); }
.tab.active { background: var(--p-primary-color); color: var(--p-primary-contrast-color); }
.card {
  padding: 1.25rem 1.5rem;
  background: var(--p-surface-900);
  border-radius: 0.6rem;
}
.card h2 {
  margin: 0 0 1rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--p-primary-color);
  font-size: 1.4rem;
}
.dot { color: var(--p-primary-color); }
.row { display: flex; align-items: baseline; gap: 2rem; padding: 0.75rem 0; }
.row-main { min-width: 0; overflow-wrap: anywhere; }
.edit { display: flex; flex-direction: column; gap: 0.45rem; max-width: 24rem; margin: 0 0 0.75rem; }
.edit label { font-size: 0.9rem; color: var(--p-text-muted-color); }
.actions { display: flex; gap: 0.5rem; margin-top: 0.5rem; }
.hint { margin: 0; font-size: 0.8rem; color: var(--p-text-muted-color); }
.link {
  padding: 0;
  background: none;
  border: 0;
  color: var(--p-text-color);
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}
.link:focus-visible { outline: 2px solid var(--p-primary-color); outline-offset: 2px; }
</style>
