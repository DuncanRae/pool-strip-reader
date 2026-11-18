# Changelog

## 0.9.1

- **Published for installing from GitHub.** Add `https://github.com/DuncanRae/pool-strip-reader` as an app store repository in Home Assistant, or run the same image with Docker alone (see the repository's README). Under Docker alone it needs a password and its address, and the Home Assistant sensors, reminders and weather are off.
- **First launch on an iPhone or iPad shows how to put the app on the Home Screen**, before the sign-in: tap Share (or the three dots beside the address bar, then Share), Add to Home Screen, Add, then open Pool from its icon and sign in there. It comes first because the app on the Home Screen keeps its own sign-in, so the pool details and the light-box calibration are then done in the app itself and not in Safari.
- It is asked once per browser, has **Carry on in the browser instead**, and is not shown when the app is already open from the Home Screen or inside the Home Assistant sidebar.
- **Settings → About → Put Pool on the Home Screen** shows the same steps later, for anyone still using it in Safari.

## 0.9.0

A new person is taken through calibrating their light box.

- **After someone gives their pool's volume and surface, the next screen is "Next: calibrate your light box".** It says why it matters and what is needed, then shows the six steps: cut the chart out of the bottle's label, stick it on the calibration slide, slide it into the box, start the camera and lay the phone on the box, wait until no ring is red, then check the colours and save. **Start calibration** opens the camera and switches the light box on for tests on that phone.
- **Not now, remind me** lets them carry on. A card, "Your light box is not calibrated yet", then stays at the top of the Today screen with **Show me how**, until a calibration is saved.
- **I don't have a light box** ends the reminders. Calibrating later under Settings → Light box takes that back.
- This goes by whether a calibration exists, not by when the person was added, so someone added earlier who has not calibrated is asked too. So are you, if you never calibrated. The full-screen step is asked once on each phone; the card is what remains.
- Inside the Home Assistant sidebar, where the live camera cannot work, only the card is shown, and it says to open the app from its own address.
- **Settings → Light box → Calibrate colours** now shows the same six steps.

## 0.8.1

- A pool shop test now says plainly that it is saved: a green line above **Delete this report** every time it is opened, and straight after adding one, the same at the top with a **Done** button.

## 0.8.0

Pool shop tests are kept in the history.

- **New test → Add a pool shop test.** Paste the link the shop sends (a WaterLink report, which is what Builders and other shops with a LaMotte meter use) and the app fetches the readings, the date and time of the test, anything the shop ticked about the water, and its advice.
- A printed report can be typed in instead, with a picture of the page kept beside it. The figures are not read from the picture.
- A shop test sits in the history and the charts with the strip tests, marked "pool shop" (a small box round its point on each chart). Opened, it also shows what else was measured (total and combined chlorine, calcium hardness, copper, iron, phosphate and so on), the shop's advice word for word, and a link back to the shop's report.
- **It goes in at the date and time on the report** (top right corner of the page), not at the moment you add it. A report added days later takes its place in the history and changes nothing newer: the latest test, its plan, the doses ticked off, the reminders and Home Assistant stay as they were. A printed report has no date filled in for you, so it cannot be saved as "now" by accident.
- If a shop test is the newest test, the Today screen and Home Assistant follow it. The plan is still the app's own, worked out from the shop's readings; the shop's products and amounts are shown for reference only.
- **No amounts are given from a test once chemicals have been added after its sample was taken.** This matters for a report entered late: the readings are shown, with a note to test again, and the badge reads "Test again". The same now applies to the test left as newest when a later one is deleted.
- **Calcium hardness from a shop test is put into Settings**, which is what the water balance needs. It does not replace the figure from a newer shop test, or one you typed in yourself: then the setting is left alone and the test offers "Use this instead".
- **Strips against the pool shop**, on the History screen and in each shop test: every reading of the nearest strip test beside the shop's figure, and how often they have agreed. A strip agrees when the shop's figure is within half a colour step of what the strip showed. A reading is left out when the comparison would not be fair: the two tests too far apart (12 hours for chlorine, a day for pH, two days for alkalinity and stabiliser), something that moves that reading added in between, or a poor colour match.
- The trends (chlorine used per day, pH drift) are worked out from strip tests only, so the difference between a strip and a shop's meter is never mistaken for the pool changing.
- The same report cannot be added twice. Pictures of printouts are not deleted after three months as strip photos are; the newest 40 are kept.

Security:

- The app only ever contacts one fixed WaterLink address for this, over a verified connection, and only with a report code made of hexadecimal digits. A pasted link that is anything else is refused without a request being made.
- Each person can fetch 12 reports in ten minutes.
- A report carries your name, address, phone number and email address. The app does not read or keep any of them. It does keep the link, which is in your backup too: anyone holding that link can open the shop's report.
- The shop's advice arrives as formatted text. It is reduced to plain words before it is stored and is always shown as text.

## 0.7.2

- New test, with the light box on: the button that starts the camera is now **Take reading** instead of "Open the light box", and the first step says that the camera and torch come on. The camera screen is headed "Reading the strip".

## 0.7.1

- When stabiliser reads in range but pH is outside 7.0 to 8.4, the plan now says the stabiliser figure is rough. The bottle's label only vouches for that pad inside that pH range; before, the caution appeared only when stabiliser read low or high.

## 0.7.0

Staying signed in, photo housekeeping, and a security review.

- **Fixed: after an update the app could sit on "Checking the water…" for up to four hours.** Cloudflare tells browsers to keep script files for four hours, so a phone could load the new app with a script left over from the previous version, and nothing started. Every script is now served under an address that contains the version, so an old copy can never be picked up.
- **A sign-in no longer expires.** Opening the app renews it. It ends when you sign out, when the password changes, or when access is switched off. Sessions from 0.6.0 carry over: nobody is asked to sign in again by this update.
- **Signing out now signs you out on every device**, so a cookie copied from a phone stops working too. Before, signing out only cleared the cookie in that browser.
- **Strip photos are deleted after three months.** Readings and history are kept for good. Each person is also limited to their 300 newest photos and 150 MB, and deleting a test deletes its photo. The calibration picture is kept.
- A person can hold up to 5000 tests and 5000 doses.

Security review (no way into Home Assistant was found; these close smaller weaknesses):

- Only a signed-in person can send anything larger than a sign-in. Only a test or a calibration may carry a photo-sized request; everything else is limited to 16 kB.
- Wrong passwords are counted per visitor (using the address Cloudflare reports), per username and in total, before the password is checked. Changing your own password is limited the same way.
- Each address serves at most 48 requests at once, with a limit on header size, a silence timeout and an overall deadline per request.
- The session cookie is locked to the app's exact host (`__Host-`), and the server now sends `Strict-Transport-Security`.
- Malformed cookies, paths and oversized headers are refused quietly, with one line in the log and no traceback. `HEAD` requests are refused. The Python version is no longer advertised.
- A sign-in with text that cannot be a password no longer reveals whether the username is the owner's.
- The standalone address refuses to start without a password of 12 characters or more, and without one it serves nobody.
- Reading the history stays fast however many tests and doses a person stores.

## 0.6.0

Light-box calibration reads the bottle's whole colour chart from one picture.

- **Calibrate colours** now expects the chart stuck flat on the new calibration slide (print `strip_box_calibration_slide_white.stl`). It finds the chart itself, any way up, and rings every swatch. A red ring marks a swatch the torch is reflecting off; pull the slide out a little until none is red. The one-column-at-a-time method is gone.
- If the chart is not found, three rings are dragged onto chlorine 0, chlorine 10 and stabiliser 0, and the rest follow. Rings that miss their swatches are reported, so a wrong calibration cannot be saved unnoticed.
- The app now keeps two charts. Strips read in the box are compared with the chart measured in the box; ordinary photos are compared with the built-in chart. Before, a box calibration replaced the chart for both, although torch-lit and daylight pictures show the same pad very differently.
- The built-in chart was measured again, from the label lying flat in open shade. The first one came from the curved, glossy bottle and was too dark throughout.
- The calibration picture is kept with the calibration, so a doubtful one can be looked at afterwards.

## 0.5.0

More than one person can use the app, each with a pool of their own.

- The standalone address now asks for a username and a password. Yours are `standalone_username` (new, `owner` unless you change it) and `standalone_password` in the app configuration. **You will be asked to sign in again after this update.**
- **Settings → People** (owner only): add a person, set a new password for them, switch their access off, or remove them and their data. There is no sign-up; only you create accounts.
- Each person has their own settings, tests, doses, photos and light-box calibration, and cannot see anyone else's. A new person is asked for their pool's volume and surface before the app gives any amount.
- Home Assistant sensors, reminders, weather and the water temperature sensor belong to your pool only. Nothing another person does reaches Home Assistant.
- The pool surface is now a setting: concrete or plaster, fibreglass, or vinyl liner. Amounts are the same; on fibreglass and vinyl the app no longer suggests scattering chlorine granules, stops talking about plaster, and for fibreglass points out where pool makers' own care guides differ (calcium-based chlorine and calcium hardness).
- Low, high and unclear readings are marked with an arrow or a shape as well as a colour (history, the trend charts and the review screen), so the app reads the same to someone who is colour-blind.
- People other than the owner can change their own password, and everyone signed in with a password can sign out.

## 0.4.0

The advice was audited against public-health guidance, the pool water-quality standard and published research. Sources are listed in the app under Settings.

- Chlorine's ideal range now follows stabiliser: at least 1 ppm with none, 2 ppm with stabiliser, and at least a twentieth of the stabiliser level (CDC; Falk et al. 2019).
- pH is left alone between 7.2 and 7.8 (CDC accepts 7.0 to 7.8). The claim that high pH makes chlorine weak is now only made for unstabilised water, where it is true.
- Alkalinity between 120 and 180 is inside the accepted range and no longer triggers acid. Low alkalinity is raised to 90, the ideal for hypochlorite chlorine.
- Chlorine above its range is only called a hazard at 10 ppm or more, the level at which pool codes close a pool.
- Acid instructions no longer allow pouring in one spot (it does not lower alkalinity better and harms plaster). Aeration is offered as the chemical-free way to raise pH.
- Acid and soda ash arithmetic now uses constants for real pool water and reproduces a published worked example to within 2%.
- Notes on calcium added by granular chlorine, on a zero chlorine reading possibly being a bleached pad, and on checking strips against a drop kit.

## 0.3.1

- The bottom bar stays at the bottom of the screen on short pages (it rode up on History in the installed iPhone app). Only the content scrolls now; the bar is outside it.
- The strip expiry month is an ordinary list, which lines up with the other fields.
- Removed the "On hand" amount for each chemical, and with it the low-stock reminders.

## 0.3.0

- A reading whose colour is a poor match to the chart is now shown as unclear, gets no advice, and holds back any dose that depends on it. Setting it by eye on the review screen makes it count again.
- Appearance setting: Auto, Light or Dark, kept per phone.

## 0.2.1

- Light box: the lens list shows back cameras only, and the default is the back ultra-wide (0.2.0 could pick the front ultra-wide).

## 0.2.0

- Light box mode: live camera with the torch on, lens chooser, remembered ring positions and automatic capture once a strip is seen and steady.
- Chart calibration from the bottle inside the light box, one column at a time; the measured chart replaces the built-in one and can be reset.
- Tests record whether they came from the light box, a photo or manual entry.

## 0.1.0

First release.

- Photograph an HTH Early Warning strip and read free chlorine, total alkalinity, pH and stabiliser.
- Ordered dosing plan for the configured pool volume and products.
- Home Assistant sensors, reminders and weather nudges.
- History, trends, water balance, strip and chemical stock tracking.
- Colour chart is provisional until recalibrated from photos taken on white paper.
