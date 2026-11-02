# Potash slurry elbows cathodic-protection data

## Source and rights

- Dataset: Michael Trask, *Protection Current Density for AISI 1018 Carbon Steel Elbows in Saturated Potash Brines at Various Flow Velocities and Slurry Concentrations of 1.05 mm Diameter Sand using a Vertical Flow Loop Apparatus*, Mendeley Data Version 3, DOI [10.17632/gkwrx7tz2k.3](https://doi.org/10.17632/gkwrx7tz2k.3).
- Publisher record: https://data.mendeley.com/datasets/gkwrx7tz2k/3
- License shown by Mendeley: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Attribution and a note of any modifications are required when redistributing adapted content.
- Contributor: Michael Trask; University of Saskatchewan.
- Local source file: `data/raw/potash-elbow-cp/Current Density Required to Protect AISI 1018 CS in Saturated Potash Brine in Various Slurry Concentrations.docx`. The raw file is excluded from source payloads. A separate stagnant-pipe DOCX is now stored under `data/raw/potash-cp/`; it is a different DOI and experiment and is not used by this reader.
- File size: 34,410 bytes. SHA-256: `9b6ba2bd756d402596783353c9dfb7ecad951fb6f3780e67e239acc7f9aa5103`. This matches the checksum published in Mendeley Data's public file metadata.

## Structure and process context

The source is one DOCX file containing five tables with 28, 14, 39, 39, and 39 rows. The file has 12 package parts and no embedded media. This structural inventory is recorded in `research/potash_elbow_cp_structure.json`; no observation values are copied there.

The experiment measures protective-current demand for AISI 1018 steel elbows in saturated potash brine with 1.05 mm sand in a vertical flow loop. The published conditions include four declared fluid velocities and stepped sand loading from 0 to 35 wt.%. It complements—but is a distinct DOI/version from—the repository's previously inventoried 95-minute stagnant-brine pipe experiment (`potash_cp`).

## Intended application and limits

Use this as condition-specific laboratory evidence for comparing how flow and abrasive loading relate to protective-current demand, and as a source candidate when wiring existing cathodic-protection or erosion/corrosion APIs. Preserve the experiment and campaign as the split unit. The table rows and velocity conditions are not independent field trials.

Protective-current demand is not a direct corrosion-loss measurement, a remaining-wall estimate, proof of protection potential, or a generic anode-sizing rule. The material, electrolyte, elbow geometry, flow loop and sand size restrict transfer. Temperature, electrode/reference details, exposed area, source measurement uncertainty and the precise protection criterion must be reviewed from the record before any quantitative model use. This acquisition records provenance and structure only; it does not claim model calibration or field qualification.


## Python reader

`pipeline_integrity.potash_elbow_cp.read_potash_elbow_cp(path)` reads the OOXML document with
Python standard-library ZIP/XML/hash modules. The default SHA-256 pin is the published Mendeley
Version 3 file. The two fragments captioned as table A.1 are combined as velocity group A.1
(2.5 m/s); original table and XML row numbers remain attached to every result. Other captioned
velocities remain separate groups.

The result retains each replicate reading and publisher-reported mean and 95% confidence interval
in `mA/cm2` as distinct fields. It adds SI fields in `A/m2` using the explicit factor 10; this is
marked as a modification in the result attribution metadata. Reported summaries are not recomputed.
Rows and velocity conditions are explicitly marked as not independent field trials. The caller
chooses the local source path; for this workspace the acquired elbow DOCX is under `data/raw/potash-elbow-cp` as
listed above. Do not add that raw file or its pointwise output to the code release.

## First source application

The pinned reader has been applied to the acquired source. The local application receipt at
`artifacts/derived/potash_elbow_cp_application_receipt.json` records the source hash, license,
analysis definition, and aggregate ranges without pointwise source observations. It summarizes
144 conditions (36 per velocity) and 684 replicate readings: 324 at 2.5 m/s, 144 at 3.0 m/s,
108 at 3.5 m/s, and 108 at 4.0 m/s. Publisher-reported means span 9.26–14.36 A/m2 across
conditions; the reported 95% interval field spans 0.22–0.76 A/m2. These are descriptive ranges
across different sand-loading conditions, not prediction intervals or independent campaign
samples. No fitted relation, uncertainty propagation, or inferential analysis was performed.

The aggregate supports a future laboratory comparison input to an existing cathodic-protection
demand API after the missing electrode, exposed-area, temperature, and protection-criterion
metadata are resolved. The values do not calibrate corrosion loss, establish protection
potential, or qualify a field design. The derived receipt is under the ignored `artifacts/`
directory and must not be added to a code release.
