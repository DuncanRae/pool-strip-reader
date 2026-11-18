# Pool Strip Reader

Photograph a pool test strip and get exact amounts of each chemical for your pool. Built for HTH Early Warning 4-way strips (free chlorine, total alkalinity, pH, stabiliser).

## Taking a test

1. Open **Pool** in the Home Assistant sidebar (or the Home Screen app) and tap **Test**.
2. Dip the strip elbow-deep for 2 seconds, shake once.
3. Lay it pads-up on a sheet of **white paper** in open shade, and photograph it from directly above with nothing else on the paper.
4. Check the rings sit on the four pads (drag the end rings if not), then **Save reading**.

White paper is not decoration. A phone exposes for the whole scene, so a white strip on a dark table is blown out and its colours read too pale. On white paper the exposure settles and the reading is repeatable. The app warns when a photo is blown out.

Tap any colour on the review screen to override a pad by eye.

If a pad's colour does not sit on the chart, the app marks it **Unclear** and shows its best guess with a question mark. An unclear reading is never used for a dose, and neither is anything that depends on it (the acid amount depends on alkalinity, for example). Tapping the colour you see makes it count.

## Pool shop tests

A test done at a shop's counter goes in the history beside your strip tests: **Test → Add a pool shop test**.

- **With the link.** Shops that test with a LaMotte meter (Builders among them) send a link that starts `https://solutions.waterlinkconnect.com/treatment-reports/`. Paste it and tap **Fetch the report**. The app takes the readings, the date and time, and the shop's advice from the report.
- **From a printout.** Tap **Enter a printed report by hand**, type in the date and the figures, and photograph the page if you want it kept with the test. The app does not read figures from the picture.

A shop test goes into the history at the date and time printed on the report (top right corner), whenever you add it. Adding last week's report today changes nothing newer: the latest test, its plan and Home Assistant stay as they are.

The newest test by its own date drives the Today screen and the Home Assistant sensors, whether it is a strip or a shop test. The plan is always the app's own: the shop's products and amounts are kept for reference and never added to it. If chemicals were added after a test's sample was taken, no amounts are given from that test; it says so and asks for a fresh test.

Calcium hardness from a shop test is copied into **Settings → Water balance**, unless a newer shop test or a figure you typed in is already there. In that case the test offers **Use this instead**.

### Checking the strips

Each shop test is set beside the strip test nearest to it in time, and **History → Strips against the pool shop** keeps the score for each reading. For a fair check, dip a strip in the same water you take to the shop, before adding anything. A reading is only compared when the two tests are close enough for it (12 hours for chlorine, a day for pH, two days for alkalinity and stabiliser), nothing that moves it was added in between, and the strip's colour match was not poor. A strip counts as agreeing when the shop's figure is within half a colour step of what the strip showed, which is as fine as a strip can read.

Fetching a report needs the Home Assistant machine to reach `wls-api.waterlinkconnect.com` on the internet. Nothing else is contacted.

## Light box

A printed box the phone lies on, camera down, so every strip is photographed under the phone's torch from a fixed distance and position. Turn it on per phone under **Settings → Light box**. It needs the live camera, which only works from the app's own HTTPS address (the Home Screen app), not inside the Home Assistant sidebar.

1. **Camera check** shows the live picture and a lens chooser. Pick the ultra-wide lens (the only one that focuses that close) and confirm the torch comes on.
2. On the first capture, drag the rings onto the four pads once. They are remembered on that phone.
3. **Calibrate colours** measures the bottle's colour chart under the box's light, all of it in one picture. Trim the chart off the bottle's label, stick it flat on the calibration slide (inside the corner marks, any way up) and slide that in where the strip tray goes. The app finds the chart and puts a ring on every swatch; a red ring means the torch is reflecting there, so pull the slide out a little. Strips read in the box are then compared with this measured chart. Ordinary photos keep the built-in chart, because the two kinds of picture show colour differently.

After that a test is: tap **New test**, then **Take reading** (the camera and torch come on), lay the phone on the box, dip a strip, put it in the tray and shut it. The photo is taken automatically.

## Configuration

| Option | Purpose |
|---|---|
| `weather_entity` | A `weather.*` entity. Enables "hot spell" and "rain since last test" nudges. |
| `water_temp_entity` | A pool water temperature sensor, used for the water balance index. |
| `notify_service` | For example `notify.mobile_app_your_phone`. Reminders are pushed there once a day between 07:00 and 20:00. Without it, reminders appear as Home Assistant notifications only. |
| `standalone_enabled`, `standalone_url`, `standalone_username`, `standalone_password` | Serve the app on its own HTTPS port (8746) for a Home Screen PWA or a Cloudflare Tunnel. The username and password are the owner's sign-in there; the password must be at least 12 characters. |
| `certificate_mode` | `files` uses `certfile`/`keyfile` from `/ssl`; `cloudflare` requests a Let's Encrypt certificate by DNS-01 using `cloudflare_api_token` and `letsencrypt_email` (accept the subscriber agreement with `letsencrypt_terms_accepted`). |

To use the standalone address, enable port 8746 on the app's Network card and point your tunnel or DNS at it.

Pool volume, pool surface, product strengths, strip count and reminder interval are set inside the app under **Settings**.

## Other people

The app is installed for one owner: you. The Home Assistant sidebar is always your pool, and on the standalone address you sign in with `standalone_username` and `standalone_password`.

Under **Settings → People** you can add someone else. You choose their username and password (there is no sign-up), and the app shows both once so you can pass them on. They sign in at the standalone address, so standalone mode has to be on.

- Each person has a pool of their own: settings, tests, doses, photos and light-box calibration. Nobody can see another person's pool, including you theirs.
- On an iPhone or iPad, the very first screen shows how to put the app on the Home Screen, so that it opens full screen from its own icon. They do that, open **Pool** from the icon and sign in there. It can be skipped, and is under **Settings → About** later.
- The first time they sign in they are asked for their pool's volume and surface. No amount is given before that, because the defaults describe your pool.
- The next screen takes them through calibrating their light box, step by step (see **Light box**). They can put it off, in which case a reminder stays on their Today screen until it is done, or say they have no light box, which ends the reminders. Testing works either way; an uncalibrated box is read against the chart measured in daylight, which is less accurate.
- Home Assistant only ever shows your pool: the sensors, the reminders, the weather nudges and the water temperature sensor are not used for anyone else. Other people see "test due" reminders inside the app only.
- **Set password** on a person signs them out everywhere. **Switch access off** locks them out and keeps their data. **Remove** deletes their data for good.
- They can change their own password under **Settings → Account**.

A sign-in does not expire. It ends when the person signs out (which signs them out on every device), when their password changes (for you: `standalone_password`), or when you switch their access off. A browser forgets a cookie it has not used for about 13 months, so a phone that never opens the app for that long is asked again.

The built-in colour chart was measured from the bottle's label photographed flat in open shade, and is what ordinary photos are read against. The light box has a chart of its own once calibrated (each person's is separate).

## Pool surface

**Settings → Pool → Surface** is concrete or plaster, fibreglass, or vinyl liner. It never changes an amount. It changes the handling advice (chlorine granules must not be left lying on fibreglass or vinyl, which they bleach) and what the water balance card says, because the saturation index is about calcium and only plaster is made of it.

Fibreglass pool makers publish their own limits, and they do not all agree with the general pool standard or with each other. One maker's care guide asks for calcium hardness under 120 ppm and no calcium-based chlorine. The app does not change its ranges for that; it says where to check your own pool's care guide.

## Sensors

Published after every test and refreshed every five minutes:

- `sensor.pool_free_chlorine`, `sensor.pool_total_alkalinity`, `sensor.pool_ph`, `sensor.pool_stabiliser` with a `status` attribute (`low`, `ok`, `high`) and the likely range.
- `sensor.pool_water_status`: `Balanced` or `Needs attention`, with the dosing plan in the `actions` attribute.
- `sensor.pool_last_tested`, `sensor.pool_next_test_due` (timestamps).
- `sensor.pool_water_balance` (saturation index, once calcium hardness is entered).
- `sensor.pool_test_strips_remaining`, `sensor.pool_reminders`.

These are set through the API, so they have no unique ID and cannot be renamed in the UI.

## Where the advice comes from

Thresholds and dose rules are taken from public-health guidance (CDC), the pool water-quality standard (ANSI/APSP/ICC-11) and published research, and each source is listed in the app under **Settings → Where the advice comes from**. Two things differ from the chart on the strip bottle, on purpose:

- **Chlorine follows stabiliser.** Stabiliser holds most of the chlorine in reserve, so the minimum rises with it: 1 ppm with none, 2 ppm with stabiliser, and at least a twentieth of the stabiliser level. At 50 ppm of stabiliser the ideal is 2.5 to 4.5 ppm, where the bottle would call 4 "high".
- **pH and alkalinity use the accepted ranges.** pH is left alone from 7.2 to 7.8 and alkalinity from 80 to 180; the app does not add acid to chase a narrower band.

The app does not advise routine "shock" doses, because nothing in the guidance supports them for clear, sanitised water.

## Accuracy

A strip resolves about one colour step, and the app cannot do better than the strip. Doses are deliberately cautious and every plan ends with a retest. The stabiliser pad is only valid when pH is between 7.0 and 8.4.

Dose arithmetic assumes the product strengths entered in Settings. Check them against your labels. Never mix chemicals; add one at a time with the pump running.

## Security

The app is built to be safe on a public address, because the machine it runs on matters more than the pool data.

- Nothing sent before signing in is larger than a username and a password, and nothing a visitor sends ever becomes part of a Home Assistant request: the app only writes its own fixed `sensor.pool_*` entities.
- Wrong passwords are limited to 10 per visitor and 10 per username in five minutes, and 100 in all. The same limit covers guessing the current password from a signed-in session. Refusals and failed sign-ins are written to the app's log with the visitor's address.
- At most 48 requests are served at once on each address, each with a size limit, a silence timeout and an overall deadline, so a flood costs a fixed, small amount of memory.
- Each person is limited to 5000 tests, 300 photos and 150 MB, so nobody can fill the disk.
- Fetching a pool shop report is the one time the app calls out to the internet on a person's say-so. It contacts a single fixed WaterLink address over a verified connection, with nothing from the pasted link but the report's code, and each person can fetch 12 reports in ten minutes. What comes back is checked figure by figure and the shop's advice is stored as plain text.
- Sessions are signed with a key that never leaves `/data`. The cookie is locked to the app's exact HTTPS host and cannot be read by scripts or sent from another site.

Two things outside the app are worth doing. Keep port 8746 off your router's port forwarding, so the only way in from outside is the tunnel. For a second lock in front of the app, a Cloudflare Access policy on the hostname makes Cloudflare ask who someone is before any request reaches Home Assistant.

## Data

Everyone's readings, settings and strip photos live in the app's `/data` folder and are included in Home Assistant backups.

Strip photos are kept for three months and then deleted automatically; the readings taken from them stay for good. Each person is also limited to their 300 newest photos and 150 MB, and deleting a test deletes its photo. The light-box calibration picture is kept until the next calibration. Pictures of pool shop printouts are not aged out; each person's newest 40 are kept. A person can hold up to 5000 tests.

A pool shop report names its customer. The app keeps the readings, the date, the shop's name and its advice, and none of your name, address, phone number or email address. It does keep the report's link so that you can open the original; that link is in the backup too, and anyone who has it can open the shop's report.
 Passwords of the people you add are stored only as salted hashes. **Settings → Download a backup** exports the signed-in person's own readings as JSON.
