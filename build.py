#!/usr/bin/env python3
"""멍청한 돈(한국어판) 데이터 생성기 — 표준 라이브러리만 사용.

사용법: python3 build.py OUTDIR [--prev PREV_traders.json]
출력: OUTDIR/data.json (메타·코인·피드), OUTDIR/traders.json (트레이더·포지션)
"""
import json, sys, time, threading, urllib.request, urllib.error, os
from concurrent.futures import ThreadPoolExecutor

API = "https://api.hyperliquid.xyz/info"
LB = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
MIN_AV = 1000          # 계좌가치 $1K 미만은 포지션 조회 생략
TIERS = [("genius", 2e6, None), ("smart", 2e5, 2e6), ("stupid", -1e6, -1e5), ("worst", None, -1e6)]


def tier_of(p):
    if p >= 2e6: return "genius"
    if p >= 2e5: return "smart"
    if p <= -1e6: return "worst"
    if p <= -1e5: return "stupid"
    return "mid"


def post(body, tries=6):
    data = json.dumps(body).encode()
    for i in range(tries):
        try:
            req = urllib.request.Request(API, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 + i * 3); continue
            if i == tries - 1: raise
            time.sleep(1 + i)
        except Exception:
            if i == tries - 1: raise
            time.sleep(1 + i)


class Limiter:
    def __init__(self, rate):
        self.gap = 1.0 / rate; self.lock = threading.Lock(); self.t = 0.0
    def wait(self):
        with self.lock:
            now = time.time(); t = max(now, self.t); self.t = t + self.gap
        if t > now: time.sleep(t - now)


def r2(x, n=2):
    return round(float(x), n)


def main():
    out = sys.argv[1]
    prev = None
    if "--prev" in sys.argv:
        p = sys.argv[sys.argv.index("--prev") + 1]
        if os.path.exists(p):
            try: prev = json.load(open(p))
            except Exception: prev = None
    os.makedirs(out, exist_ok=True)
    t0 = time.time()

    # 1) 리더보드
    with urllib.request.urlopen(LB, timeout=180) as r:
        rows = json.load(r)["leaderboardRows"]
    lb = []
    for r in rows:
        w = dict(r["windowPerformances"])
        lb.append({
            "a": r["ethAddress"], "n": r.get("displayName") or "",
            "av": float(r["accountValue"]),
            "pa": float(w["allTime"]["pnl"]), "pm": float(w["month"]["pnl"]),
            "pw": float(w["week"]["pnl"]), "pd": float(w["day"]["pnl"]),
            "v": float(w["allTime"]["vlm"]), "roi": float(w["allTime"]["roi"]),
        })
    lb.sort(key=lambda x: -x["pa"])
    total = len(lb)
    for i, x in enumerate(lb):
        x["rk"] = i + 1; x["t"] = tier_of(x["pa"])
    tier_counts = {}
    for x in lb: tier_counts[x["t"]] = tier_counts.get(x["t"], 0) + 1

    # 2) 시장 정보
    meta, ctxs = post({"type": "metaAndAssetCtxs"})
    market = {}
    for u, c in zip(meta["universe"], ctxs):
        if u.get("isDelisted"): continue
        mark = float(c.get("markPx") or 0); prev_px = float(c.get("prevDayPx") or 0)
        market[u["name"]] = {
            "px": mark, "chg": (mark / prev_px - 1) * 100 if prev_px else 0,
            "oi": float(c.get("openInterest") or 0) * mark,
            "fund": float(c.get("funding") or 0) * 100,  # 시간당 %
            "vol": float(c.get("dayNtlVlm") or 0), "ml": u.get("maxLeverage", 0),
        }

    # 3) 등급 트레이더 포지션 조회
    targets = [x for x in lb if x["t"] != "mid" and x["av"] >= MIN_AV]
    lim = Limiter(9)
    done = [0]

    def fetch(x):
        lim.wait()
        try:
            st = post({"type": "clearinghouseState", "user": x["a"]})
        except Exception:
            return x, None
        done[0] += 1
        if done[0] % 500 == 0:
            print(f"  {done[0]}/{len(targets)} ({time.time()-t0:.0f}s)", flush=True)
        return x, st

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(fetch, targets))

    traders = []
    failed = 0
    for x, st in results:
        if st is None:
            failed += 1; continue
        pos = []
        for ap in st.get("assetPositions", []):
            p = ap["position"]; szi = float(p["szi"])
            if szi == 0: continue
            lev = p.get("leverage", {})
            liq = p.get("liquidationPx")
            pos.append([p["coin"], 1 if szi > 0 else -1, abs(szi), float(p["entryPx"] or 0),
                        r2(p["positionValue"]), r2(p["unrealizedPnl"]), int(lev.get("value", 0)),
                        float(liq) if liq else 0, 1 if lev.get("type") == "cross" else 0])
        pos.sort(key=lambda q: -q[4])
        x2 = dict(x); x2["av"] = r2(st.get("marginSummary", {}).get("accountValue", x["av"]))
        x2["p"] = pos
        for k in ("pa", "pm", "pw", "pd", "v"): x2[k] = round(x2[k])
        x2["roi"] = round(x2["roi"], 4)
        traders.append(x2)

    # 4) 코인 집계
    coins = {}
    def C(name):
        if name not in coins:
            m = market.get(name, {"px": 0, "chg": 0, "oi": 0, "fund": 0, "vol": 0, "ml": 0})
            coins[name] = {"c": name, **{k: m[k] for k in ("px", "chg", "oi", "fund", "vol", "ml")},
                           "g": {t: [0, 0, 0, 0] for t, *_ in TIERS}, "top": []}
        return coins[name]
    near = []
    for ti, tr in enumerate(traders):
        for q in tr["p"]:
            coin, side, sz, ent, ntl, upnl, lev, liq, cross = q
            if coin not in market: continue
            c = C(coin); g = c["g"][tr["t"]]
            if side > 0: g[0] += ntl; g[1] += 1
            else: g[2] += ntl; g[3] += 1
            c["top"].append([ti, side, sz, ent, ntl, upnl, lev, liq])
            px = market[coin]["px"]
            if liq and px and ntl >= 20000:
                dist = (px - liq) / px * 100 * side  # 롱: 가격이 청산가 위 몇 %
                if 0 < dist <= 5:
                    near.append([ti, coin, side, ntl, upnl, lev, liq, round(dist, 2)])
    clist = []
    for c in coins.values():
        c["top"].sort(key=lambda q: -q[4]); c["top"] = c["top"][:40]
        for t in c["g"]: c["g"][t] = [round(c["g"][t][0]), c["g"][t][1], round(c["g"][t][2]), c["g"][t][3]]
        sm = [c["g"]["genius"][i] + c["g"]["smart"][i] for i in range(4)]
        du = [c["g"]["stupid"][i] + c["g"]["worst"][i] for i in range(4)]
        c["smNet"] = sm[0] - sm[2]; c["duNet"] = du[0] - du[2]
        c["smTot"] = sm[0] + sm[2]; c["duTot"] = du[0] + du[2]
        c["px"] = float(f"{c['px']:.6g}"); c["chg"] = round(c["chg"], 2)
        c["oi"] = round(c["oi"]); c["vol"] = round(c["vol"]); c["fund"] = round(c["fund"], 5)
        clist.append(c)
    clist.sort(key=lambda c: -(c["smTot"] + c["duTot"]))
    for i, c in enumerate(sorted(clist, key=lambda c: -c["smTot"])): c["smRk"] = i + 1
    near.sort(key=lambda q: q[7])

    # 5) 직전 스냅샷 대비 변동 피드
    feed = []
    prev_at = None
    if prev:
        prev_at = prev.get("at")
        pmap = {}
        for tr in prev.get("traders", []):
            for q in tr.get("p", []):
                pmap[(tr["a"], q[0])] = q
        idx = {tr["a"]: i for i, tr in enumerate(traders)}
        prev_tr = {tr["a"] for tr in prev.get("traders", [])}
        cur = {}
        for i, tr in enumerate(traders):
            for q in tr["p"]: cur[(tr["a"], q[0])] = (i, q)
        for key, (i, q) in cur.items():
            if key[0] not in prev_tr: continue
            o = pmap.get(key)
            if o is None or o[1] != q[1]:
                if q[4] >= 50000: feed.append([i, q[0], "open", q[1], q[4], 0, q[3]])
            else:
                d = q[2] - o[2]
                if abs(d) / max(o[2], 1e-12) >= 0.2 and abs(d) * market.get(q[0], {}).get("px", 0) >= 50000:
                    feed.append([i, q[0], "add" if d > 0 else "cut", q[1], q[4], round(abs(d) * market[q[0]]["px"]), q[3]])
        for key, o in pmap.items():
            if key not in cur and key[0] in idx and o[4] >= 50000:
                feed.append([idx[key[0]], key[1], "close", o[1], o[4], 0, o[3]])
        feed.sort(key=lambda f: -max(f[4], f[5]))
        feed = feed[:300]

    at = int(time.time())
    data = {"at": at, "prevAt": prev_at, "total": total, "tierCounts": tier_counts,
            "tracked": len(traders), "failed": failed, "minAv": MIN_AV,
            "coins": clist, "near": near[:200], "feed": feed,
            "tr": [[t["a"], t["n"], t["rk"], t["t"], t["pa"], round(t["av"]), t["pm"], len(t["p"]),
                    round(sum(q[4] for q in t["p"]))] for t in traders]}
    json.dump(data, open(os.path.join(out, "data.json"), "w"), separators=(",", ":"))
    json.dump({"at": at, "traders": traders}, open(os.path.join(out, "traders.json"), "w"), separators=(",", ":"))
    print(f"done: traders={len(traders)} failed={failed} coins={len(clist)} near={len(near)} feed={len(feed)} {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
