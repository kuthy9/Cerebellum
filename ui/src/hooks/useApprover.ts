import { useState } from "react";

const KEY = "cerebellum.approver";

/** The name recorded with decisions and task resolutions, remembered in this browser. */
export function useApprover(): [string, (name: string) => void] {
  const [name, setName] = useState(() => {
    try {
      return window.localStorage.getItem(KEY) ?? "";
    } catch {
      return "";
    }
  });
  const update = (value: string) => {
    setName(value);
    try {
      window.localStorage.setItem(KEY, value);
    } catch {
      // storage unavailable (private mode); the name just isn't remembered
    }
  };
  return [name, update];
}
