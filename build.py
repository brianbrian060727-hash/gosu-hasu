#!/usr/bin/env python3
"""멍청한 돈(한국어판) 데이터 생성기 — 표준 라이브러리만 사용.

시장(dex)별 수집과 합치기를 나눠, 깃허브에서 시장마다 따로(병렬로) 돌릴 수 있습니다.

  python3 build.py fetch PART.json --dex main|xyz|io
  python3 build.py merge OUTDIR [--prev PREV_traders.json] PART.json [PART.json ...]
  python3 build.py all OUTDIR [--prev PREV_traders.json] [--dexes main,xyz,io]   # 한 번에 순서대로

출력: OUTDIR/data.json (메타·코인·피드), OUTDIR/traders.json (트레이더·포지션)
"""
import json, sys, time, threading, urllib.request, urllib.error, os
from concurrent.futures import ThreadPoolExecutor

API = "https://api.hyperliquid.xyz/info"
LB = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
MIN_AV = 1000          # 리더보드 계좌가치 $1K 미만은 조회 생략
TIERS = ["genius", "smart", "stupid", "worst"]
DEX_LABEL = {"": "코인", "xyz": "주식·원자재", "io": "비상장·기타"}


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


def load_leaderboard():
    with urllib.request.urlopen(LB, timeout=180) as r:
        rows = json.load(r)["leaderboardRows"]
    lb = []
    for r in rows:
        w = dict(r["windowPerformances"])
        lb.append({
            "a": r["ethAddress"].lower(), "n": r.get("displayName") or "",
            "av": float(r["accountValue"]),
            "pa": float(w["allTime"]["pnl"]), "pm": float(w["month"]["pnl"]),
            "pw": float(w["week"]["pnl"]), "pd": float(w["day"]["pnl"]),
            "v": float(w["allTime"]["vlm"]), "roi": float(w["allTime"]["roi"]),
        })
    lb.sort(key=lambda x: -x["pa"])
    for i, x in enumerate(lb):
        x["rk"] = i + 1; x["t"] = tier_of(x["pa"])
    return lb


def targets_of(lb):
    t = [x for x in lb if x["t"] != "mid" and x["av"] >= MIN_AV]
    n = int(os.environ.get("HL_LIMIT", "0"))   # 테스트용: 앞에서 n명만
    return t[:n] if n else t


def market_of(dex):
    body = {"type": "metaAndAssetCtxs"}
    if dex: body["dex"] = dex
    meta, ctxs = post(body)
    market = {}
    for u, c in zip(meta["universe"], ctxs):
        if u.get("isDelisted"): continue
        mark = float(c.get("markPx") or 0); prev_px = float(c.get("prevDayPx") or 0)
        market[u["name"]] = {
            "px": mark, "chg": (mark / prev_px - 1) * 100 if prev_px else 0,
            "oi": float(c.get("openInterest") or 0) * mark,
            "fund": float(c.get("funding") or 0) * 100,  # 시간당 %
            "vol": float(c.get("dayNtlVlm") or 0), "ml": u.get("maxLeverage", 0), "d": dex,
        }
    return market


# ---------------------------------------------------------------- fetch
def fetch(out, dex):
    t0 = time.time()
    lb = load_leaderboard()
    targets = targets_of(lb)
    market = market_of(dex)
    lim = Limiter(9)
    done = [0]

    def one(x):
        lim.wait()
        body = {"type": "clearinghouseState", "user": x["a"]}
        if dex: body["dex"] = dex
        try:
            st = post(body)
        except Exception:
            return x["a"], None
        done[0] += 1
        if done[0] % 1000 == 0:
            print(f"  [{dex or 'main'}] {done[0]}/{len(targets)} ({time.time()-t0:.0f}s)", flush=True)
        return x["a"], st

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(one, targets))

    pos, av, failed = {}, {}, 0
    for a, st in results:
        if st is None:
            failed += 1; continue
        av[a] = r2(st.get("marginSummary", {}).get("accountValue", 0))
        ps = []
        for ap in st.get("assetPositions", []):
            p = ap["position"]; szi = float(p["szi"])
            if szi == 0: continue
            lev = p.get("leverage", {}); liq = p.get("liquidationPx")
            ps.append([p["coin"], 1 if szi > 0 else -1, abs(szi), float(p["entryPx"] or 0),
                       r2(p["positionValue"]), r2(p["unrealizedPnl"]), int(lev.get("value", 0)),
                       float(liq) if liq else 0, 1 if lev.get("type") == "cross" else 0])
        if ps: pos[a] = ps
    json.dump({"dex": dex, "at": int(time.time()), "market": market, "pos": pos, "av": av,
               "queried": len(targets), "failed": failed}, open(out, "w"), separators=(",", ":"))
    print(f"fetch done: dex={dex or 'main'} queried={len(targets)} withPos={len(pos)} failed={failed} {time.time()-t0:.0f}s")


# ---------------------------------------------------------------- merge
def merge(out, parts, prev_path=None):
    os.makedirs(out, exist_ok=True)
    prev = None
    if prev_path and os.path.exists(prev_path):
        try: prev = json.load(open(prev_path))
        except Exception: prev = None
    P = [json.load(open(p)) for p in parts if os.path.exists(p)]
    if not any(p["dex"] == "" for p in P):
        sys.exit("기본 시장(main) 수집 결과가 없어 합칠 수 없습니다")

    lb = load_leaderboard()
    total = len(lb)
    tier_counts = {}
    for x in lb: tier_counts[x["t"]] = tier_counts.get(x["t"], 0) + 1
    targets = targets_of(lb)

    market = {}
    for p in P: market.update(p["market"])
    main = next(p for p in P if p["dex"] == "")

    traders = []
    for x in targets:
        a = x["a"]
        if a not in main["av"]:      # 기본 시장 조회 실패·명단 변동
            continue
        ps = []
        for p in P: ps += p["pos"].get(a, [])
        ps.sort(key=lambda q: -q[4])
        t = {k: x[k] for k in ("a", "n", "rk", "t")}
        t["av"] = main["av"][a]
        for k in ("pa", "pm", "pw", "pd", "v"): t[k] = round(x[k])
        t["roi"] = round(x["roi"], 4); t["p"] = ps
        traders.append(t)

    # 코인 집계
    coins = {}
    def C(name):
        if name not in coins:
            m = market[name]
            coins[name] = {"c": name, **{k: m[k] for k in ("px", "chg", "oi", "fund", "vol", "ml", "d")},
                           "g": {t: [0, 0, 0, 0] for t in TIERS}, "top": []}
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
                dist = (px - liq) / px * 100 * side
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

    # 직전 데이터 대비 변동
    feed, prev_at = [], None
    if prev:
        prev_at = prev.get("at")
        pmap = {}
        for tr in prev.get("traders", []):
            for q in tr.get("p", []):
                pmap[(tr["a"].lower(), q[0])] = q
        prev_tr = {tr["a"].lower() for tr in prev.get("traders", [])}
        prev_dex = set(prev.get("dexes", [""]))
        idx = {tr["a"]: i for i, tr in enumerate(traders)}
        cur = {}
        for i, tr in enumerate(traders):
            for q in tr["p"]: cur[(tr["a"], q[0])] = (i, q)
        dex_of = lambda coin: coin.split(":")[0] if ":" in coin else ""
        for key, (i, q) in cur.items():
            if key[0] not in prev_tr or dex_of(q[0]) not in prev_dex: continue
            o = pmap.get(key)
            if o is None or o[1] != q[1]:
                if q[4] >= 50000: feed.append([i, q[0], "open", q[1], q[4], 0, q[3]])
            else:
                d = q[2] - o[2]
                pxm = market.get(q[0], {}).get("px", 0)
                if abs(d) / max(o[2], 1e-12) >= 0.2 and abs(d) * pxm >= 50000:
                    feed.append([i, q[0], "add" if d > 0 else "cut", q[1], q[4], round(abs(d) * pxm), q[3]])
        got_dex = {p["dex"] for p in P}
        for key, o in pmap.items():
            if key not in cur and key[0] in idx and o[4] >= 50000 and dex_of(o[0]) in got_dex:
                feed.append([idx[key[0]], key[1], "close", o[1], o[4], 0, o[3]])
        feed.sort(key=lambda f: -max(f[4], f[5]))
        feed = feed[:300]

    at = int(time.time())
    dexes = sorted(p["dex"] for p in P)
    data = {"at": at, "prevAt": prev_at, "total": total, "tierCounts": tier_counts,
            "tracked": len(traders), "failed": sum(p["failed"] for p in P), "minAv": MIN_AV,
            "dexes": dexes, "dexLabel": {d: DEX_LABEL.get(d, d) for d in dexes},
            "coins": clist, "near": near[:200], "feed": feed,
            "tr": [[t["a"], t["n"], t["rk"], t["t"], t["pa"], round(t["av"]), t["pm"], len(t["p"]),
                    round(sum(q[4] for q in t["p"]))] for t in traders]}
    json.dump(data, open(os.path.join(out, "data.json"), "w"), separators=(",", ":"))
    json.dump({"at": at, "dexes": dexes, "traders": traders},
              open(os.path.join(out, "traders.json"), "w"), separators=(",", ":"))
    print(f"merge done: dexes={dexes} traders={len(traders)} coins={len(clist)} near={len(near)} feed={len(feed)}")


def arg(name, default=None):
    if name in sys.argv:
        return sys.argv[sys.argv.index(name) + 1]
    return default


def main():
    cmd = sys.argv[1]
    if cmd == "fetch":
        d = arg("--dex", "main")
        fetch(sys.argv[2], "" if d == "main" else d)
    elif cmd == "merge":
        skip = {sys.argv.index("--prev"), sys.argv.index("--prev") + 1} if "--prev" in sys.argv else set()
        parts = [a for i, a in enumerate(sys.argv[3:], 3) if i not in skip]
        merge(sys.argv[2], parts, arg("--prev"))
    elif cmd == "all":
        out = sys.argv[2]; os.makedirs(out, exist_ok=True)
        parts = []
        for d in arg("--dexes", "main,xyz,io").split(","):
            p = os.path.join(out, f"part-{d}.json"); fetch(p, "" if d == "main" else d); parts.append(p)
        merge(out, parts, arg("--prev"))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
