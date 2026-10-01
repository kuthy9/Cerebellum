import { describe, expect, it } from "vitest";
import { APPROVER_KEY, createNameStore } from "./approver";

function memoryStorage(initial: Record<string, string> = {}) {
  const items = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
  };
}

describe("createNameStore", () => {
  it("starts from the name remembered under the approver key", () => {
    const store = createNameStore(() => memoryStorage({ [APPROVER_KEY]: "dana" }));
    expect(APPROVER_KEY).toBe("cerebellum.approver");
    expect(store.get()).toBe("dana");
  });

  it("shows a name typed in one form to every other form at once", () => {
    const storage = memoryStorage();
    const store = createNameStore(() => storage);
    const heard: string[] = [];
    const first = store.subscribe(() => heard.push(`first:${store.get()}`));
    store.subscribe(() => heard.push(`second:${store.get()}`));
    store.set("erin");
    expect(heard).toEqual(["first:erin", "second:erin"]);
    expect(storage.getItem(APPROVER_KEY)).toBe("erin");
    first();
    store.set("fay");
    expect(heard.slice(2)).toEqual(["second:fay"]);
  });

  it("still shares the name when storage is unavailable", () => {
    const store = createNameStore(() => {
      throw new Error("SecurityError: storage is disabled");
    });
    expect(store.get()).toBe("");
    let heard = 0;
    store.subscribe(() => heard++);
    store.set("gus");
    expect(store.get()).toBe("gus");
    expect(heard).toBe(1);
  });
});
