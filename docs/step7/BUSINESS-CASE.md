# UrbanEcho business case

**Planning date: 9 October 2026 · Initial buyer: residential community management, confirmed by the product owner.**

UrbanEcho could help a residential community's facilities manager see where excessive common-area noise occurs, respond while it is happening, and review a consistent history. The current deliverable is a local prototype demonstrated with simulated recordings. It is not yet a field-validated acoustic instrument or a commercial service. All prices, adoption numbers, improvements and future dates below are planning assumptions unless a source is attached.

## 1. A specific first customer

The intended daily user is the facilities manager of an Indian apartment community with several shared spaces and an existing Wi-Fi network. The buyer is the residents' association or management committee that approves the facilities budget. Security supervisors can act on an alert; committee members can review a weekly summary. Residents benefit from better-managed common areas but are not automatically product users or paying customers.

The first proposed pilot covers **three shared locations**: a clubhouse activity area, an entrance/vehicle access area, and a shared garden or play area. Placement, operating hours and thresholds must be agreed with the community after physical calibration and a site assessment. The existing simulator's North garden, Workshop and East gate are fictional demonstration locations, not evidence of a residential deployment.

The problem to validate is repeated time spent investigating noise complaints without a reliable location/time record. UrbanEcho's potential value is earlier awareness and a shared basis for operational decisions, such as changing event timing or investigating a recurring equipment noise. We have not demonstrated reduced complaints, health benefits, enforcement suitability, market demand or willingness to pay. Existing sound meters, manual complaint registers and scheduled inspections remain alternatives to compare during interviews.

## 2. What can be offered today, and what must precede a pilot

Today, demonstrate saved audio uploads, calculated levels, thresholds, incident recovery, visible notifications, maps and daily summaries using explicitly simulated inputs. The Step 6 report records two successful three-location runs, 382 backend tests, 34 frontend tests and checksum verification of 300 recordings on 9 October 2026. These are previously recorded software results, not customer or acoustic evidence. [Step 6 verification](../../STEP6-VERIFICATION.md)

ESP32/INMP441 firmware has compiled; the actual microphone, Wi-Fi path, capture timing, reliability and acoustic accuracy still need testing. Physical pilot entry therefore requires:

- Complete **real microphone → ESP32 → UrbanEcho → incident alert → daily summary**, including temporary disconnection and recovery.
- Agree a reference measurement procedure, calibrate the installed assembly, and record comparison results and uncertainty. Digital dBFS and synthetic SPL must not be presented as trustworthy environmental dB SPL.
- Confirm the community's authority to operate the pilot and agree where audio is captured, who may access it, how residents are informed, the retention period and deletion procedure. Raw recordings can contain speech; the current backend saves audio, not only numbers.
- Establish an operator, a backup/restore procedure and a capacity budget. The prototype runs on a local computer; the prepared LAN HTTPS device listener is not enabled. It has no resident accounts, tenant separation, role-based viewer access, automatic audio retention or billing.

This is an operational learning pilot, not a promise of compliance certification, person identification or automatic penalties. The product does not classify the source of a sound or prove who caused it.

## 3. Pilot design and measurable value

Proposed pilot: one community, three devices, five staff/committee reviewers, and eight weeks after the entry conditions above. Use two weeks to record the existing complaint-handling baseline, followed by six weeks of monitored operation. A named operator manually records response times and feedback; UrbanEcho does not currently implement an acknowledgement workflow, complaint register or usage analytics.

| Question | Proposed measure and decision criterion | Evidence to collect |
|---|---|---|
| Does data arrive reliably? | At least 90% usable coverage of agreed continuous operating hours, with separate full-calendar-day coverage shown | Saved duration/coverage, reported drops, gaps and device outages; state the denominator |
| Can staff act promptly? | Median acknowledgement time 25% below the baseline, as a hypothesis to test | A timestamped manual response log; report sample size and confounding changes |
| Is the signal useful? | Review at least 20 complaints or agreed noise exercises; record relevant alerts, missed events and nuisance alerts | Staff observations matched to incident times and a reference instrument where available |
| Is reviewing manageable? | A manager can review yesterday's three locations in under ten minutes | Timed tasks and feedback; no claim that time savings are already achieved |
| Will people keep using it? | Four of five invited reviewers participate in each of the final four weekly reviews | Attendance/use log and short interviews |
| Will anyone pay? | A written continuation decision from the buyer, including a tested price and named budget owner | Interview notes or an actual purchase commitment, not a forecast counted as revenue |

Agree the acceptable acoustic comparison error with a qualified measurement partner before testing; this document does not invent an accuracy specification. Do not interpret sparse sampling as an all-day noise level. If there are too few genuine complaints, report that limit and keep controlled exercises separate.

## 4. Reaching the first 100 users

**A user means a distinct facilities, security or committee reviewer who uses the product or a supervised review to make a decision at least weekly for four consecutive weeks.** It does not mean an apartment resident, an installed microphone, a sales lead, a paid account or a demo viewer. Initially track participation manually; count independent authenticated use only after appropriate account controls exist.

| Stage | Acquisition work | Cumulative planning target |
|---|---|---|
| Discover | Interview ten community managers/committee buyers through existing introductions and local facilities-management contacts; test the complaint workflow and audio-retention acceptability | Ten interviews; no user count claimed |
| Establish one pilot | Recruit one willing community, train five reviewers and publish an approved, anonymised case study only if outcomes support it | Five recurring users; one potential buyer |
| Repeat with referrals | Ask the first buyer for introductions; work with one local facilities-management provider; run small supervised demonstrations | Five communities × five recurring users = 25 |
| Expand only after acceptance gates | Use measured results, manager referrals and scheduled training to reach twenty communities | Twenty communities × five recurring users = 100 |

Twenty communities are twenty buyer organisations at most, not twenty paying contracts. A three-device package for every community would mean up to **60 devices**, a scale not yet verified. Accounts/access control, installation support, storage capacity and load tests are gates before that expansion. These are targets for learning; no customers, partnerships or conversion rates have been confirmed.

## 5. Proposed revenue and business model

Test an **eight-week pilot service fee of ₹10,000–₹20,000**, with hardware, installation and independent calibration quoted separately at cost. For a validated product, interview buyers about **₹7,500–₹12,000 per community per month** for up to three devices, a local dashboard, maintenance and a defined support allowance. Extra locations, site visits and calibration would have explicit prices. These are unvalidated price hypotheses, not an available subscription or a claim of profitability. No billing, purchasing or paid deployment was initiated.

Prefer a community/site fee over charging each resident. It aligns the payer with common-area management and keeps the first offer understandable. Retain a local installation option: hosted service, multi-community management and public resident access would require additional implementation and acceptance checks.

| Business model canvas | Current proposal |
|---|---|
| Customer segments | Residential community committees and facilities-management companies; daily users are on-site managers and security supervisors |
| Value proposition | A time/location history, prompt excessive-noise awareness and coverage-aware daily review for common areas |
| Channels | Direct manager interviews, committee introductions, facilities-provider referrals and supervised demos |
| Customer relationships | Assisted installation, clear training, scheduled pilot review and bounded support |
| Revenue streams | Pilot service fee; later site subscription and separately quoted installation/calibration/support |
| Key activities | Device validation, reliable capture/processing, calibration coordination, support, backups and privacy/retention operations |
| Key resources | Current software and firmware, validated device assemblies, local server/storage, operator documentation and measurement expertise |
| Key partners | Community management, component suppliers, installation/IT support and a qualified calibration/reference-measurement provider; none confirmed |
| Cost structure | Components, enclosures/power, installation, calibration, audio storage/backup, computing, support labour and replacements |

## 6. Device, storage, hosting and maintenance economics

### Device and operating allowances

The known assembly is an ESP-WROOM-32-based development board with an INMP441. Its exact carrier revision remains unconfirmed. Retail listings are cost references, not confirmation that a listed replacement is the user's exact board.

| Item | Planning value | Basis and limitation |
|---|---:|---|
| ESP-WROOM-32 38-pin development board | ₹425 each | Current displayed seller price; shipment/stock may change. [Robocraze board listing](https://robocraze.com/products/esp32-development-board) |
| INMP441 breakout | ₹148 each | Current displayed seller price, page also reports sold out; obtain a fresh availability/price quote. [Robocraze microphone listing](https://robocraze.com/products/inmp441-mems-high-precision-omnidirectional-microphone-module-i2s) |
| Bare board + microphone subtotal | ₹573 | Arithmetic only; not a deployed or calibrated device price |
| Complete pilot assembly | ₹1,800–₹3,000 per device; ₹5,400–₹9,000 for three | Unquoted allowance including the electronics, USB power/cable, wiring, basic enclosure/mount and spares; outdoor protection and installation labour need a site quote |
| Reference/calibration provision | ₹10,000–₹25,000 per three-device pilot | Unquoted allowance for borrowed/rented reference equipment or external service; supplier and method not selected; excludes purchasing a certified instrument |
| Local primary + backup storage | ₹8,000–₹14,000 one-off allowance for two 1 TB drives | Unquoted budget, conditional on interface and drive choice; two copies are needed, and database/index growth must be measured |
| Recurring maintenance | ₹3,000–₹9,000 per community/month | Assumed 4–12 hours × ₹750/hour; record actual support, backup checking and periodic device inspection time; travel and recalibration extra |
| Local hosting today | ₹0 external hosting fee | Existing computer/Wi-Fi assumed; electricity, equipment wear, disks and labour are not free |

### Audio is the main capacity driver

At the firmware default of **one 1-second recording every second, 16,000 samples/second, mono PCM24**, each ordinary WAV uses `16,000 × 3 + 44 = 48,044 bytes`. That is 86,400 files and **4.151 GB per device per day**. Calculations use decimal GB; GiB uses 1,073,741,824 bytes. Multipart/network overhead, filesystem allocation, database records/indexes, logs and backup versions are additional.

| Scenario | Original audio retained/created | WAV file count | Interpretation |
|---|---:|---:|---|
| Three devices, seven continuous days | 87.17 GB / 81.18 GiB | 1,814,400 | Proposed short pilot retention capacity; deletion is not automated today |
| Three devices, thirty continuous days | **373.59 GB / 347.93 GiB** | **7,776,000** | One full audio copy; a second complete copy doubles the audio capacity to about 747.18 GB |
| Thirty devices, thirty continuous days | 3.736 TB | 77,760,000 | Capacity model only; not a verified supported deployment size |
| Three devices, one second per minute for thirty days | 6.23 GB | 129,600 | Hypothetical 1.67% time coverage, not continuous monitoring; longer gaps can prevent the current contiguous recovery rule from completing |

An unchanged always-on three-device setup would add about **4.55 TB of original WAV files per 365-day year**. Keeping all audio indefinitely cannot be budgeted as a small fixed database. Do not silently reduce capture cadence to meet a storage budget: that changes what incidents and summaries represent. Agree retention and coverage first; safely coordinated deletion/archive would be future work, preserving database/audio consistency. [Current recording/queue profile](../../firmware/esp32/README.md)

### Optional hosted cost comparison — not a deployment plan

The current backend stores WAV files on a filesystem volume. An attached cloud block volume is a closer cost comparison than pretending object storage is already integrated. For an illustrative single-host pilot, reserve a 4 GiB/2-vCPU VM at **US$24/month**; this is a price reference, not a proven capacity recommendation. [DigitalOcean compute pricing](https://www.digitalocean.com/pricing/droplets)

At **US$0.10/GiB/month** for provisioned block storage, a 128 GiB audio volume costs $12.80 and 512 GiB costs $51.20. Allow extra capacity if measured database/file overhead exceeds the reserve. [DigitalOcean volume pricing](https://docs.digitalocean.com/products/volumes/details/pricing/)

One logical off-site audio backup at **US$6.95/TB per 30 days** would be about $0.61 for the seven-day audio set or $2.60 for the thirty-day set. That price alone does not provide backup software, database consistency, retention versions or restoration testing; object-store backup is not implemented here. [Backblaze B2 pricing](https://www.backblaze.com/cloud-storage/pricing)

| Illustrative three-device steady-state scenario | Compute | Provisioned audio volume | One logical audio backup | Indicative subtotal/month |
|---|---:|---:|---:|---:|
| Seven-day retention, 128 GiB volume | $24.00 | $12.80 | $0.61 | **$37.41** |
| Thirty-day retention, 512 GiB volume | $24.00 | $51.20 | $2.60 | **$77.80** |

Subtotals exclude taxes, currency conversion, domain, database backups, additional backup versions, excess transfer, operational labour and availability redundancy. Prices were checked on **9 October 2026**; seller availability and hosting terms must be rechecked before procurement. No exchange rate is assumed. The current Backblaze page says $6.95/TB; older $6/TB pages were not used. None of these services has been purchased or enabled.

Support and calibration may cost more than hosting. Validate contribution margin using actual revenue minus support, hardware replacement, storage, installation and calibration allocation. The proposed subscription can fail commercially if support effort stays near the high end; do not assert a margin before a pilot measures it.

## 7. Twelve-month plan: October 2026–September 2027

| Period | Product and adoption work | Evidence required to proceed |
|---|---|---|
| October–November 2026 | Complete Step 8 software acceptance; flash/test one physical board; perform microphone/reference comparisons; interview ten residential-community buyers | A signed-off physical flow, known limitations, calibration evidence and an agreed pilot/retention protocol |
| December 2026–January 2027 | Run the first three-location, eight-week pilot with five reviewers; record baseline and monitored results | Coverage/drop logs, nuisance/missed-alert review, operator time, buyer decision and measured storage growth |
| February–March 2027 | Fix pilot findings; implement agreed retention/backup operations and appropriate viewer access; prepare support and installation procedures | Restore test, access review, repeat physical tests and a documented unit-cost model |
| April–May 2027 | Expand cautiously to five communities and target 25 recurring users; seek referrals and test actual paid continuation | At least two willing paying buyers as a target; site-by-site acceptance and evidence that support is sustainable |
| June–July 2027 | Target twelve communities / 60 recurring users; validate larger device/file loads and operator capacity before each increase | Load/capacity results, onboarding completion, ongoing acoustic checks and acceptable support workload |
| August–September 2027 | Work toward twenty communities / 100 recurring users; review renewal, retention and delivery economics | Measured recurring use and buyer renewals; revise or stop expansion if evidence does not support it |

These dates are conditional targets, not promises or implemented automations. A missed hardware, privacy/retention or reliability gate moves adoption later.

## 8. Uncertainties to resolve first

1. **Measurement:** physical capture and calibration are still pending; microphone placement, enclosure response and real Wi-Fi reliability can change results.
2. **Demand:** residential management was selected as the buyer, but no customer interviews, commitments, savings or revenue have been demonstrated.
3. **Audio acceptance:** the buyer and residents may reject raw-audio retention; capture purpose, access and deletion must be agreed before a field pilot.
4. **Capacity:** millions of small files and metadata writes are untested at commercial scale; no load/uptime claim follows from the current three-device simulator.
5. **Operations:** sleep/power loss on the local computer stops service; short RAM queues can lose recordings; long disconnections and sparse sampling reduce usable coverage.
6. **Product gaps:** independent viewer accounts, tenant isolation, complaint/acknowledgement workflow, usage analytics, automatic retention and billing remain future work.

**Decision now:** use the verified local software demo to test the residential-management problem and complete one real calibrated device path before promising field performance or a paid service.
