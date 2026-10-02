import { describe, expect, it } from "vitest";
import type { Info } from "../types";
import { mockNotice } from "./info";

const info = (over: Partial<Info>): Info => ({
  version: "0.1.0",
  mode: "Claude API (claude-opus-5-5)",
  mock: false,
  mock_requested: false,
  model: "claude-opus-5-5",
  ...over,
});

describe("mockNotice", () => {
  it("says nothing while AI steps use Claude", () => {
    expect(mockNotice(info({}))).toBeNull();
  });

  it("points at credentials when the mock is a fallback", () => {
    const notice = mockNotice(info({ mock: true, mode: "mock AI (no Anthropic credentials found)" }));
    expect(notice).toContain("mock AI (no Anthropic credentials found)");
    expect(notice).toContain("set ANTHROPIC_API_KEY to use Claude");
  });

  it("does not ask for a key when the mock was requested", () => {
    const notice = mockNotice(info({ mock: true, mock_requested: true, mode: "mock AI (requested)" }));
    expect(notice).toContain("mock AI (requested)");
    expect(notice).toContain("--mock");
    expect(notice).toContain("CEREBELLUM_MOCK");
    expect(notice).not.toContain("ANTHROPIC_API_KEY");
  });
});
