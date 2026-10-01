import type { Info } from "../types";

/** The banner shown while AI steps run on the mock provider; null while they use Claude. */
export function mockNotice(info: Info): string | null {
  if (!info.mock) return null;
  const toUseClaude = info.mock_requested
    ? "restart without --mock or CEREBELLUM_MOCK to use Claude"
    : "set ANTHROPIC_API_KEY to use Claude";
  return `${info.mode} — AI steps return the outputs from each step's mock rules; ${toUseClaude}.`;
}
