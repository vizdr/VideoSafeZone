import { onMounted, ref, type Ref } from "vue";
import { errorText, type ApiResult } from "../api/client";

/** One API read for a view: the data, an error to show, a reload. */
export function useLoad<T>(read: () => Promise<ApiResult<T>>): {
  data: Ref<T | null>; error: Ref<string>; loading: Ref<boolean>; reload: () => Promise<void>;
} {
  const data = ref<T | null>(null) as Ref<T | null>;
  const error = ref("");
  const loading = ref(false);
  async function reload(): Promise<void> {
    loading.value = true;
    try {
      const res = await read();
      if (res.ok) {
        data.value = res.data as T;
        error.value = "";
      } else {
        error.value = errorText(res);
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      loading.value = false;
    }
  }
  onMounted(reload);
  return { data, error, loading, reload };
}
