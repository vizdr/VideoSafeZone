<script setup lang="ts">
// Enter Cloud ID (DEALER-PANEL-PLAN.md §7.3, D15): the code from the adapter's label, then where the
// adapter is -- one of the customer's locations or a new one -- and a name for it. The adapter
// needn't be online. Used by the customer (Add adapter, accountId) and by a dealer, owner or
// administrator for a customer (the dealer portal, customerId). A wrong code goes back to the code.
import { computed, ref } from "vue";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import type { Activation } from "../../api/types";
import { formatCloudId, readCloudId } from "../../dealer/cloudid";
import { browserZone, timeZones } from "../../dealer/zones";

const props = defineProps<{
  sites: Array<{ id: string; name: string; timezone: string }>;
  accountId?: string;
  customerId?: string;
}>();
const emit = defineEmits<{ activated: [Activation] }>();

const step = ref<"code" | "place">("code");
const code = ref("");
const site = ref(props.sites[0]?.id ?? "new");
const siteName = ref(""), siteZone = ref(browserZone()), name = ref("");
const zones = timeZones();
const error = ref(""), busy = ref(false);
const complete = computed(() => readCloudId(code.value) !== null);

function next(): void {
  if (!complete.value) return;
  error.value = "";
  step.value = "place";
}

async function activate(): Promise<void> {
  busy.value = true;
  error.value = "";
  try {
    const res = await endpoints.activateAdapter({
      cloudId: code.value,
      ...(props.accountId ? { accountId: props.accountId } : {}),
      ...(props.customerId ? { customerId: props.customerId } : {}),
      ...(site.value === "new" ? { newSite: { name: siteName.value.trim(), timezone: siteZone.value } } : { siteId: site.value }),
      name: name.value.trim() || undefined,
    });
    if (!res.ok) {
      error.value = errorText(res);
      if (res.status === 400 && /Cloud ID/.test(error.value)) step.value = "code";
      return;
    }
    emit("activated", res.data);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="activate">
    <div v-if="step === 'code'" class="cloud-id">
      <!-- The adapter, its label circled: where the Cloud ID is (an own drawing, §7.3). -->
      <svg viewBox="0 0 160 100" width="160" height="100" aria-hidden="true" class="drawing">
        <rect x="8" y="18" width="144" height="64" rx="8" fill="none" stroke="currentColor" stroke-width="2.5" />
        <circle cx="26" cy="34" r="3" fill="currentColor" />
        <rect x="70" y="44" width="62" height="22" rx="2" fill="none" stroke="currentColor" stroke-width="1.5" />
        <text x="101" y="59" font-size="8" text-anchor="middle" fill="currentColor">K7QM4-XRPZ9</text>
        <ellipse cx="101" cy="55" rx="40" ry="20" fill="none" class="circled" stroke-width="2.5" />
      </svg>
      <div class="field">
        <label for="cloud-id">Cloud ID</label>
        <InputText id="cloud-id" :model-value="code" placeholder="K7QM4-XRPZ9" autocomplete="off" spellcheck="false"
                   maxlength="11" data-test="cloud-id" @update:model-value="v => (code = formatCloudId(v ?? ''))"
                   @keyup.enter="next" />
        <small class="muted">On the adapter's label: ten letters and digits.</small>
        <Button label="Continue" :disabled="!complete" data-test="continue" @click="next" />
      </div>
    </div>

    <template v-else>
      <p>Cloud ID <strong data-test="code-shown">{{ code }}</strong>
        <Button text size="small" label="Change" @click="step = 'code'" /></p>
      <fieldset class="choice">
        <legend>Where is the adapter?</legend>
        <label v-for="s in sites" :key="s.id"><input v-model="site" type="radio" name="site" :value="s.id" /> {{ s.name }}</label>
        <label><input v-model="site" type="radio" name="site" value="new" data-test="new-site" /> A new location</label>
      </fieldset>
      <div v-if="site === 'new'" class="console-form">
        <label>The location's name<InputText v-model="siteName" data-test="site-name" /></label>
        <label>Time zone
          <select v-model="siteZone" class="p-inputtext" data-test="site-zone">
            <option v-for="z in zones" :key="z" :value="z">{{ z }}</option>
          </select>
        </label>
      </div>
      <div class="console-form">
        <label>A name for the adapter (optional)<InputText v-model="name" placeholder="Garage" data-test="adapter-name" /></label>
      </div>
      <Button label="Activate" :loading="busy" :disabled="site === 'new' && !siteName.trim()" data-test="activate" @click="activate" />
    </template>
    <Message v-if="error" severity="error" size="small" variant="simple" data-test="activate-error">{{ error }}</Message>
  </div>
</template>

<style scoped>
.activate { display: flex; flex-direction: column; gap: 0.75rem; }
.cloud-id { display: flex; flex-wrap: wrap; gap: 1.5rem; align-items: center; }
.drawing { color: var(--p-text-muted-color); }
.drawing .circled { stroke: var(--p-primary-color); }
.field { display: flex; flex-direction: column; gap: 0.4rem; min-width: 14rem; }
.field input { font-family: ui-monospace, monospace; font-size: 1.2rem; letter-spacing: 0.08em; }
.choice { display: flex; flex-wrap: wrap; gap: 1rem; padding: 0.6rem 0.9rem; border: 1px solid var(--p-content-border-color); border-radius: 0.5rem; }
.choice legend { padding: 0 0.3rem; color: var(--p-text-muted-color); font-size: 0.85rem; }
</style>
