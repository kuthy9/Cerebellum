/** localStorage key of the name recorded with decisions and task resolutions. */
export const APPROVER_KEY = "cerebellum.approver";

type NameStorage = Pick<Storage, "getItem" | "setItem">;

export interface NameStore {
  get(): string;
  set(name: string): void;
  subscribe(listener: () => void): () => void;
}

/** One name shared by every form that subscribes, so typing it in one shows it in all of them;
 *  remembered in `storage` when the browser allows it. */
export function createNameStore(storage: () => NameStorage, key: string = APPROVER_KEY): NameStore {
  let name: string | null = null; // read on first use
  const listeners = new Set<() => void>();
  return {
    get() {
      if (name === null) {
        try {
          name = storage().getItem(key) ?? "";
        } catch {
          name = ""; // storage unavailable (private mode)
        }
      }
      return name;
    },
    set(value) {
      name = value;
      try {
        storage().setItem(key, value);
      } catch {
        // storage unavailable (private mode); the name just isn't remembered
      }
      for (const listener of [...listeners]) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
