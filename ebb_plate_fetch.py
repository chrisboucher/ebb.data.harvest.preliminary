#!/usr/bin/env python3
"""EBB test plate: fetch, hash and normalise the four climate series.

Usage:
  python ebb_plate_fetch.py # download all sources, then parse
  python ebb_plate_fetch.py --offline # re-parse files already in raw/

Outputs (in --out, default ./plate_data):
  raw/<id>.<ext> untouched source files, exactly as downloaded
  plate_series.csv series,t,value,unc,units,source_id (t = decimal year)
  manifest.json provenance: url, retrieval time, SHA-256, coverage, splice offset

Standard library only. Parsers fail loudly if a source changes its format.
"""
import argparse, csv, hashlib, json, re, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

SOURCES = {
    "co2_mlo": dict(
        series="co2", units="ppm", ext="csv",
        title="NOAA GML Mauna Loa monthly mean CO2",
        url="https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_mm_mlo.csv"),
    "gistemp": dict(
        series="temp_anomaly", units="degC vs 1951-1980", ext="csv",
        title="NASA GISTEMP v4 global land-ocean temperature anomaly",
        url="https://data.giss.nasa.gov/gistemp/tabledata_v4/GLB.Ts+dSST.csv"),
    "gmsl_recon": dict(
        series="sea_level_recon", units="mm", ext="csv",
        title="CSIRO reconstructed GMSL (Church & White 2011)",
        url="https://datahub.io/core/sea-level-rise/_r/-/data/CSIRO_Recons_gmsl_mo_2015.csv"),
    # NOAA STAR LSA (the original choice) 404s / is frozen at 2025-02; AVISO is
    # the same multi-mission altimetry record and is what the EBB archive uses.
    "gmsl_sat": dict(
        series="sea_level_sat", units="mm (aligned to recon)", ext="txt",
        title="AVISO merged multi-mission satellite altimetry GMSL (GIA-adjusted, 2-month filter, NRT)",
        url="ftp://ftp.aviso.altimetry.fr/pub/oceano/AVISO/indicators/msl/"
            "MSL_Serie_MERGED_Global_AVISO_GIA_Adjust_Filter2m_NRT.txt"),
    "ohc_2000": dict(
        series="ocean_heat_0_2000m", units="1e22 J", ext="dat",
        title="NOAA NCEI World Ocean heat content 0-2000 m, yearly",
        url="https://www.ncei.noaa.gov/data/oceans/woa/DATA_ANALYSIS/3M_HEAT_CONTENT/DATA/basin/yearly/h22-w0-2000m.dat"),
}

# Loose plausibility ranges: catch format changes, not science.
RANGES = {"co2_mlo": (250, 600), "gistemp": (-3, 3), "gmsl_recon": (-400, 400),
          "gmsl_sat": (-400, 400), "ohc_2000": (-100, 100)}


def _num(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()


def _decimal_year(s):
    s = s.strip()
    m = re.fullmatch(r"(\d{4})-([A-Za-z]{3})", s) # CSIRO recon: '1880-Jan' = mid-month
    if m:
        return int(m.group(1)) + (MONTHS.index(m.group(2).lower()) + 0.5) / 12
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return float(s)
    d = datetime(*map(int, m.groups()))
    start, end = datetime(d.year, 1, 1), datetime(d.year + 1, 1, 1)
    return d.year + (d - start).total_seconds() / (end - start).total_seconds()


def parse_co2(text):
    out = []
    for row in csv.reader(l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")):
        if len(row) < 4 or not (row[0].strip().isdigit() and _num(row[3])):
            continue # header row
        y, m, v = int(row[0]), int(row[1]), float(row[3])
        if v < 0:
            continue # -99.99 = missing
        unc = float(row[7]) if len(row) > 7 and _num(row[7]) and float(row[7]) >= 0 else None
        out.append((y + (m - 0.5) / 12, v, unc))
    return out


def parse_gistemp(text):
    rows = list(csv.reader(text.splitlines()))
    i = next(k for k, r in enumerate(rows) if r and r[0].strip() == "Year")
    hdr = [h.strip() for h in rows[i]]
    cols = [hdr.index(m) for m in ("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())]
    out = []
    for r in rows[i + 1:]:
        if not r or not r[0].strip().isdigit():
            continue
        y = int(r[0])
        for m, j in enumerate(cols):
            s = r[j].strip() if j < len(r) else ""
            if _num(s): # '***' = missing
                out.append((y + (m + 0.5) / 12, float(s), None))
    return out


def parse_recon(text):
    rdr = csv.DictReader(text.splitlines())
    keys = {k.lower(): k for k in rdr.fieldnames}
    tk = next(keys[k] for k in keys if "time" in k or "year" in k)
    vk = next(keys[k] for k in keys if "gmsl" in k and "uncert" not in k)
    uk = next((keys[k] for k in keys if "uncert" in k), None)
    out = []
    for r in rdr:
        if _num(r[vk]):
            unc = float(r[uk]) if uk and _num(r[uk]) else None
            out.append((_decimal_year(r[tk]), float(r[vk]), unc))
    return out


def parse_sat(text):
    """AVISO: 'decimal_year gmsl_m uncertainty_m' per line, no header; ~10-day steps."""
    out = []
    for line in text.splitlines():
        p = line.split()
        if len(p) >= 2 and all(_num(x) for x in p[:2]):
            unc = float(p[2]) * 1000 if len(p) > 2 and _num(p[2]) else None
            out.append((float(p[0]), float(p[1]) * 1000, unc)) # m -> mm
    return out


def parse_ohc(text):
    out = []
    for line in text.splitlines():
        p = line.split()
        if len(p) >= 3 and all(_num(x) for x in p[:3]):
            out.append((float(p[0]), float(p[1]), float(p[2]))) # year+0.5, World, World s.e.
    return out


PARSERS = {"co2_mlo": parse_co2, "gistemp": parse_gistemp, "gmsl_recon": parse_recon,
           "gmsl_sat": parse_sat, "ohc_2000": parse_ohc}


def check(sid, recs):
    if len(recs) < 10:
        sys.exit(f"[{sid}] only {len(recs)} records parsed - source format may have changed")
    lo, hi = RANGES[sid]
    bad = [r for r in recs if not lo <= r[1] <= hi]
    if bad:
        sys.exit(f"[{sid}] {len(bad)} values outside {lo}..{hi}, e.g. {bad[0]} - check units/format")


def annual(recs):
    by = {}
    for t, v, _ in recs:
        by.setdefault(int(t), []).append(v)
    return {y: mean(v) for y, v in by.items()}


def splice_offset(recon, sat):
    """Offset that aligns satellite GMSL to the reconstruction over their common full years."""
    a, b = annual(recon), annual(sat)
    common = sorted(set(a) & set(b))[1:-1] # drop partial first/last years
    if len(common) < 3:
        return None, common
    return mean(a[y] - b[y] for y in common), common


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "EBB-plate-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data")
    ap.add_argument("--offline", action="store_true", help="parse existing raw/ files, no network")
    args = ap.parse_args()

    out = Path(args.out)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old = json.loads((out / "manifest.json").read_text()) if (out / "manifest.json").exists() else {}
    manifest = {"generated_utc": now, "sources": {}}
    data = {}

    for sid, src in SOURCES.items():
        path = raw / f"{sid}.{src['ext']}"
        if args.offline:
            if not path.exists():
                sys.exit(f"[{sid}] missing {path}")
            blob = path.read_bytes()
            retrieved = old.get("sources", {}).get(sid, {}).get("retrieved_utc")
        else:
            print(f"fetching {sid} ...", flush=True)
            blob = fetch(src["url"])
            path.write_bytes(blob)
            retrieved = now
        recs = sorted(PARSERS[sid](blob.decode("utf-8", "replace")))
        check(sid, recs)
        data[sid] = recs
        manifest["sources"][sid] = {
            "title": src["title"], "url": src["url"], "units": src["units"],
            "retrieved_utc": retrieved, "sha256": hashlib.sha256(blob).hexdigest(),
            "bytes": len(blob), "records": len(recs),
            "first_t": round(recs[0][0], 3), "last_t": round(recs[-1][0], 3)}
        print(f" {sid}: {len(recs)} records, {recs[0][0]:.2f}-{recs[-1][0]:.2f}")

    off, years = splice_offset(data["gmsl_recon"], data["gmsl_sat"])
    if off is None:
        print("WARNING: no recon/satellite overlap - satellite sea level left unaligned")
    else:
        data["gmsl_sat"] = [(t, v + off, u) for t, v, u in data["gmsl_sat"]]
        print(f" sea-level splice: satellite shifted {off:+.2f} mm ({years[0]}-{years[-1]} overlap)")
    manifest["sea_level_splice"] = {"offset_mm_added_to_sat": None if off is None else round(off, 3),
                                    "overlap_years": years}

    with open(out / "plate_series.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["series", "t", "value", "unc", "units", "source_id"])
        for sid, recs in data.items():
            s = SOURCES[sid]
            for t, v, u in recs:
                w.writerow([s["series"], f"{t:.4f}", f"{v:.4f}", "" if u is None else f"{u:.4f}", s["units"], sid])
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"wrote {out/'plate_series.csv'} and {out/'manifest.json'}")


if __name__ == "__main__":
    main()
