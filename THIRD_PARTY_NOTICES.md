# External software and evidence

Original project code and explanations are independently authored and MIT licensed.
External software is used through dependencies or explicit application adapters;
its code is not copied into this source tree. Each dependency retains its license.

## Software dependencies

| Package | License family | Use |
|---|---|---|
| NumPy, SciPy, pandas | BSD | Arrays, engineering library dependencies, source-table reading |
| Pydantic, Pint | MIT / BSD respectively | Validated contracts and explicit unit conversion |
| Fluids | MIT | Friction, compressible flow, control valves and gas relief area |
| ht | MIT | Internal convection correlations and separately called steady cylinder/isothermal-plane conduction APIs |
| CoolProp | MIT | Fluid state and transport properties |
| WNTR | Revised BSD | Optional water-network execution; includes its EPANET integration |
| openpyxl, rdata | MIT | Workbook inspection and optional source-format support |
| Matplotlib | PSF-based Matplotlib license | Optional figures generated from original synthetic cases |
| OR-Tools | Apache-2.0 | Separately installed CP-SAT solver for declared finite operation plans |
| rainflow | MIT, Piotr Janiszewski | Separately installed material engine's cycle-counting dependency; no vendored implementation |
| FiPy | NIST Terms of Use and separately retained component notices | Optional orthogonal 2D soil finite-volume library; see the detailed notice below |
| MoorPy | BSD-3-Clause | Optional separately installed elastic catenary solver; no solver code is vendored |
| scikit-fem | BSD-3-Clause | Optional separately installed finite-element library |
| PyNiteFEA | MIT | Optional separately installed frame solver; detailed attribution below |
| pypdf | BSD-3-Clause | Optional source-document reader; source documents are not shipped |
| pytest, build, Ruff | MIT / MIT / MIT | Development checks and packaging |
| PyYAML 6.0.3 (optional test integration) | MIT | YAML runtime used by the MoorPy route-pull validation path; its official wheel is retained under `artifacts/hpc/wave16-wheels/` with the wheel's embedded license. |

Exact tested versions and installed metadata are in research/tested_environment.json.
The installed packages carry their complete notices and transitive dependencies.
This source release does not vendor their source code or binary distributions.
When distributing a combined binary environment, retain the complete applicable
notices, including bundled components. A library license does not settle the
engineering applicability of a correlation or grant certification to a device.

Optional application projects are separately installed, not relicensed by this
package. Integration examples retain their owner schema and source revision/hash.
The consequence exchange additionally executes separately checked-out SLABx
(Ugwiyeon Lee, MIT) and SLABx-LH2 (Korea Gas Safety Corporation, MIT), through
PRISM's pinned backend. Source commits, package fingerprints and required runtime
versions are retained. Their full notices remain with the separately acquired
sources; no source, coefficients, data tables or native prose are vendored here.

Data sources retain their own attribution and use conditions. Raw and locally
transformed external observations are excluded from source archives and wheels.
Source metadata, acquisition recipes, hashes and aggregate checks support reproducibility.
Public availability alone is not recorded as permission to redistribute.

## Data attribution and review

Primary C1/C2 crack-arrest machine observations are credited to Puyi Gao,
Mendeley Data V1 (2023), DOI 10.17632/g69875w65c.1, under the reviewed CC-BY-4.0
publisher record. The 943,565-byte ZIP matches a prior-acquisition SHA-256, not a
publisher-issued checksum. The separately installed MatIntegrity fracture reader,
Mode-I/Paris models and user-boundary evaluator retain their MIT credit. No source
engine, standard curve or coefficient table is copied. Raw and pointwise transformed
machine observations stay outside distributions. The plotted fracture cases and
polyline are original illustrative inputs. Signed channels, original cycle indices,
header representation and filename-derived temperatures retain their stated limits.
The separate transition-toughness compilation, DOI 10.17632/2vkgrbsy8j.1, was reviewed
as metadata only: its publisher-level CC-BY-4.0 record is retained, while row-level
lineage, applicability and redistribution conditions remain unreviewed here. It is
not used to assign service resistance or growth coefficients.

Thermal-rod observations are credited to Lauren B. Tomanek and Daniel S. Stutts,
Mendeley Data V1 (2021), DOI 10.17632/sbcf36976g.1, CC-BY-4.0 as reviewed on the
publisher page. The 2,099,328-byte archive is matched to a prior-acquisition SHA-256,
not represented as a publisher-provided checksum. The separately installed
matintegrity reader and mechanics/thermal models remain credited to MatIntegrity
contributors under their MIT record. Raw CSV rows, source documentation, fitted
workbooks and locally transformed or conditionally simulated experimental records
are excluded from the release. Only aggregate source checks, original synthetic
reference inputs/results and figures are published here. Excess temperatures and
controller counts retain their original meaning; no field qualification is claimed.

The control exchange calls the separately acquired lh2dt_models PIDController,
FirstOrderTransmitter, HomogeneousTank and stroke-limited valve under that owner's
MIT record, credited to LH2DT contributors. Native source identities and source-tree
hashes are recorded in research/control_exchange_results.json. These implementations
are not copied into this distribution. Control-library documentation was reviewed
for alternative integration and tracking features; no example source or prose was
vendored. Coupled-tank shadow commands retain the data attribution below and are
not claimed to be observed actions, calibration, fault probabilities or plant validation.

Coupled-tank experimental data are credited to Guilherme Silva, Marcela Coelho
and Valter Leite (CEFET-MG, Brazil), version v1.0.0, DOI 10.5281/zenodo.19856119,
CC-BY-4.0 according to the publisher's API metadata. Six selected files were
checked against publisher MD5 values. Acquisition recipes, citation and aggregate
checks are retained. Original CSV rows, parameter file, source README prose and
locally transformed observations are excluded from distributions. Source unit,
datum and geometry gaps remain unresolved; example inventory assumptions are
separate research conditions, not a correction to the original experimental record.

TUBERS source data are credited to University of Essex and TWI ELLAS ASTIKI MI
KERDOSKOPIKI ETAIREIA, version v1, DOI 10.5281/zenodo.11239514. The publisher API
identifies the MIT license and provides both archive MD5 checksums. The source
metadata, acquisition and aggregate review are retained; neither archive nor
transformed waveforms, source imagery, masks or source README prose are shipped.
Signed times, missing voltages and unmatched masks remain unresolved where relevant.

The data catalog records creators/citation, version, source URL, rights evidence,
license, checked date, limitations and file checksums. UCI, BattLeDIM and selected
Mendeley/Zenodo candidates are attributed according to their CC BY 4.0 records.
The Harvard TEP dedication has its own conditions and is not labeled CC0 here.
UCI hydraulic-system condition-monitoring data are credited to Nikolai Helwig,
Eliseo Pignanelli and Andreas Schütze, DOI 10.24432/C5CW21, CC-BY-4.0. Published
valve-condition counts are aggregate checks; original sensor matrices, profiles
and explanatory files remain outside the release. Condition grades are not
opening trajectories, actuator response times or human error probabilities.
The selected ELVHYS numerical/text assets carry the repository's CC0-1.0 record.
Repeated ILI source observations are attributed to Rioshar Yarveisy, Faisal Khan
and Rouzbeh Abbassi, version 1, DOI 10.17632/c2h2jf5c54.1, CC-BY-4.0. Source
interpretations and aggregate matching checks retain that attribution; local
normalised rows and observation workbooks are excluded from the code release.
H2SAFE data and supporting metadata carry the reviewed CC-BY-4.0 record. Their
observations and figures remain outside the source release; citations are retained.
Sheffield CID acoustic data are attributed to Mohammad Reza Shekofteh (2026),
The University of Sheffield, DOI 10.15131/shef.data.32229270.v1, CC-BY-4.0.
Only aggregate diagnostics are published here. Original waveforms, supporting
documents and locally derived sample/window rows remain outside the release.
The HRA exchange uses one NRC SACADA simulator observed-count record through the
separately installed human-reliability-toolkit. Public output contains derived
research checks, source identifiers and hashes, not a copied catalog. NRC credit,
the pinned owner's government-public-use metadata and third-party exceptions are
retained. This run did not reverify the original server's rights terms. Detailed
native estimates and catalog tables are outside this distribution boundary.
Canada's OGL and the PHMSA access record are tracked separately; access failure is
not successful acquisition and public access is not blanket redistribution clearance.

GasLib-11-v1-20211130 test-network assets carry CC-BY-3.0 in their original XML
headers. Attribution is retained to Schmidt and coauthors (2017), DOI
10.3390/data2040040, and the source-required Pfetsch et al., ZIB Report 12-41
(2012). Full creators and institutional/source rights evidence are recorded in
the dataset catalog. Raw XML, compressor configurations and SVG are excluded
from this release. The isolated geometry exercise declares different fluid,
thermal and pressure assumptions and does not reproduce the original network.
The scheduling exercise reuses pipe identities and shared-node topology with
explicitly hypothetical durations, crew calendars and exclusion rules. It does
not infer observed maintenance times or validate field work authorization.

No restricted standards text, manufacturer selection tables or unresolved numeric
tables are embedded. Caller-owned requirement/curve providers can supply reviewed
values without publishing their underlying restricted materials. Original prose is
not a replacement for checking applicable rights. The automated release inspection
checks the stated boundary; it is not an unconditional legal clearance.

The optional linear frame solver uses the separately installed PyNiteFEA 3.2.0
distribution (MIT), copyright D. Craig Brinck. Its license remains in the upstream
distribution: https://github.com/JWock82/Pynite/blob/main/LICENSE. No solver source,
documentation examples, vendor sections or standard load combinations are bundled.
Frame input contracts, independent static-balance checks, elastic reference cases
and plotted curves in this project were authored separately. The observed-load
exchange retains Gao's CC-BY-4.0 source attribution above; source observations and
detailed mapped snapshots remain outside the code distribution. The hypothetical
frame is not the experimental fixture and its output is not field validation.


Uniform frame thermal input reuses the separately installed MatIntegrity free-strain
calculation. Pressure input retains its cylinder-result receipts and source credits.
Equivalent initial-strain forces, physical-force recovery and the independent
references in this repository were authored separately; no solver or third-party
example implementation was copied. The reference plots use authored conditions.
The conditional temperature exchange credits Lauren B. Tomanek and Daniel S. Stutts
(2021), Mendeley Data V1, DOI 10.17632/sbcf36976g.1, CC-BY-4.0. Source excess
temperatures remain distinct from the declared ambient/stress-free hypothesis.
Raw or transformed observations and detailed mapped snapshots are excluded from
source distributions. This exercise does not validate the original rod's stress.


The lifecycle extension reuses the project's cashflow/energy primitives. Its
contracts, valuation bindings, fictional REF-currency inputs and figures were
authored separately. NIST BLCC public resources were reviewed as method/tool
context; no tool source, example, prescribed discount rate, tariff, emission table
or standards text is bundled. EPS1 application credits Helwig, Pignanelli and
Schuetze, UCI DOI 10.24432/C5CW21, CC-BY-4.0. Only source identifiers and aggregate
checks are public; raw/transformed samples remain excluded. Tariffs, duty, emission
credits and conditional loss are declared hypotheses. Native HRA/monitoring and
OR-Tools schedule calculations retain their own source/version/rights records.

The uncertainty service calls the already declared NumPy and optional SciPy
installations. It does not vendor their sampling or distribution implementations.
Paired hydraulic power/flow examples retain the existing UCI447 creator, DOI and
CC BY 4.0 attribution. Source archives and derived per-cycle rows are excluded
from code distributions. Assumed probability laws and scenario prices are original
illustrative inputs and are not extracted market data or field estimates.

Distribution fitting reuses the declared SciPy installation for normal/lognormal
maximum-likelihood estimates, ranks, normal transforms and pairwise distances.
The energy-score mathematical relationship was independently implemented; no
article wording, tables or code were imported. The method citation and runtime
versions are in UNCERTAINTY_FIT_KO and the research method record. UCI447 original
archives and per-cycle derived observations remain outside code releases; public
figures use aggregate diagnostics and retain the dataset attribution.

Electrochemical exchange calls the separately installed matintegrity engine; its
corrosion and cathodic-protection implementations are not vendored. Original
reference inputs and explanations are distinct from supplier design criteria.
Galvanized-steel data retain Vontorova/Novak/Vanova attribution, version DOI
10.5281/zenodo.13886478, and CC BY 4.0 terms confirmed in DOI registration metadata.
The five source assets and all transformed observation rows are excluded from code
releases. The Trask potash-brine record is metadata only; no protective-current
observations were obtained or used for field sizing.

Creep and ageing exchange calls the separately installed matintegrity constitutive
models, censored-life inference and primary-data readers. No owner implementation
is vendored. Mathematical reference inputs and explanations were independently
written; no standards text, material design tables or source example data is bundled.
NIST handbook method links are retained in CREEP_KO and the method record.

CMSX-6 primary data credit Luis A. Avila Calderon, Sina Schriever, Ying Han,
Juergen Olbricht, Pedro Dolabella Portella and Birgit Skrotzki, version DOI
10.5281/zenodo.14750432, CC BY 4.0 confirmed in DataCite metadata. The registered
release is 1.1; the supplied workbook filename still contains v1.0. Thirteen pinned
non-document assets were checked against publisher MD5 values. Original and
transformed specimen/trace rows are excluded from code releases. Source identifiers,
checksums, aggregate checks and explicitly research-level fitted parameters are
provided with attribution. Single-crystal Ni-superalloy tests do not establish
steel-pipeline field applicability or parameter uncertainty.

Common-period risk exchange reuses the separately installed human-reliability-toolkit
Boolean engine and existing probability, tree and joint-state contracts. It does not
vendor solver code. All reference probabilities and dependence scenarios are
original authored assumptions. The CMSX-6 application reuses the attributed primary
source and existing owner inference; no new observation rows or source text are
included in code distributions. Declared joint states and hypothetical component
mappings are not observed system dependence or field validation.

Pressure-test exchange calls the separately installed matintegrity engine and the
existing checked CoolProp service. The molar-density bridge states the native gas
constant convention; no owner constitutive implementation or standards table is
vendored. Test factors, admissible values and reference inputs are original declared
hypotheses and do not represent an adopted design/test standard.

The PE100RC pressure/temperature source credits Milos Mician and Radoslav Konar,
version DOI10.5281/zenodo.20582921, publisher page v1 dated 2026-06-07, CC BY4.0
confirmed in DataCite registration. The 78721-byte workbook matches publisher MD5.
Original and transformed rows are excluded from code distributions. The checked
file provides ten capillary profiles; no missing reference test or ambient channel
was reconstructed from its description. Conditional volume/atmosphere/temperature,
rigidity and predefined Air hypotheses remain separate from observations.

Welding exchange reuses separately installed matintegrity models, existing signed
power integration and unit services. It does not copy owner constitutive laws,
standards tables, procedure coefficients or supplier notebooks. All synthetic
reference assays, profiles and rule limits were independently authored.

Tandem-GMAW data credit Prashant Kumar Chaurasia, Cagtay Fabry, Andreas Pittner and
Michael Rethmeier, DOI10.5281/zenodo.21824586 v2.0.0, CC BY4.0. Only WID631 L1/L2
and the experiment metadata were selected and all3assets match publisher MD5.
EBW thermal data credit Raghawendra Pratap Singh Sisodia, Piotr Sliwinski and
Marek St. Weglowski, DOI10.17632/c7d9mt9h9x.1 version1, CC BY4.0; the original
368314-byte archive matches publisher SHA256. Source records are independently
attributed; their electrical and thermal observations are not one physical experiment.
Raw archives, video, scans, workbooks and transformed sample rows are excluded
from code distributions; public research includes aggregate checks and hashes.

Optional h5py3.16.0 is BSD-3-Clause and reads the reviewed HDF5 members. The package
and its native dependencies retain their own license notices when separately
installed. No h5py/HDF5 source or binaries are vendored in this code release.

The alarm source replay reuses the already attributed BattLeDIM v1 dataset,
DOI10.5281/zenodo.4017659, CC BY4.0. The original contributors and publisher
checksums are retained in research/alarm_source_results.json. The dimensionless
score and policy thresholds are independently authored illustrative transforms;
they are not supplied labels or calibrated performance. Raw/transformed rows
remain outside code distributions. The alarm state machine, human response and
actuator/statistical formulas call their respective existing owner APIs; their
implementations and third-party tables are not copied into this release.

Fastener force/torque data credit Adalto Farias, Fabrizio Leonardi and Sergio
Delijaicov, Mendeley Data v4, DOI10.17632/jffwrb4pcd.4, CC BY4.0. The selected
102 source CSV assets retain publisher SHA-256 pins. Rotation angle, differing
grids and the unresolved speed-unit conflict remain explicit. Reported friction
is not relabelled as a calibrated torque factor. See FASTENER_DATA_KO.

KupferDigital mechanical testing data credit Hossein Beygi Nasrabadi, Felix Bauer,
Ladji Tikana, Patrick Uhlemann, Steffen Tharig, Birgit Rehmer and Birgit Skrotzki,
Zenodo v2, DOI10.5281/zenodo.10820299, CC BY4.0. The 1608805-byte Files.zip matches
publisher MD5 a6808ba63957e6724c0ab983ae67a9d6. Source-specific parsing and stated
SI transformations are independently authored. Raw supplier files, procedures,
individual observations and transformed rows remain outside code distributions.
Public summaries retain attribution, source hashes, aggregate checks and limits;
they do not provide proprietary acceptance tables or vendor property conversions.

## Inspection statistics, cyclic quantities and separately held source pins

The inspection exchange calls MatIntegrity's `inspection.binomial_evidence`,
`inspection.hit_miss_pod`, `inspection.tofd_depth` and `geometry.thickness_grid`.
The cyclic exchange calls `fatigue.goodman`, `fatigue.hysteresis_path` and
`fatigue.strain_life`. All seven owner model versions were 1.0.0 in the recorded
verification. Their implementations remain owned and credited to the MatIntegrity
contributors under the preserved MIT record. No owner inference, confidence,
geometry, material, integration or root-finding implementation is vendored here.
The reference observations, inputs and explanations used for these checks were
independently authored. Numerical agreement is software verification and does not
establish inspection qualification, material acceptance or field validation.

The loss exchange also calls the same owner's `corrosion.power_law`,
`degradation.archard_wear` and `corrosion.gumbel_area_maximum`, each version 1.0.0.
Their laws, distribution fitting, diagnostics and bootstrap implementations remain
in the separately held MIT owner source. The authored scalar checks are original
verification inputs and do not qualify the physical corrosion or wear models.
See [the loss exchange guide](docs/CORROSION_WEAR_KO.md).

Final verification used a separately held private snapshot of 76 Python sources,
tree SHA-256
`0d9471bbe4e38b86b34610221e7adacd114e4e31d442fcdb9bb5afc7fec3efda`.
The source LICENSE, third-party notices and required catalog JSON resources were
preserved with that snapshot. The snapshot and its local path are outside this
project's release boundary and are excluded from source archives and wheels.
Earlier source fingerprints remain historical records. The shared source guard
checks the selected source bytes, actual loaded package/evaluator/registry and
source stability; it neither changes the source license nor certifies a model.
Public research records contain model/schema versions, hashes, aggregate checks
and limitations. Raw observations, transformed rows and detailed native receipts
remain outside distributions. See [inspection statistics](docs/INSPECTION_STATISTICS_KO.md),
[cyclic quantities](docs/CYCLIC_FATIGUE_KO.md) and
[integration](docs/INTEGRATION_KO.md).

## Copper corrosion sensor and AFM wear observations

The Kbely copper-wire sensor dataset credits Michal Kuchař and Jaromír Fišer,
[Zenodo version 0.2, DOI 10.5281/zenodo.10640939](https://doi.org/10.5281/zenodo.10640939),
under CC BY 4.0. The selected 2,545,534-byte CSV matches the publisher MD5; its
local SHA-256 and attribution are retained in the public source-review summary.
The 9,387 records are repeated observations of a sensor. CSV depth units and the
article-to-CSV scale, physical exposure origin and calibration remain unresolved.
No corrosion-rate, Gumbel or independent-specimen qualification is inferred.

The AISI 52100 AFM dataset credits Jack Walker, Jamal Umer, Mahdi Mohammadpour,
Stephanos Theodossiades, Stephen Richard Bewsher, Guenter Offner, Hemant Bansal,
Michael Leighton, Michael Braunstingl and Heinz-Georg Flesch,
[Dryad DOI 10.5061/dryad.s7h44j16j](https://doi.org/10.5061/dryad.s7h44j16j),
2021-05-17 release, CC0 1.0, as mirrored by Zenodo record 4743140. The selected
99,505-byte CSV matches the publisher MD5. It retains nine figure sections,
source units, signed values and distinct removed/pile-up volumes. Method-reviewed
pass distance supports descriptive dimensional volume per load-distance only;
it is not a dimensionless Archard coefficient. Matched scratch hardness,
distributed wear area, calibration support and independent scratch identities
remain unresolved. No nanoscale-to-pipeline transfer is qualified.

The two source CSV files, cached provider/method pages, pointwise observations,
transformed rows and detailed requests/receipts are excluded from distributions.
Public attribution, acquisition hashes, aggregate checks and the fourteen
withheld model-boundary requests are recorded in
[the corrosion source review](docs/CORROSION_SOURCE_KO.md) and
[its result summary](research/corrosion_source_results.json).
Those fourteen requests made no owner numerical-model calls. Source acquisition
and preservation do not establish native corrosion/wear integration verification,
physical calibration, POD, remaining life, human-error or period-failure probability.

## Chemistry, impact, hydrogen transport and axial profile capacity

The material-quality exchange calls the separately held MatIntegrity models
`materials.chemistry_reconciliation`, `materials.pitting_composition_index` and
`materials.impact_transition`. The transport exchange calls
`transport.semi_infinite_diffusion`, `transport.hydrogen_trap_equilibrium` and
`transport.hydrogen_slab_kinetic`. The profile exchange calls
`structural.profile_capacity`. Each recorded owner model version was 1.0.0.
The owner implementations remain credited to the MatIntegrity contributors under
the preserved MIT license. Interval comparison, composition-index calculation,
transition fitting, root finding, finite-volume transport, nonlinear/adaptive
solving and nodal area scans are not vendored in this distribution. Reference
assays, analytic cases, explanations and exchange checks were independently authored.

The two MatIntegrity primitives `reliability.independent_gate` and
`reliability.exclusive_paths`, version 1.0.0, were exercised through the existing
MaterialJob/Receipt contracts and compared with the separately installed
human-reliability-toolkit. Production risk calculations retain their existing
HRA/FTA/ETA and period-risk owners. This work adds no duplicate production gate
or event-tree implementation. An unscoped material probability is not relabelled
as HEP, mission probability or a measured annual event frequency; a repeated
event reference remains distinct from two independent copies.

These checks use the previously declared private MIT snapshot of 76 Python files,
tree SHA-256
`0d9471bbe4e38b86b34610221e7adacd114e4e31d442fcdb9bb5afc7fec3efda`.
Original LICENSE, third-party notices and required package JSON resources remain
with that source. They and the owner source were checked before and after execution.
The shared source-report verifier checks the consistency of expected fingerprint,
file count, before/after equality and execution claim. It is not a digital
signature, publisher authentication or independent proof that a computation ran.
The source snapshot, personal source paths and detailed native receipts remain
outside distributions. An unconfirmed owner repository or package URL is not
invented; separate owner distribution/version/source-pin records are required for
reproduction on another machine. See [the integration guide](docs/INTEGRATION_KO.md)
and [the owner extension requests](docs/EXTENSION_REQUESTS_KO.md).

## BAM S355 Charpy and MS-AHSS1300 permeation observations

The BAM source credits Markus Schilling, Steffen Glaubitz, Kathrin Matzak,
Birgit Rehmer and Birgit Skrotzki (2022), version 1.0.0,
[DOI 10.5281/zenodo.6778336](https://doi.org/10.5281/zenodo.6778336),
under CC BY 4.0 as recorded in the
[DOI rights metadata](https://api.datacite.org/dois/10.5281/zenodo.6778336).
Four selected assets, totaling 1,532,075 bytes, match publisher MD5 checksums.
The dataset description distinguishes the supplied manufacturer's composition
declaration from verified chemistry of the tested sheet's production lot. No
manufacturer composition or performance table is embedded or adopted here.
The six one-temperature, unbroken Charpy tests and their rounded secondary report
are not independent datasets or a fitted transition curve. Tested-lot chemistry
and transition support remain unresolved.

The electrochemical permeation source credits Giuseppe Macoretta (2025),
[Mendeley Data version 1, DOI 10.17632/tnsgf7v76z.1](https://doi.org/10.17632/tnsgf7v76z.1),
under CC BY 4.0 according to the
[original publisher snapshot](https://data.mendeley.com/public-api/datasets/tnsgf7v76z/snapshot/1).
The 88,885-byte workbook matches the publisher SHA-256. Its 1,859 rows form two
current histories with dependent observations within each history. Independence
between histories, physical specimen identities and active permeation area remain
unresolved. Total current is not converted into a lattice boundary
concentration or molar flux, and transport/trap/initial-state parameters are not
inferred. The descriptive nominal dimensions and source temperature remain
separate from unreported effective cell geometry or boundary conditions.

Source pages, license metadata, supplier checksums, version and creator records
are retained locally. Raw archives, PDFs, workbooks, original prose, observation
cells, transformed rows and detailed requests/receipts are excluded from source
archives and wheels. Public code contains independently written readers and
aggregate review only. The two groups are separate from the earlier literature
transition compilation and copper mechanical tests. Their nine original-condition
boundary requests were withheld and made no owner numerical-model calls.
Preservation does not establish physical calibration, material acceptance,
hydrogen embrittlement, service-life or field qualification. See
[the source review](docs/MATERIAL_PROCESS_DATA_KO.md),
[material-quality exchange](docs/MATERIAL_QUALITY_KO.md),
[hydrogen transport](docs/HYDROGEN_TRANSPORT_KO.md),
[profile capacity](docs/PROFILE_CAPACITY_KO.md) and
[existing risk comparison](docs/MATERIAL_RISK_REFERENCE_KO.md).

## Monitoring families and separately installed numerical dependencies

The monitoring exchange calls Process Monitoring Toolkit 0.1.0, copyright (c) 2026
Process Monitoring Toolkit contributors, under its preserved MIT license. Original
Monitor, registry factory, SignalBatch, feature, detector, scaler, forecasting and
calibration implementations remain in the separately held owner source. The selected
28-Python-file tree SHA-256 is
`a8856f3e30f7bddf7539dbe0b74ea7d47ada4127f52f765bf61e4a134300fe0a`.
The LICENSE, third-party notices and additional license resources were checked
before and after execution. This project does not vendor those implementations.

The isolated monitoring environment also uses separately installed scikit-learn
1.9.1 and statsmodels 0.15.0 (BSD-3-Clause), PyOD 3.6.7 (BSD-2-Clause), combo 0.1.3,
Numba 0.68.0 and llvmlite 0.50.0. The llvmlite distribution metadata declares
`BSD-2-Clause AND Apache-2.0 WITH LLVM-exception`; complete bundled and transitive
notices remain in those distributions. This list is not a replacement for those
notices or a declaration that every optional owner extra has the same license.
No worker environment or dependency binary is included in this code release.

The BattLeDIM application retains the existing original-contributor attribution,
version DOI [10.5281/zenodo.4017659](https://doi.org/10.5281/zenodo.4017659) and CC BY 4.0
record. Publisher MD5 and local SHA-256 checks apply to the three selected source
files. Public summaries contain aggregate split/quality diagnostics, not original
or transformed observations. Reference-normality hypotheses, detector parameters,
test interventions and mathematical checks were independently authored. Executing
59 selected IDs does not establish field performance, model qualification or a
calibrated probability. See [the monitoring guide](docs/MONITORING_EXCHANGE_KO.md).

The selected dynamic-subspace, neural and temporal-neural paths call the original
PMT/PyTorch implementations, including their graph and diffusion research models.
PyTorch was installed only in the separate monitoring environment: distribution
torch 2.14.1, actual runtime 2.14.1+cpu, no CUDA build. Its installed
License-Expression is `Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND
BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT`. Retain the complete upstream
license and bundled third-party notices; the PMT MIT notice does not relicense
this dependency tree. No torch binary, trained weight, tensor array or model
pickle is included in this package. Authored small training inputs, seeds,
resource limits and consistency checks are separate from the owner algorithms
and do not reproduce an external paper's performance claims. Provider identity
and wheel integrity are recorded with the separate acquisition evidence; see
[official PyTorch installation](https://pytorch.org/get-started/locally/) and
[the selected distribution metadata](https://pypi.org/pypi/torch/2.14.1/json).

## Hydrogen station and dynamic liquid pool source boundaries

The station exchange calls separately acquired
[hydrogen-station-sim](https://github.com/lyullee/hydrogen-station-sim) 0.1.2,
copyright (c) 2026 Ugwiyeon Lee, MIT. Original network, tank, restriction, EOS,
controller and full-reference simulation implementations are not vendored.
The runtime snapshot retains the original license and unchanged source files.
Its initially absent property table was generated with the original generator
and separately installed CoolProp 8.0.0; generator and generated table are pinned
as local runtime evidence and excluded from this distribution. This derivation
is distinct from a supplied experimental dataset or a qualified property table.

The pool exchange calls separately acquired
[DynamicLH2PoolX](https://github.com/lyullee/DynamicLH2PoolX) SDK 0.2.0rc2 at commit
`6a700e8e183039bbb81803bacc416f6c9117ed71`, copyright (c) 2026 Ugwiyeon Lee, MIT.
Eight original Python modules, LICENSE, README and project metadata are pinned;
source files were checked against the publisher Git blob identities when acquired.
Original shallow-layer, fixed-area, surface, saturation, evaporation and numerical
implementations are not copied. The separately installed CoolProp dependency retains
its own MIT record and bundled notices. Authored inflow histories, coefficients,
boundaries, balance checks and explanations are original verification inputs, not
supplier recommendations or reproduced experimental observations. Source snapshots,
generated property artifacts, detailed trajectories and local requests/receipts are
excluded from code releases. See [station](docs/STATION_EXCHANGE_KO.md) and
[pool](docs/POOL_EXCHANGE_KO.md) for scope and unverified physical boundaries.

## SAGA offline knowledge path and its independent dependency conditions

The knowledge exchange calls separately acquired
[SAGA-PY](https://github.com/lyullee/saga-py), copyright (c) 2026 Ugwiyeon Lee, MIT.
The project/CITATION version is 2.0.1 while the loaded namespace reports 2.0.0;
both are preserved with the selected source/resource SHA-256
`572eca2cd0bfae7363920a2680b2559a10a1d2797922fc084b447ed5d7545ffd`.
Original SQLite hybrid search, citation, HAZOP readiness and provider-missing
proposal implementations remain in that separately held source.

PyMuPDF 1.28.2 is an actual import dependency of the selected offline path:
`saga.rag → external_sources → indexer → pymupdf`, including `pymupdf.mupdf`.
No PDF open, extraction or indexing operation was executed in the recorded run.
The installed Artifex metadata declares dual licensing under **GNU AFFERO GPL 3.0
or an Artifex Commercial License**; see the
[upstream license](https://github.com/pymupdf/PyMuPDF/blob/main/COPYING).
SAGA's MIT license does not relicense PyMuPDF or remove those independent conditions.
Process isolation and the absence of an extraction call are not unconditional
copyright or licensing clearance. A combined source/binary/service distribution
requires its own dependency and applicable-license review. The current source
release does not bundle SAGA, PyMuPDF, its native libraries, the worker environment,
source corpus, database or detailed excerpts/receipts. A lighter owner library
path without the document importer is requested in PI-X07; no import stub or
patched owner code is used to claim that separation already exists.

The isolated environment's OpenAI SDK is an import dependency only; the recorded
worker executed no external model/API requests. Its two-document/four-passage
corpus and illustrative pending candidates were independently authored under MIT.
No retrieved external prose or safety-rule table is included in public research
summaries. Offline search and candidate readiness do not establish generated-answer
accuracy, regulatory currency or operational approval. See
[the knowledge guide](docs/KNOWLEDGE_EXCHANGE_KO.md).

## Steady buried thermal component

The buried thermal component calls the separately installed MIT-licensed ht 1.2.0
APIs `R_cylinder` and `S_isothermal_pipe_to_plane`, with the existing project cooling
model. It does not copy those library implementations, documentation examples,
standards tables or supplier material values. The official API reference is
[ht conduction](https://ht.readthedocs.io/en/latest/ht.conduction.html).
Geometry, material inputs, analytic reference cases and explanations here were
independently authored. Raw soil measurements or field-derived material parameters
were not used in this phase. Runtime/library source pins and numerical agreement
are software evidence, not transient soil/moisture coupling or field qualification.
See [the steady buried thermal guide](docs/BURIED_THERMAL_KO.md).

## Radial pipe/soil histories and independently authored audits

The radial transient exchange calls the separately held MIT-licensed MatIntegrity
model `thermal.transient_conduction_1d`. Its original finite-volume assembly,
nonlinear solve, property interpolation and thermal-storage implementation remain
in the existing 76-Python-file owner snapshot, SHA-256
`0d9471bbe4e38b86b34610221e7adacd114e4e31d442fcdb9bb5afc7fec3efda`.
Original LICENSE, third-party notices and required resources are retained with
that source, outside this release. The adapter does not vendor those algorithms
or introduce a duplicate heat solver. Reference geometry, thermal curves,
boundaries, logarithmic/eigenmode cases and explanation were independently authored.
Cell-volume/capacity and boundary-heat consistency audits are separate software
checks, not measured-soil or regulatory qualification. Detailed native inputs,
receipts and temperature histories are excluded from distributions. An unconfirmed
owner repository/package URL is not invented; reproducing the actual owner run
requires its separately supplied version, source pin and licensed resources.
See [the radial transient guide](docs/PIPE_SOIL_TRANSIENT_KO.md).

## Soil hydrothermal and borehole observation sources

The outdoor soil-cover observations credit Bing Wu and Hong-Hu Zhu (2025),
[Zenodo record 16415302, DOI 10.5281/zenodo.16415302](https://doi.org/10.5281/zenodo.16415302),
under CC BY 4.0 as confirmed in publisher and
[DOI rights metadata](https://api.datacite.org/dois/10.5281/zenodo.16415302).
The API version-label field is absent; the fixed record and source file checksums
identify the selected first published version. Four measured-source workbooks
total 5,764,077 bytes and match publisher MD5. The two numerical-model workbooks
were not acquired or used as measured observations. Daily extrema, opaque source
level/treatment labels, unresolved units, calendar timezone and a header/value
alignment inconsistency remain explicit. No supplier prose, observation rows,
workbook formulas or inferred material table is embedded in this code release.

The processed borehole heating observations credit Bo Zhang, Kai Gu, Peter Bayer,
Haibo Qi, Bin Shi, Baojun Wang, Yuehua Jiang and Quanping Zhou (2022), version 1.0,
[DOI 10.5281/zenodo.6480800](https://doi.org/10.5281/zenodo.6480800), CC BY 4.0 confirmed
in [DOI rights metadata](https://api.datacite.org/dois/10.5281/zenodo.6480800).
The selected estimation input and two method-source files total 2,058,814 bytes
and match publisher MD5. Method files support unit/processing review only; no
MATLAB method, optimization, preprocessing or supplier property coefficient is
executed or copied into this distribution. The complete original measurement
asset was not acquired. Processing time, vertical depth, irregular intervals
and incomplete trace-specific correction lineage remain separate from a radial
pipe geometry or an independently validated boundary. Raw files, method source,
metadata/page caches, original/transformed observation arrays and detailed
receipts remain outside release archives. Original readers, aggregate source
checks and explicit SI conversions retain their attribution and limits in
[the soil data guide](docs/SOIL_DATA_KO.md).

## Optional FiPy cross-section dependency and complete notices

The optional cross-section path uses separately installed FiPy 4.0.3. Its
[NIST Terms of Use](https://pages.nist.gov/fipy/en/4.0.3/LICENSE.html) describe the
status of NIST employee work in the United States and separately grant use and
distribution permissions subject to retained notice and warranty disclaimer.
They also address possible foreign copyright. This package does not label every
part of FiPy or its dependency tree copyright-free. The installed main license
SHA-256 is `83c519e315a2da6c78aa822989e896358a870ebb51246b3049f671eb84b6f16e`.
FiPy additionally carries the separate
[permissive copyright/permission notice](https://github.com/usnistgov/fipy/blob/4.0.3/fipy/tools/dimensions/LICENSE)
for Konrad Hinsen's 1997–2006 dimensions code; installed notice SHA-256
`adc70fb3fc093d4a744118fdf94e0ee91b6f14884c4f9274bf6ad1322d71e252`.
Both complete notices remain in the separately installed distribution. Solver,
mesh and property-library implementations and upstream example scripts are not
vendored here; no combined binary environment is released by this source package.

The additive dependency installation included Typer 0.27.3 (Sebastián Ramírez,
MIT) and its separately credited vendored Click code (Pallets, BSD-3-Clause).
Shellingham 1.5.4, Rich 15.0.0, markdown-it-py 4.2.0 and mdurl 0.1.2 retain their
own installed notices. Existing NumPy, SciPy and Matplotlib installations were
not upgraded for this addition. Preserve complete bundled and transitive notices
when distributing an environment; FiPy's principal notice is not a substitute
for them. See [the official FiPy project](https://github.com/usnistgov/fipy) for
the original software and supported documentation.

## Optional finite elements and observation document reader

The separate `soil-fem` extra uses scikit-fem 12.0.2, BSD-3-Clause, by its
credited contributors. The [official project](https://github.com/kinnala/scikit-fem)
and separately installed distribution retain the complete license (installed
license SHA-256 `383a8df7a707975913b2439594903a9b7e4f31372da3cd7be9b096ce1839ed34`).
Native triangulation, basis, assembly, quadrature and sparse linear algebra are
called through their APIs; their implementations are not copied here. The source
tree and runtime versions used for calculations are pinned in the native receipts.
Original polygon meshes, test fields, conservation checks and explanations do not
establish measured soil, curved pipe, moisture or fluid-coupled qualification.

The optional data readers use separately installed pypdf 6.10.0, BSD-3-Clause.
The [official project](https://github.com/py-pdf/pypdf) retains contributor
attribution and its complete notice (installed license SHA-256
`a97ac230e5f33ef10a5367a850eb01f91f1a0b064e34742c7794d2294557f524`).
Its official wheel SHA-256 is
`90005e959e1596c6e6c84c8b0ad383285b3e17011751cedd17f2ce8fcdfc86de`.
No reader binary, source document, extracted table or source prose is bundled in
this release. Preserve all original and transitive notices if redistributing an
environment. These permissive dependencies do not replace the source dataset's
own attribution and reuse conditions.

The measurement uncertainty code uses the general sensitivity/covariance relation
described by [NIST](https://www.nist.gov/pml/nist-technical-note-1297/nist-tn-1297-appendix-law-propagation-uncertainty).
Its implementation and examples were independently authored. No NIST procedure,
standard text, industry acceptance table or calibration certificate is reproduced.
Caller-selected coverage and limits remain explicit declarations; no legal
custody-transfer conformity is inferred.

## Flowmeter calibration and compressed-air observation sources

Ara Abdulrahman and Menne Schakel (2026) published the LNG/water calibration data,
[fixed Zenodo record 22274102](https://doi.org/10.5281/zenodo.22274102), CC BY 4.0,
as confirmed in [DOI rights metadata](https://api.datacite.org/dois/10.5281/zenodo.22274102).
The chosen workbook and method document total 364,146 bytes and match publisher
MD5 and locally recorded SHA-256. The latest fixed record has no API version label;
the document's internal release label is recorded separately. Original readers
convert explicit units and retain run/setpoint/meter identity, repeat-average
meaning, stated expanded uncertainty and missing covariance. A sheet label
containing LH2 does not change the documented liquid LNG campaign into hydrogen
observations. No REFPROP implementation or proprietary property coefficients are
copied or executed here.

Eli Wilfredo Zavaleta Aguilar, Beethoven Narváez-Romo and Jose R Simoes-Moreira
(2023) published the vortex-tube research observations,
[Mendeley Data V1, DOI 10.17632/8zktfd9k9n.1](https://doi.org/10.17632/8zktfd9k9n.1),
CC BY 4.0 as confirmed in the
[publisher snapshot](https://data.mendeley.com/public-api/datasets/8zktfd9k9n/snapshot/1).
The selected 183,183-byte document matches publisher SHA-256. Original processing
retains gauge/atmospheric pressure, pressure differences and temperature; missing
geometry/taps/composition/reference flow/time/uncertainty are not filled with
authored demonstration defaults. Acquisition does not establish independent gas
meter validation. Source documents, observation rows, extracted tables and complete
receipts remain outside release archives. See [the source guide](docs/METER_DATA_KO.md).

## Correlated sonic-nozzle calibration observations

Steffen Martens, Katy Klauenberg, Bodo Mickan, Catherine Yardin, Nicolas Fischer
and Clemens Elster (2020) published the EMUE calibration material, version 1.0,
[Zenodo record 4016916, DOI 10.5281/zenodo.4016916](https://doi.org/10.5281/zenodo.4016916).
CC BY 4.0 is confirmed in [DOI rights metadata](https://api.datacite.org/dois/10.5281/zenodo.4016916).
The selected 1,406,894-byte archive matches publisher MD5 and local SHA-256.
Original processing retains the distinction between laboratory estimates and
publisher Monte Carlo derived x/y estimates, their full cross-point/cross-axis
covariance, stated Type A uncertainties and unavailable independent validation.
Incorrect workbook declared dimensions are preserved alongside observed extents;
the source bytes are unchanged. Supplier MATLAB/program files are not executed,
copied into the package or relicensed. Observation rows, covariance arrays,
method documents and detailed receipts stay outside release archives. The code,
explanations and numerical reference checks here were independently authored.
See [the calibration source guide](docs/CALIBRATION_DATA_KO.md).

## Optional calibration comparison software

The production EIV service calls the existing SciPy least-squares entry point;
it does not require odrpack. The separately installed odrpack 0.7.0 comparison
uses the [official Python project](https://github.com/HugoMVale/odrpack-python)
and its original ODRPACK/Fortran dependency. Their principal licenses are MIT,
but the tested Windows wheel also contains GCC/Fortran/C++/quadmath/thread
runtimes with separate terms. They are not all relabeled MIT. Preserve complete
upstream notices and applicable GPL Runtime Library Exception/LGPL and other
runtime conditions if distributing an environment. This repository does not
redistribute the wheel, DLLs, runtime source or an assembled environment.
The comparison is an explicit optional extra (`calibration-reference`); supplier
wheel and actual binary/source hashes are recorded in its native verification.
Native success, rank-deficiency terminology, residual-scaled standard errors
and the unsupported full-vector attempt are kept distinct in
[the EIV guide](docs/EIV_CALIBRATION_KO.md).

Pydantic and pydantic-core remain separately installed dependencies with their
original notices. The finite JSON guard uses their native serialization API
without modifying owner classes or vendoring implementations. No other project
is relicensed by this package's MIT license. Spatial rate and integer-PMF
accounting delegates to the separately supplied, source-pinned PRISM/HRA engines;
original geometry, exposure examples and source/primitive consistency checks
do not constitute independent physical or regulatory qualification.
## 추가 공정 자료와 기존 열 장치 경계

정유공장 자료는 Ntafalias, Aristotelis; Chrysovalantis, Savvidis; Lamprinoudakis,
Pantelis; Kitsatoglou, Alkis (2025), *Refinery Compressor Sensor Data, One-Year Dataset*,
[DOI10.5281/zenodo.14866092](https://doi.org/10.5281/zenodo.14866092), CC-BY-4.0이다.
원 DCS 관측을 맵·서지 truth로 재표시하지 않는다.

극저온 자료는 Reza, Mohammad S; Ignatoff, Philip; Almacddissi, Jimmy; Hartwig,
Jason; Chung, Jacob; Song, Youngsup (2026), *Decoupling Surface Topography from
Gravitational Acceleration in Cryogenic Pool Boiling*,
[DOI10.5281/zenodo.19792024](https://doi.org/10.5281/zenodo.19792024), CC-BY-4.0이다.
실험/ANSYS 처리·U열과 원 압력/온도 파일을 구분한다. 원 workbook의 제3자
비교 열은 이 코드의 모델 보정/공개 수치 집계에 사용하지 않는다.

NASA 자료는 John C. Aydelott (1967), NASA TN D-4171, NTRS19670028965다.
[원 NTRS](https://ntrs.nasa.gov/citations/19670028965)는 미국정부 작업의 공개 사용
허용을 명시한다. supplier checksum이 없어 로컬 SHA-256을 이전 취득 pin으로
사용하며 표 OCR을 자동 검증된 수치로 배포하지 않는다.

이 자료 원본·행과 상세 receipt는 배포에 포함하지 않는다. 새 압축/상변화 경계는
기존 lh2dt_models MIT, CoolProp MIT, SciPy BSD API를 실제 호출한다. NumPy의
기존 선형해석으로 인양 평형을 계산하며 제조사 WLL/규격 표를 내장하지 않는다.
별도 새 바이너리나 소유 엔진 소스는 재배포하지 않는다.


## Human-in-the-loop process-control experiment data

Ammar N. Abbas and Winniewelsh, *Human-in-the-Loop Decision Support in Process Control Room Dataset*, Zenodo v1.1.2, [record 13984198](https://doi.org/10.5281/zenodo.13984198), declares CC BY 4.0 for the deposited asset. The record links to the 2024 Data in Brief article by Amazu et al. The selected archive also contains a separate MIT LICENSE file; its scope is recorded separately and is not substituted for Zenodo's file-level data license. This repository does not redistribute the archive, workbook rows, README, or bundled software. See [the source and privacy guide](docs/HUMAN_CONTROL_ROOM_DATA_KO.md).
