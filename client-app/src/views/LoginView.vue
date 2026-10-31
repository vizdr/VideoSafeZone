<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import Password from "openvue/password";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const username = ref("");
const password = ref("");
const error = ref("");
const busy = ref(false);

async function submit(): Promise<void> {
  error.value = "";
  busy.value = true;
  try {
    await auth.signIn(username.value, password.value);
    // Back where the guard sent the viewer from; only an in-app path.
    const next = route.query.next;
    await router.replace(typeof next === "string" && next.startsWith("/") ? next : "/live");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <main class="login">
    <form class="login-card" @submit.prevent="submit">
      <h1>VideoSafeZone</h1>
      <label for="username">Username</label>
      <InputText id="username" v-model="username" autocomplete="username" required fluid />
      <label for="password">Password</label>
      <Password v-model="password" input-id="password" :feedback="false" toggle-mask fluid
                :input-props="{ autocomplete: 'current-password', required: true }" />
      <Button type="submit" label="Sign in" :loading="busy" fluid />
      <Message v-if="error" severity="error" size="small" variant="simple">{{ error }}</Message>
    </form>
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
  width: min(22rem, 100%);
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  padding: 2rem;
  background: var(--p-surface-800);
  border: 1px solid var(--p-content-border-color);
  border-radius: 0.75rem;
  box-sizing: border-box;
}
.login-card h1 { margin: 0 0 0.75rem; font-size: 1.4rem; }
.login-card label { font-size: 0.9rem; color: var(--p-text-muted-color); }
.login-card :deep(.p-button) { margin-top: 0.75rem; }
</style>
