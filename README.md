# Pool Strip Reader

Photograph an HTH Early Warning test strip and get an exact, ordered dosing plan for the pool. Runs as a Home Assistant app (sidebar via ingress, plus an optional password-protected HTTPS port for a Home Screen PWA or Cloudflare Tunnel), the same hosting pattern as 43 Links Power.

## Layout

| Path | What it is |
|---|---|
| `pool-app/server.py` | HTTP server, people and sessions, API, SQLite state (one row per person), strip photos, pool shop reports |
| `pool-app/chemistry.py` | Status, dosing plan, carbonate maths, water balance, trends |
| `pool-app/ha_pool.py` | Home Assistant sensors, reminders, weather sampling |
| `pool-app/tls_manager.py` | Standalone HTTPS and Let's Encrypt (copied from 43 Links Power) |
| `pool-app/chart.json` | Reference colours of the bottle chart (generated, see below) |
| `pool-app/public/` | The phone interface: `app.js`, `strip.js` (strip finding and reading), `color.js` |
| `pool-app/ha-app/` | Home Assistant manifest, Dockerfile, launcher, docs |
| `pool-app/scripts/` | Packaging, local preview, chart derivation |
| `IMG_079*.HEIC` | The original bottle and strip photos |

## Light box

`enclosure/` holds a 3D-printed box the phone lies on, camera down (design notes, the Fusion build script and STLs). It has two lids, both for the phone with its case on: one for the iPhone 17 / 18 Pro Max and one for the iPhone 14. `enclosure/design-notes.md` ends with how to load the script into Fusion 360 to change the box or add a phone. In the app, **Settings → Light box** turns on a live-camera mode for it: the torch comes on, the ultra-wide lens is chosen, ring positions are remembered per phone, and a test is captured automatically once a strip is seen and steady. **Calibrate colours** measures the bottle's chart inside the box from one picture of the label stuck flat on the calibration slide (`findChartGrid` finds the grid of swatches any way up; `measureChartGrid` reads each against the label's white) and stores it server-side (`/api/chart`, with the picture, served as `/api/photo?id=calibration`). The state then carries two charts: `chart` (built-in, daylight) for ordinary photos and `boxChart` for strips read in the box, because torch-lit video and daylight photos render the same pad very differently. Per-phone choices (lens, ring positions, calibration in progress) live in the phone's `localStorage` under `pool-*`.

The live camera needs a secure origin, so it works from the standalone HTTPS address but not inside the Home Assistant sidebar.

## Pool shop tests

`POST /api/lab` adds a test done at a shop, stored as an ordinary test with `source: 'lab'`, readings marked `confidence: 'lab'`, and a `lab` block (shop, meter, `extras` for everything beyond the four readings, `conditions`, `advice`, `url`). Because it is an ordinary test, the history, charts, plan and Home Assistant sensors need no special case; the newest shop test with a calcium figure also sets `settings.calciumHardness`.

- **By link** (`{url}`): shops with a LaMotte meter send `https://solutions.waterlinkconnect.com/treatment-reports/<code>`. That page is a script that reads JSON from `https://wls-api.waterlinkconnect.com/treatment-reports/<code>` with no sign-in, and `fetch_report` reads the same address. `LAB_LINK` accepts only that page address or a bare code of hexadecimal digits, the host is the constant `LAB_API`, and nothing else from the request reaches the outgoing call. `read_report` treats the answer as hostile: figures are matched by WaterLink's factor number and unit (`LAB_FACTORS`) and range-checked, the advice HTML is reduced to words by `plain_text`, and the customer's details are never read. The test's id is a hash of the code, so a report is kept once.
- **By hand** (`{manual, photo}`): the same record from typed figures. The picture is saved as `<id>.lab.jpeg`, which keeps it out of the three-month rule for strip photos (`prune_photos` keeps the newest `LAB_PHOTOS`).

A shop test is usually entered after the event, so three rules keep it from disturbing anything newer. `PoolShop` and `test_chemistry.py::ShopTests` pin them:

- **Its place is its own date.** `at` is the report's `waterTestDate` (the time shown top right on the report; checked against the live page), and everything that asks for "the latest test" goes by `at`. The by-hand form leaves the date empty on purpose.
- **A plan is only good for the water that was tested.** `chemistry.hold_if_dosed_since`, applied in `current_analysis`, empties the steps of any test that has doses logged after it which are not its own, and says why. Doses are only ever logged against the current test, so this arises for a late-entered shop test or after deleting the newest test.
- **Settings and trends are not rewritten.** Calcium is only set when the current figure is empty or came from an older shop test (`add_lab_test`); `chemistry.drift` ignores shop tests, because a strip and a meter differ by up to a colour step on the same water.

`chemistry.strip_checks` pairs each shop test with the nearest strip test and judges each reading (`strip_verdict`: within half a chart step), skipping readings that are too far apart in time (`CHECK_WINDOW`), dosed in between, or a poor colour match. The view sends the result as `stripChecks`.

`tests/test_server.py::PoolShop` covers both with a stand-in for the shop's service; no test contacts WaterLink. The service's format is not documented or promised, so if imports start failing, compare a live answer with `shop_report()` in the tests.

## Develop

```sh
cd pool-app
npm run check && npm test
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/preview.py --demo     # http://localhost:8745 with invented tests
python3 scripts/preview.py --demo --login   # the same with sign-in on: the owner and one invented person
```

## People

One owner, plus anyone the owner adds under **Settings → People**. Row 1 of `state` and of `users` is the owner; every other person has a row in each with the same id, and their photos in `/data/photos/u<id>/` (the owner's stay in `/data/photos`). Ids are never reused.

- **Who is asking** is decided in one place, `Handler.who()`: Home Assistant ingress is always the owner; on the standalone address it is the session cookie, which is signed over the person's id and their current password hash (so a new password or switched-off access ends their sessions). The owner's username and password come from the app configuration, not the database.
- **Every data function takes the person's id** and defaults to the owner, which is what `ha_pool` relies on: it never passes one, so Home Assistant only ever sees the owner's pool. `water_temp()` keeps the owner's live sensor out of other people's dosing.
- **A new person's state carries `needsSetup`** until they give their own volume and surface; tests and plans are refused before that, because `DEFAULT_SETTINGS` describes the owner's pool.
- **On an iPhone the Home Screen comes before all of it.** `start()` shows `installGuide()` before the sign-in when `canInstall()` (iPhone or iPad, not already opened from the Home Screen, not inside a frame such as the Home Assistant sidebar) and the browser has not been asked (`pool-install-asked`). It is first because a Home Screen web app keeps its own cookies, storage and camera permission: whatever a person set up in Safari would have to be done again. The wording allows for Safari's layouts: iOS 26 put Share behind the three dots beside the address bar, and added the "Open as Web App" switch, on by default. Nothing here can be unit tested; it was checked in the preview pane, which is not Safari.
- **Calibrating the light box is the step after that.** The view's `needsCalibration` is derived, not stored: true while the person has no measured chart (`state['chart']`) and has not said they have no box (`state['noLightBox']`, set through `POST /api/lightbox {have}` and cleared by a calibration). It blocks nothing. In `app.js`, `render()` shows the full-screen guide (`calibWelcome`) once per phone and person (`pool-calib-asked-<username>` in `localStorage`) and only where the live camera exists; after that `calibReminder()` keeps a card on the Today screen. The steps themselves are `calibSteps()`, shared with Settings → Calibrate colours. `People::test_a_new_person_is_asked_to_calibrate...` pins the server side.
- **Colour is never the only signal.** Low, high and unclear carry an arrow or a shape as well (`STATUS_MARK`, `marker()` in `app.js`), because one of the people using the app is colour-blind.
- **Sessions** carry their issue time and no expiry. `who()` refuses one that is older than a cookie can live (400 days unused), older than the person's last sign-out (`users.sessionsFrom`), or signed against a password that has since changed. `/api/state` hands the cookie back on every load and swaps it for a fresh one once a day. On HTTPS the cookie is `__Host-pool_session`; the old name is honoured only for pre-0.7.0 sessions, which carried an expiry in that field.
- **Limits that protect the machine** are the constants at the top of `server.py`: body sizes by endpoint (`SIGN_IN_BODY`, `SMALL_BODY`, `MAX_BODY`), `MAX_HEADERS`, `MAX_CONNECTIONS`, `REQUEST_TIMEOUT`, `REQUEST_DEADLINE`, `MAX_RECORDS`, and the photo limits (`PHOTO_DAYS`, `KEEP_PHOTOS`, `PHOTO_BYTES`, enforced by `prune_photos` on every upload and by the `housekeeping` thread). Password attempts go through `password_attempt`/`password_accepted`, counted before the hash. Identity is settled from headers before any body is read.
- `tests/test_server.py::People` pins the isolation, and `::Upgrade` the migration from the single-row database of 0.4.0 and earlier.

No dependencies to install: Python standard library and plain browser JavaScript.

## Deploy

1. Bump the version in `server.py` (`APP_VERSION`), `ha-app/config.yaml`, `ha-app/Dockerfile` and `package.json`. The page's scripts and styles are served under `v/<APP_VERSION>/` (the server writes the version into `index.html`, and the scripts import each other relatively), so nothing in `public/` is edited per release. This matters because Cloudflare gives browsers a four-hour cache time for `.js` and `.css` whatever the app says: in 0.6.0 a phone kept the previous `strip.js` next to the new `app.js`, the import failed, and the app sat on its loading screen.
2. `python3 pool-app/scripts/package_ha.py` builds `dist/pool_strip_reader`.
3. Mount `smb://192.168.0.5/addons`, copy `dist/pool_strip_reader` to it, then in Home Assistant: Settings → Apps → App store → ⋮ → Check for updates → install or update. Never copy anything into the app's `/data`.

## How the colour reading works

Every colour is measured relative to the white right next to it (the strip's plastic, or the label around a chart swatch) in linear light, which cancels the light source and exposure. The pad colour is then projected onto the path the chart swatches trace through Lab space, giving an interpolated value and a plausible range.

Two things were learned from the first photos and shaped the design:

- **Blown-out whites ruin readings.** A white strip or bottle on a dark surface is over-exposed by the phone, so the white reference is wrong and everything reads pale. The app asks for white paper under the strip and warns when the white is near clipping.
- **The glossy, curved bottle is a poor in-shot reference.** The same swatch measures very differently between photos because of reflected sky. So the chart is measured once, carefully, and stored, instead of being compared in every photo.
- **Torch-lit video renders pads far paler than a daylight photo** (the same water's stabiliser pad measured L 61 in shade and L 84 in the box, with equal whites). So the box is read against a chart measured in the box, never against the daylight chart.

## Where the advice comes from

Every threshold and claim in `chemistry.py` is tied to a source in its `SOURCES` list (CDC, Falk et al. 2019, the Model Aquatic Health Code, ANSI/APSP/ICC-11, NSW Health, Wojtowicz, Pickens, onBalance), and the app shows that list under Settings. The pool surface (`SURFACES`) changes wording and handling advice only, never an amount; the fibreglass cautions come from one maker's care guide and are worded as things to check, because makers differ. The rules that differ from the strip bottle's chart: chlorine's range follows stabiliser (`FC_RULE`), pH is left alone from 7.2 to 7.8, and alkalinity from 80 to 180. `tests/test_chemistry.py::PublishedGuidance` pins each rule, and the acid model is checked against Wojtowicz's worked example. Not yet verified against a published table: the soda ash dose, which is why it stays capped per application.

## Recalibrating the chart

`chart.json` is the built-in chart, measured from the bottle's label peeled off and photographed flat in open shade (lot 531804). Ordinary photos are read against it; the light box uses its own in-app calibration instead. To rebuild it for a new bottle, photograph the flat label square-on in shade and run:

```sh
sips -m "/System/Library/ColorSync/Profiles/sRGB Profile.icc" -s format bmp -Z 2000 IMG.HEIC --out label.bmp
node pool-app/scripts/chart_from_label.mjs label.bmp           # prints what it measured
node pool-app/scripts/chart_from_label.mjs label.bmp --write   # writes chart.json
```

On this label the pH 7.2 and 7.6 swatches are almost the same colour (about 3 Lab units apart), so no reading of these strips can separate those two values; the app reports the range.

`node pool-app/scripts/read_photo.mjs photo.bmp x1 y1 x2 y2` reads a strip from a bitmap given its two end-pad centres, for checking real strips against by-eye readings. Every test saved in the app keeps its photo under `/data/photos`, so real-world strips can be used to tune the chart later.
