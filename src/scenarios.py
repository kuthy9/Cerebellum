"""
scenarios.py — Three real-world scenarios that show Cerebellum's actual value
  1. AI Research Pipeline  — deep research report with parallel agents
  2. Trading Signal Runner — live quant pipeline with budget guard + fallback
  3. Crash & Resume        — 20-step pipeline, crash at step 12, resume exactly
"""
import asyncio, random, time, os
from cerebellum import Cerebellum
from engine import FailPolicy, FailStrategy


# ═══════════════════════════════════════════════════════════════
# SCENARIO 1 — AI Research Pipeline
# Mimics what MiroFish / deep-research tools do internally:
#   search → [extract_facts, extract_entities] → cross_check
#          → [write_intro, write_body, write_conclusion]
#          → edit → publish
# 14 nodes, 3 parallel fan-outs
# ═══════════════════════════════════════════════════════════════

async def scenario_research():
    print("\n" + "█"*55)
    print("  SCENARIO 1 — AI Research Pipeline (14 nodes)")
    print("█"*55)

    async def web_search(inp):
        await asyncio.sleep(0.3)
        return ({"hits": ["paper_A","paper_B","paper_C","paper_D"]}, 0.003)

    async def extract_facts(inp):
        hits = inp["web_search"]["hits"]
        await asyncio.sleep(0.4)
        return ({"facts": [f"fact from {h}" for h in hits]}, 0.006)

    async def extract_entities(inp):
        hits = inp["web_search"]["hits"]
        await asyncio.sleep(0.35)
        return ({"entities": ["OpenAI","Anthropic","DeepMind"]}, 0.005)

    async def cross_check(inp):
        await asyncio.sleep(0.25)
        facts    = inp["extract_facts"]["facts"]
        entities = inp["extract_entities"]["entities"]
        return ({"verified": facts[:2], "entities_mentioned": entities}, 0.008)

    async def write_intro(inp):
        await asyncio.sleep(0.3)
        return ({"text": "Introduction: AI is transforming..."}, 0.005)

    async def write_body(inp):
        verified = inp["cross_check"]["verified"]
        await asyncio.sleep(0.5)
        return ({"text": f"Body: {len(verified)} verified claims..."}, 0.012)

    async def write_conclusion(inp):
        await asyncio.sleep(0.28)
        return ({"text": "Conclusion: The future is..."}, 0.005)

    async def edit(inp):
        sections = [inp.get(k,{}).get("text","")
                    for k in ["write_intro","write_body","write_conclusion"]]
        await asyncio.sleep(0.2)
        full = " ".join(s for s in sections if s)
        return ({"article": full, "word_count": len(full.split())}, 0.004)

    async def seo_check(inp):
        await asyncio.sleep(0.15)
        return ({"score": 87, "suggestions": ["add subheadings"]}, 0.002)

    async def fact_score(inp):
        await asyncio.sleep(0.18)
        return ({"confidence": 0.91}, 0.002)

    async def publish(inp):
        article  = inp["edit"]["article"]
        seo      = inp["seo_check"]["score"]
        fc       = inp["fact_score"]["confidence"]
        await asyncio.sleep(0.1)
        return ({"url": "https://blog.example.com/ai-2026",
                 "seo": seo, "confidence": fc,
                 "preview": article[:80] + "..."}, 0.001)

    c = Cerebellum("research.db")
    (c
     .node("web_search",        web_search)
     .node("extract_facts",     extract_facts,    depends_on=["web_search"])
     .node("extract_entities",  extract_entities, depends_on=["web_search"])
     .node("cross_check",       cross_check,
           depends_on=["extract_facts","extract_entities"])
     .node("write_intro",       write_intro,      depends_on=["cross_check"])
     .node("write_body",        write_body,       depends_on=["cross_check"])
     .node("write_conclusion",  write_conclusion, depends_on=["cross_check"])
     .node("edit",              edit,
           depends_on=["write_intro","write_body","write_conclusion"])
     .node("seo_check",         seo_check,        depends_on=["edit"])
     .node("fact_score",        fact_score,       depends_on=["edit"])
     .node("publish",           publish,
           depends_on=["edit","seo_check","fact_score"])
    )

    t0 = time.time()
    r  = await c.run({"topic": "AI infrastructure 2026"})
    print(r.summary())
    pub = r.output_of("publish")
    print(f"\n  Published → {pub['url']}")
    print(f"  SEO score   {pub['seo']}   Fact confidence {pub['confidence']}")
    print(f"  Preview:  \"{pub['preview']}\"")
    print(f"\n  Wall time {r.elapsed:.2f}s  "
          f"(sequential would be ~{0.3+0.4+0.25+0.5+0.2+0.18+0.1:.2f}s)")


# ═══════════════════════════════════════════════════════════════
# SCENARIO 2 — Trading Signal Pipeline
# Mimics a quant execution system:
#   fetch_prices → [compute_momentum, compute_mean_reversion,
#                   fetch_sentiment] → ensemble_signal
#                → risk_check → execute_order
# Hard budget cap: $0.05 per run (cost-aware scheduling)
# Live API is flaky — falls back to cached data automatically
# ═══════════════════════════════════════════════════════════════

async def scenario_trading():
    print("\n" + "█"*55)
    print("  SCENARIO 2 — Trading Signal Pipeline (budget + fallback)")
    print("█"*55)

    random.seed(7)

    async def fetch_prices_live(inp):
        if random.random() < 0.6:   # 60% chance live feed is down
            raise ConnectionError("Market data feed timeout")
        await asyncio.sleep(0.2)
        return ({"prices": [100,101,99,103,102], "source": "live"}, 0.001)

    async def fetch_prices_cached(inp):
        await asyncio.sleep(0.05)
        return ({"prices": [100,101,99,103,102], "source": "cache"}, 0.0)

    async def compute_momentum(inp):
        prices = inp.get("fetch_prices_live",
                 inp.get("fetch_prices_cached",{})).get("prices",[])
        await asyncio.sleep(0.15)
        mom = (prices[-1] - prices[0]) / prices[0] if prices else 0
        return ({"momentum": round(mom, 4), "signal": "BUY" if mom>0 else "SELL"}, 0.003)

    async def compute_mean_rev(inp):
        prices = inp.get("fetch_prices_live",
                 inp.get("fetch_prices_cached",{})).get("prices",[])
        await asyncio.sleep(0.12)
        avg  = sum(prices)/len(prices) if prices else 0
        last = prices[-1] if prices else 0
        return ({"mean_reversion": round(last - avg, 2),
                 "signal": "BUY" if last < avg else "SELL"}, 0.003)

    async def fetch_sentiment(inp):
        await asyncio.sleep(0.2)
        return ({"sentiment": 0.62, "signal": "BUY"}, 0.008)

    async def ensemble_signal(inp):
        mom  = inp["compute_momentum"]["signal"]
        mr   = inp["compute_mean_rev"]["signal"]
        sent = inp.get("fetch_sentiment",{}).get("signal","NEUTRAL")
        votes = [mom, mr, sent]
        buy  = votes.count("BUY")
        sig  = "BUY" if buy >= 2 else "SELL"
        await asyncio.sleep(0.05)
        return ({"signal": sig, "confidence": buy/3,
                 "votes": {"momentum": mom,"mean_rev": mr,"sentiment": sent}}, 0.002)

    async def risk_check(inp):
        sig  = inp["ensemble_signal"]["signal"]
        conf = inp["ensemble_signal"]["confidence"]
        await asyncio.sleep(0.05)
        approved = conf > 0.5
        return ({"approved": approved, "signal": sig,
                 "reason": "confidence OK" if approved else "low confidence"}, 0.001)

    async def execute_order(inp):
        rc = inp["risk_check"]
        await asyncio.sleep(0.08)
        if not rc["approved"]:
            return ({"status": "SKIPPED", "reason": rc["reason"]}, 0.0)
        return ({"status": "FILLED", "signal": rc["signal"],
                 "qty": 100, "price": 102.50}, 0.001)

    c = Cerebellum("trading.db")
    (c
     .node("fetch_prices_cached", fetch_prices_cached)
     .node("fetch_prices_live",   fetch_prices_live)
     .on_fail("fetch_prices_live", fallback="fetch_prices_cached", timeout=5.0)
     .node("compute_momentum", compute_momentum,
           depends_on=["fetch_prices_live"])
     .node("compute_mean_rev", compute_mean_rev,
           depends_on=["fetch_prices_live"])
     .node("fetch_sentiment",  fetch_sentiment,
           depends_on=["fetch_prices_live"])
     .on_fail("fetch_sentiment", skip=True)   # sentiment is optional
     .node("ensemble_signal",  ensemble_signal,
           depends_on=["compute_momentum","compute_mean_rev","fetch_sentiment"])
     .node("risk_check",       risk_check,    depends_on=["ensemble_signal"])
     .node("execute_order",    execute_order, depends_on=["risk_check"])
    )

    r = await c.run({"ticker": "NVDA"}, budget_usd=0.05)
    print(r.summary())
    order = r.output_of("execute_order")
    ens   = r.output_of("ensemble_signal")
    src   = (r.output_of("fetch_prices_live") or
             r.output_of("fetch_prices_cached") or {}).get("source","?")
    print(f"\n  Price source   : {src}")
    if ens:
        print(f"  Signal votes   : {ens['votes']}")
        print(f"  Ensemble       : {ens['signal']}  confidence={ens['confidence']:.0%}")
    if order:
        print(f"  Order status   : {order['status']}")
        if order['status'] == 'FILLED':
            print(f"  Execution      : {order['qty']} shares @ ${order['price']}")


# ═══════════════════════════════════════════════════════════════
# SCENARIO 3 — Crash & Resume
# Long 8-step pipeline. Simulate a crash mid-way.
# Cerebellum resumes exactly from the last checkpoint —
# completed nodes are NOT re-run. Zero wasted API cost.
# ═══════════════════════════════════════════════════════════════

async def scenario_crash_resume():
    print("\n" + "█"*55)
    print("  SCENARIO 3 — Crash mid-pipeline, resume without re-running")
    print("█"*55)

    CRASH_AT = "step_05"
    call_log = []

    def make_step(name: str, cost: float, sleep: float):
        async def step(inp):
            call_log.append(name)
            await asyncio.sleep(sleep)
            if name == CRASH_AT:
                raise RuntimeError("Simulated server crash / OOM")
            return ({f"{name}_out": f"{name}_result"}, cost)
        step.__name__ = name
        return step

    steps = [
        ("step_01", 0.002, 0.10),
        ("step_02", 0.003, 0.12),
        ("step_03", 0.004, 0.08),
        ("step_04", 0.003, 0.10),
        ("step_05", 0.000, 0.05),  # crashes here
        ("step_06", 0.004, 0.10),
        ("step_07", 0.003, 0.09),
        ("step_08", 0.005, 0.11),
    ]

    DB = "crash_resume.db"
    if os.path.exists(DB): os.remove(DB)
    RUN_ID = "pipeline-42"

    # ── First run: crashes at step_05 ────────────────────────────────────────
    print("\n  [RUN 1] Starting pipeline — will crash at step_05...")
    c1 = Cerebellum(DB)
    prev = None
    for name, cost, sleep in steps:
        deps = [prev] if prev else []
        c1.node(name, make_step(name, cost, sleep), depends_on=deps)
        prev = name

    r1 = await c1.run(run_id=RUN_ID)
    cost_run1 = r1.total_cost
    print(f"\n  Run 1 result  : {'OK' if r1.success else 'FAILED'}")
    print(f"  Nodes called  : {call_log}")
    print(f"  Completed     : {sorted(r1.completed)}")
    print(f"  Cost so far   : ${cost_run1:.4f}")

    # ── Fix the crash, resume ─────────────────────────────────────────────────
    print(f"\n  [RUN 2] Resuming from last checkpoint (skip completed nodes)...")
    call_log.clear()
    CRASH_AT = "__none__"   # crash is fixed

    c2 = Cerebellum(DB)
    prev = None
    for name, cost, sleep in steps:
        deps = [prev] if prev else []
        c2.node(name, make_step(name, cost, sleep), depends_on=deps)
        prev = name
    c2.on_fail("step_05", retry=1)

    r2 = await c2.run(run_id=RUN_ID, resume=True)
    print(f"\n  Run 2 result  : {'OK' if r2.success else 'FAILED'}")
    print(f"  Nodes called  : {call_log}")
    print(f"  Re-ran        : {len(call_log)} nodes (NOT {len(steps)-len(call_log)} already done)")
    print(f"  Cost run 2    : ${r2.total_cost - cost_run1:.4f}  (saved ${cost_run1:.4f})")
    print(r2.summary())


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    await scenario_research()
    await scenario_trading()
    await scenario_crash_resume()
    print("\n" + "█"*55)
    print("  All scenarios complete.")
    print("█"*55)

asyncio.run(main())