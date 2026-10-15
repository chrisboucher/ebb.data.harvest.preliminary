# EBB Data Plate — Handoff Summary

Project: **Earth's Black Box (EBB)**, a century-scale climate data archive.
This document hands the laser-engraved data plate work over to Codex. Status: **test-coupon stage**.
Companion files: `ebb_plate_build.py` builds the plate series (run and verified 2026-10-15). `ebb_plate_fetch.py` downloaded the two series EBB doesn't carry, and also holds their parsers.

Each item below is marked either **DECIDED** (confirmed by the owner) or **PROPOSED** (a working design that has not been validated yet).

---

## 1. Goal

Store climate data on a 300 × 300 mm stainless steel plate in two layers:

1. **Human-readable layer**: four line charts that anyone can read by eye, with no decoder.
2. **Machine-readable layer**: dense binary dot tiles holding the full numeric records, protected by error correction.

The charts double as a sanity check on the binary data. The plate must be readable in about 100 years with no surviving software, so it carries a plain-language **primer** explaining the encoding.

## 2. Hardware and constraints (DECIDED)

- **Plate:** 300 × 300 mm stainless steel. Use 316L if possible.
- **Laser:** Bambu Lab 10 W diode module, with a dot size of about 0.11 mm.
- **Laser work area:** 310 (X) × 270 (Y) mm, so the full plate does not fit in Y.
  - **Active area: 280 × 260 mm.** The unreachable strip becomes the border.
- **Marking process:** the 10 W diode marks stainless by growing an oxide layer (annealing). It does **not** ablate the surface or engrave deeply.
  - The black oxide marks hold up well in dry indoor conditions.
  - The marked surface is chromium-depleted, so it can corrode in humid or salty air. Store the plate dry and sealed.
  - Passivation is untested and may lighten the marks. Test it on an offcut before using it on a real plate.

## 3. Test coupon results (power/speed matrix, 0.100 mm interval, 1 pass)

- The deepest, most even black is at **1,200 mm/min with 60–80% power**. This is the candidate setting.
- At 100% power and 1,200 mm/min the fill looks slightly streaky, which suggests too much heat.
- **2,400 mm/min at 80–100%** is nearly as dark and would halve engraving time. It still needs a closer look under a loupe.
- Do not use the blue, straw, or brown cells. These are thin interference oxide films whose colour changes with viewing angle.
- **Not yet known:** the smallest reliable dot pitch. The coupon used filled squares, which do not answer that question.

## 4. Plate layout (PROPOSED)

All coordinates are in mm, measured within the 280 × 260 active area.

| Region | Size | Content |
|---|---|---|
| Title + primer band | 280 × 30 | Title, how to decode the tiles, data provenance |
| Chart: CO₂ | ~137 × 56 | ppm, 1958– |
| Chart: Temp anomaly | ~137 × 56 | °C vs 1901–2000, 1850– in the data (plot annual means; the chart may start at 1880) |
| Chart: Sea level | ~137 × 56 | mm, 1880–. Reconstruction drawn **dotted**, satellite **solid** |
| Chart: Ocean heat | ~137 × 56 | 10²² J, 0–700 m, 1955–. Drawn as steps, with a standard-error band |
| Binary tile band | 280 × 100 | 28 × 10 = 280 tiles, about 95 KB net |

The charts sit in a 2 × 2 grid with about 6 mm gutters. Each chart needs axis labels, units, and the name of its source.

At 0.15 mm pitch, a chart panel is about 900 columns × 370 rows, which works out to roughly 8.5 bits per value. That is enough for one column per month for CO₂.

The total data is about 5,000 values, roughly 10 KB, so the tile band has plenty of room. **Store every dataset three times, in physically separated parts of the band.**

**Estimated engraving time:** about 4 hours at 1,200 mm/min. The tile band raster takes most of that, about 2.5–3 hours. The charts are vector paths and take only minutes.

## 5. Binary tile format v1 (PROPOSED)

- **Tile:** 64 × 64 modules. The module pitch is 0.15 mm for now; the dot-pitch test will set the final value.
- **Quiet zone:** 1 blank module around each tile, giving a tile pitch of 66 modules (9.9 mm).
- **Finder L:** the left column and bottom row are always dots. This gives orientation and detects mirroring.
- **Clock tracks:** the top row and right column alternate dot and gap, starting with a dot at column 0 and row 0.
- **Header:** rows 1 and 2, columns 1–62. The same 62-bit header is written in both rows:
  - plate ID: 12 bits
  - tile row: 8 bits
  - tile column: 8 bits
  - format version: 8 bits
  - reserved: 10 bits
  - CRC-16: 16 bits. Proposed variant is CRC-16/CCITT-FALSE, computed over the preceding 46 bits.
- **Payload:** rows 3–62 × columns 1–62 = 3,720 bits = **465 bytes**.
  - Bits are read row-major, MSB first.
  - An engraved dot means 1.
- **Mask:** each payload bit is XORed with `(row + col) mod 2`, using tile coordinates. This prevents large solid or empty regions.
- **Error correction:** Reed–Solomon RS(255,191) over GF(256), striped across tiles.
  - Codeword *k* takes byte *k* from each tile in a stripe of 255 tiles.
  - This tolerates up to 64 lost tiles per stripe. Tiles whose header CRC fails are treated as erasures.
  - Net payload is about 348 bytes per tile.
  - ⚠️ **Open issue:** 280 tiles is not a multiple of 255. Options are shortened RS codes, for example two stripes of 140 tiles with RS(140,108), or padding. Codex should pick one and document it.

### Payload data format (PROPOSED, not final)

Keep the format simple enough to describe in the primer. **No compression libraries**: use fixed-width scaled integers, with optional delta encoding.

Each series gets a header giving: series ID, start time, step, count, scale, integer type, and units string.

| Series | Proposed type × scale | Range covered |
|---|---|---|
| CO₂ | uint16 × 100 | 0–655.35 ppm |
| Temp anomaly | int16 × 1000 | ±32.767 °C |
| Sea level | int16 × 10 | ±3,276.7 mm |
| Ocean heat (and its standard error) | int16 × 100 | ±327.67 × 10²² J |

The satellite sea-level series is irregular, about every 10 days. Either store explicit timestamps (for example int32 days since an epoch) or resample it to monthly before encoding. **This is a decision to make.**

Also store SHA-256 hashes of each raw source file, taken from the manifest, in the binary layer.

## 6. Data sources (DECIDED 2026-10-15: EBB archive where it has the data)

Three series come from the EBB archive (`cb.ebb.claude.data`). Monthly Mauna Loa CO₂ (EBB's daily record only starts in 1974) and the CSIRO reconstruction (EBB has no pre-1993 sea level) are not EBB sources. For this test they come from files downloaded into `data/raw/`, and their SHA-256 hashes are recorded in the manifest.

| Plate series | EBB source_id | EBB indicator | Coverage | Cadence | Uncertainty |
|---|---|---|---|---|---|
| `co2` | local `data/raw/co2_mlo.csv` (NOAA GML `co2_mm_mlo.csv`) | (not in EBB) | 1958-03– | Monthly | from 1974 (NOAA publishes none earlier) |
| `temp_anomaly` | `noaa_temp_global_monthly` (NOAA NCEI Climate at a Glance, land+ocean) | `temp.anomaly.global.land_ocean.monthly` | 1850– | Monthly | none published |
| `sea_level_recon` | local `data/raw/gmsl_recon.csv` (CSIRO Church & White 2011, datahub copy) | (not in EBB) | 1880–2013 | Monthly | yes |
| `sea_level_sat` | `aviso_gmsl_merged_nrt` (AVISO merged altimetry, GIA-adjusted) | `sea_level.global.weekly` | 1993– | ~10-day | yes |
| `ocean_heat_0_700m` | `noaa_ohc_700m_{1_3,4_6,7_9,10_12}_quarterly` (NOAA NCEI WOA22) | `ohc.0_700m.global.quarterly` | 1955– | 3-month | yes (World Ocean s.e.) |

Changes from the original plan: GISTEMP (1951–1980 baseline) became NOAA's series (1901–2000 baseline), so the primer must state the new baseline. NOAA STAR satellite sea level is dead (404, frozen 2025-02), so it became AVISO. Ocean heat moved from 0–2000 m (which only starts in 2005) to 0–700 m (1955–), to match the chart's planned start.

The two sea-level sources are spliced as before:

- The satellite series is offset to match the reconstruction, using annual means over the full years where both have data. The current offset is +2.04 mm over 1994–2012.
- The two series are kept separate in the data, so the chart can draw them in different line styles.
- The offset value is recorded in the manifest.

## 7. Existing code: `ebb_plate_build.py`

- Standard library only. It imports EBB's own `archive_reader.py` from `--ebb-root` (default `../Claude/cb.ebb.claude.data`).
- `python ebb_plate_build.py` reads the archive plus the two local files. Nothing is downloaded: refresh the archive series by letting EBB ingest, and the local files by re-running `ebb_plate_fetch.py`.
- **Outputs (in `data/`):**
  - `plate_series.csv`: columns are series, t (decimal year, mid-month for monthly data), value, unc, units, source_id.
  - `manifest.json`: per series, the record count, coverage and SHA-256 of the file used. Archive series also get their EBB indicator and source IDs, plus the newest raw file. It also records the splice offset.
- `raw_covers_to_t` in the manifest says how far that raw file reaches. EBB saves a full raw copy at most monthly for some sources, so the last few archive points (and their uncertainties) can be newer than the hashed file. Take this into account when writing hashes onto the plate.
- Range checks make the script stop with an error on implausible values or an empty series.
- `data/raw/` holds the 2026-10-15 download run. The build script uses `co2_mlo.csv` and `gmsl_recon.csv`. The other three files are unused.

## 8. Next tasks for Codex (suggested order)

1. ~~Live-run the fetcher.~~ Done 2026-10-15. The data now comes from the EBB archive through `ebb_plate_build.py`.
2. **Dot-pitch test coupon generator.** Output an SVG for Bambu Studio containing:
   - dot grids at 0.12, 0.15, 0.18, 0.20, 0.22 and 0.25 mm pitch
   - samples of 1 mm text
   - one full 64×64 tile
3. **Tile encoder.** `plate_series.csv` → binary payload → RS-striped tiles → SVG at the chosen pitch.
4. **Decoder.** Scan image (2400–4800 dpi) → finder/clock detection → bits → RS decode → CSV. Include a damage simulator (scratches, missing tiles) to verify the round trip.
5. **Chart renderer.** The four panels as vector SVG paths at the layout dimensions, with axes, labels, units and sources.
6. **Primer.** A human-readable decoding guide: a tile drawn at large scale with labelled bits, the mask rule, the RS description, the data format, and a provenance table.
7. **Full plate assembly.** A single SVG with the 280 × 260 active area and registration marks.

## 9. Open decisions

- Final dot pitch (pending the coupon test).
- Laser setting: 1,200 mm/min vs 2,400 mm/min.
- RS stripe sizing for 280 tiles.
- How to store the irregular satellite sea-level series.
- Plate ID scheme. Possibly tie it to EBB's existing hash-chain design.
- Whether to passivate after marking (pending the offcut test).
