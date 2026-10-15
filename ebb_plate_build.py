#!/usr/bin/env python3
"""EBB test plate: build the four plate series, mostly from the EBB archive.

Temperature, satellite sea level and ocean heat come from archived EBB
snapshots (uncertainties from the source's archived raw file). Monthly
Mauna Loa CO2 (1958-) and the CSIRO sea-level reconstruction (1880-2013) are
not EBB sources, so they are read from files already downloaded into
<out>/raw/ by ebb_plate_fetch.py. SHA-256 of every file used goes in the
manifest. Nothing is downloaded.

Usage:
  python ebb_plate_build.py [--ebb-root ../Claude/cb.ebb.claude.data] [--out data]

Outputs (in --out):
  plate_series.csv  series,t,value,unc,units,source_id (t = decimal year)
  manifest.json     provenance: EBB source/indicator, raw file + SHA-256,
                    record count, coverage, sea-level splice offset

Standard library only. Fails loudly on implausible values or empty series.
"""
import argparse, csv, hashlib, json, sys
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean

from ebb_plate_fetch import parse_co2, parse_recon  # same-folder parsers for the two local files

LOCAL_PARSERS = {"co2_mlo": parse_co2, "gmsl_recon": parse_recon}

# series -> EBB source. unc_col: column in the raw file holding the
# uncertainty (whitespace- or comma-delimited, first column = time).
SERIES = {
    "co2": dict(
        local="co2_mlo.csv", source_id="co2_mlo",
        units="ppm", range=(250, 600),
        title="NOAA GML Mauna Loa monthly mean CO2 (co2_mm_mlo.csv)"),
    "temp_anomaly": dict(
        source_id="noaa_temp_global_monthly", indicator="temp.anomaly.global.land_ocean.monthly",
        units="degC vs 1901-2000", monthly=True, unc_col=None, scale=1.0, range=(-3, 3),
        title="NOAA NCEI Climate at a Glance global land+ocean temperature anomaly"),
    "sea_level_recon": dict(
        local="gmsl_recon.csv", source_id="gmsl_recon",
        units="mm", range=(-400, 400),
        title="CSIRO reconstructed GMSL (Church & White 2011)"),
    "sea_level_sat": dict(
        source_id="aviso_gmsl_merged_nrt", indicator="sea_level.global.weekly",
        units="mm (aligned to recon)", monthly=False, unc_col=2, scale=1000.0, range=(-400, 400),
        title="AVISO merged multi-mission satellite altimetry GMSL (GIA-adjusted, NRT)"),
    "ocean_heat_0_700m": dict(
        source_id="noaa_ohc_700m_*_quarterly", indicator="ohc.0_700m.global.quarterly",
        units="1e22 J", monthly=False, unc_col=2, scale=1.0, range=(-100, 100),
        title="NOAA NCEI WOA22 World Ocean heat content 0-700 m, 3-month means"),
}


def decimal_year(iso, monthly):
    d = date.fromisoformat(iso[:10])
    if monthly:  # archive dates monthly records by period end; plot mid-month
        return d.year + (d.month - 0.5) / 12
    start, end = date(d.year, 1, 1), date(d.year + 1, 1, 1)
    return d.year + (d - start).days / (end - start).days


def latest_raw(ebb, source_id):
    files = sorted((p for p in (ebb / "data" / "raw").glob(f"{source_id}/**/*")
                    if p.is_file() and not p.name.startswith(".")),
                   key=lambda p: (p.parent.name, p.name))
    return files[-1] if files else None


MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()


def _time(s):
    if len(s) == 8 and s[4] == "-" and s[5:].lower() in MONTHS:  # CSIRO '1880-Jan'
        return int(s[:4]) + (MONTHS.index(s[5:].lower()) + 0.5) / 12
    return float(s)


def raw_uncertainties(path, unc_col, scale):
    """[(decimal_year, unc)] from a raw text file whose first column is time."""
    out = []
    for line in path.read_text(errors="replace").splitlines():
        p = line.replace(",", " ").split()
        try:
            t, u = _time(p[0]), float(p[unc_col])
        except (IndexError, ValueError):
            continue
        if len(p) > 2 and p[1].isdigit() and 1 <= int(p[1]) <= 12 and t.is_integer():
            t += (int(p[1]) - 0.5) / 12  # NOAA monthly CSV: year,month,... columns
        if u >= 0:  # NOAA uses -0.99 / -9.99 for "not available"
            out.append((t, u * scale))
    return out


def nearest(uncs, t, tol=0.02):
    if not uncs:
        return None
    best = min(uncs, key=lambda x: abs(x[0] - t))
    return best[1] if abs(best[0] - t) <= tol else None


def check(name, cfg, recs):
    if len(recs) < 10:
        sys.exit(f"[{name}] only {len(recs)} records - source missing or format changed")
    lo, hi = cfg["range"]
    bad = [r for r in recs if not lo <= r[1] <= hi]
    if bad:
        sys.exit(f"[{name}] {len(bad)} values outside {lo}..{hi}, e.g. {bad[0]} - check units")
    return recs


def annual(recs):
    by = {}
    for t, v, _ in recs:
        by.setdefault(int(t), []).append(v)
    return {y: mean(v) for y, v in by.items()}


def splice_offset(recon, sat):
    """Offset aligning satellite GMSL to the reconstruction over common full years."""
    a, b = annual(recon), annual(sat)
    common = sorted(set(a) & set(b))[1:-1]  # drop partial first/last years
    if len(common) < 3:
        return None, common
    return mean(a[y] - b[y] for y in common), common


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    here = Path(__file__).resolve().parent
    ap.add_argument("--ebb-root", default=str(here.parent / "Claude" / "cb.ebb.claude.data"))
    ap.add_argument("--out", default=str(here / "data"))
    args = ap.parse_args()

    ebb, out = Path(args.ebb_root).resolve(), Path(args.out)
    sys.path.insert(0, str(ebb))
    import archive_reader  # EBB's own reader: one best record per (indicator, date)

    snaps, _, _ = archive_reader.read_archive(str(ebb / "data"))
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "ebb_root": str(ebb), "series": {}}
    data = {}

    for name, cfg in SERIES.items():
        if "local" in cfg:  # not an EBB source: parse the file ebb_plate_fetch.py downloaded
            path = out / "raw" / cfg["local"]
            if not path.exists():
                sys.exit(f"[{name}] missing {path} - run ebb_plate_fetch.py once to download it")
            recs = sorted(LOCAL_PARSERS[cfg["source_id"]](path.read_text(errors="replace")))
            data[name] = check(name, cfg, recs)
            manifest["series"][name] = {
                "title": cfg["title"], "units": cfg["units"], "origin": "local file (not an EBB source)",
                "file": str(path.relative_to(out)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "records": len(recs), "with_uncertainty": sum(r[2] is not None for r in recs),
                "first_t": round(recs[0][0], 3), "last_t": round(recs[-1][0], 3)}
            print(f" {name}: {len(recs)} records, {recs[0][0]:.2f}-{recs[-1][0]:.2f} (local file)")
            continue

        sid_glob = cfg["source_id"]
        match = (lambda s: s.startswith("noaa_ohc_700m_") and s.endswith("_quarterly")) \
            if "*" in sid_glob else (lambda s, g=sid_glob: s == g)
        pts = [p for p in snaps.get(cfg["indicator"], []) if match(p["source_id"])]
        if len(pts) < 10:
            sys.exit(f"[{name}] only {len(pts)} archive records for {cfg['indicator']} - ingest the source first")

        sids = sorted({p["source_id"] for p in pts})
        raws = {s: latest_raw(ebb, s) for s in sids}
        # Uncertainties are read from the newest stored raw file. EBB stores a
        # full raw copy at most monthly for delta-stored sources, so the last
        # few archive points can be newer than that file (raw_covers_to_t).
        uncs = []
        if cfg["unc_col"] is not None:
            for s, r in raws.items():
                if r:
                    uncs += raw_uncertainties(r, cfg["unc_col"], cfg["scale"])

        recs = sorted((decimal_year(p["date"], cfg["monthly"]), float(p["value"]), None) for p in pts)
        recs = [(t, v, nearest(uncs, t)) for t, v, _ in recs]
        data[name] = check(name, cfg, recs)
        manifest["series"][name] = {
            "title": cfg["title"], "units": cfg["units"], "ebb_indicator": cfg["indicator"],
            "ebb_source_ids": sids, "records": len(recs),
            "with_uncertainty": sum(r[2] is not None for r in recs),
            "first_t": round(recs[0][0], 3), "last_t": round(recs[-1][0], 3),
            "raw_covers_to_t": round(max(t for t, _ in uncs), 3) if uncs else None,
            "raw_files": {s: {"path": str(r.relative_to(ebb)) if r else None,
                              "sha256": hashlib.sha256(r.read_bytes()).hexdigest() if r else None}
                          for s, r in raws.items()},
        }
        n_unc = manifest["series"][name]["with_uncertainty"]
        print(f" {name}: {len(recs)} records, {recs[0][0]:.2f}-{recs[-1][0]:.2f}"
              + (f", {n_unc} with uncertainty" if cfg["unc_col"] is not None else ""))

    off, years = splice_offset(data["sea_level_recon"], data["sea_level_sat"])
    if off is None:
        print("WARNING: no recon/satellite overlap - satellite sea level left unaligned")
    else:
        data["sea_level_sat"] = [(t, v + off, u) for t, v, u in data["sea_level_sat"]]
        print(f" sea-level splice: satellite shifted {off:+.2f} mm ({years[0]}-{years[-1]} overlap)")
    manifest["sea_level_splice"] = {"offset_mm_added_to_sat": None if off is None else round(off, 3),
                                    "overlap_years": years}

    with open(out / "plate_series.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["series", "t", "value", "unc", "units", "source_id"])
        for name, recs in data.items():
            cfg = SERIES[name]
            sid = cfg["source_id"].replace("_*", "")  # OHC: 4 per-quarter sources, one series
            for t, v, u in recs:
                w.writerow([name, f"{t:.4f}", f"{v:.4f}", "" if u is None else f"{u:.4f}", cfg["units"], sid])
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {out / 'plate_series.csv'} and {out / 'manifest.json'}")


if __name__ == "__main__":
    main()
