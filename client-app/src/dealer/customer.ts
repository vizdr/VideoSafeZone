// What a customer's pages share (DEALER-PANEL-PLAN.md §7.1): CustomerView loads the customer and its
// adapters once -- each adapter with its plan, cameras and plan vs. actual -- and its pages read them.
import { computed, type InjectionKey, type Ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { CustomerAdapters, CustomerDetail } from "../api/types";

export interface CustomerContext {
  customerId: Ref<string>;
  detail: Ref<CustomerDetail | null>;
  adapters: Ref<CustomerAdapters | null>;
  error: Ref<string>;
  reload: () => Promise<void>;
}

export const CUSTOMER: InjectionKey<CustomerContext> = Symbol("customer");

/** "Home · Garage — adapter-0002": where an adapter is, its customer's name for it, its Thing name. */
export function adapterLabel(a: { site: { name: string } | string; name: string | null; thingName: string }): string {
  const site = typeof a.site === "string" ? a.site : a.site.name;
  return `${site}${a.name ? ` · ${a.name}` : ""} — ${a.thingName}`;
}

/** The adapter a page shows: the route's :adapterId, else the customer's first; choose() switches. */
export function useAdapterChoice(ctx: CustomerContext, routeName: string) {
  const route = useRoute();
  const router = useRouter();
  const list = computed(() => ctx.adapters.value?.adapters ?? []);
  const selected = computed(() => list.value.find(a => a.id === route.params.adapterId) ?? list.value[0] ?? null);
  const choose = (adapterId: string) =>
    router.replace({ name: routeName, params: { customerId: ctx.customerId.value, adapterId } });
  return { list, selected, choose };
}
