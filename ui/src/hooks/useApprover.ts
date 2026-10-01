import { useSyncExternalStore } from "react";
import { createNameStore } from "../lib/approver";

const approver = createNameStore(() => window.localStorage);

/** The name recorded with decisions and task resolutions: one name shared by every form in the
 *  dashboard, remembered in this browser. */
export function useApprover(): [string, (name: string) => void] {
  return [useSyncExternalStore(approver.subscribe, approver.get), approver.set];
}
