# Strip light box: design notes

A 3D-printed box that the phone lies on, camera down, so every strip is photographed
under the phone's own torch at a fixed distance and position. Built by the Fusion 360 script
`build_strip_box/build_strip_box.py`, which writes the STLs to `export/`. Edit the constants
at the top of the script and re-run to change it (see "Editing in Fusion 360" below).

There are two lids, one per phone. **Both are made for the phone with its case on**: the case's raised
camera surround drops into the lid's hole, so the case never has to come off.

| Lid | Phone |
|---|---|
| `strip_box_lid_black.stl` | iPhone 17 Pro Max / 18 Pro Max, in its case (measured on a ZAGG Luxe Snap case) |
| `strip_box_lid_iphone14_black.stl` | iPhone 14, in a case with a raised ring round the camera |

The saved Fusion design is **"Pool Strip Reader Box - Multi Phone"** in Admin Project. Run the script
with that document active and it is rebuilt in place; save it to make a new version. With any other
document active, the script opens a new untitled design and leaves the saved one alone.

Only the lid depends on the phone. Each phone is one entry in `PHONES` at the top of the script and
gets its own lid; the body, tray, calibration slide and stick-on parts are shared.

## Inputs
| What | Value | Source |
|---|---|---|
| Phone in ZAGG Luxe Snap case | 82 x 167 x 13 mm | owner |
| Raised camera area of the case | 73 across x 45 along the phone; 2 mm proud at the flash, 3 mm at the lenses | owner |
| Bump to top edge of case | about 4 mm | photo (edges are below the ruler plane, so +/-1 mm) |
| Lens nearest the flash | 6.6 mm from bump centre towards the lens side, centred along the phone | photo `phone-back-with-ruler.HEIC`, ruler scale 21.2 px/mm |
| Other two lenses (the one nearer the top of the phone is taken to be the ultra-wide, see below) | 24 mm towards the lens side, +/-9.5 mm along the phone | photo |
| Flash | 24.5 mm towards the flash side, 9.7 mm towards the top of the phone | photo |
| Strip | 5 x 70 mm; four 5 mm pads from the tip, 3 mm apart (29 mm) | owner |
| Bottle | 36 mm body, 41 mm lid (46 with lip and hinge), 85 mm long | owner |
| 1x camera closest focus | about 20 cm; 0.5x focuses very close | owner |
| Printer | Bambu Lab A1, black and white filament | owner |
| iPhone 14, bare | 71.52 x 146.71 x 7.80 mm | Apple, "iPhone 14 Dimensional Drawings" (2022-09-21) |
| iPhone 14 camera plateau | 34.75 mm square, 1.15 mm in from the top and left edges (looking at the back); stands 1.85 mm out of the back, lens glass 3.42 mm | Apple drawing; corner radius about 11 mm, scaled off the drawing |
| iPhone 14 ultra-wide lens ("rear camera 1") | 11.88 mm from the left edge, 11.88 mm from the top | Apple drawing; its keep-out cone is 132.87 degrees, the other camera's is 81.99, so this is the ultra-wide |
| iPhone 14 other lens / flash | 24.63, 24.63 / 26.30 from the left, 10.21 from the top | Apple drawing |
| iPhone 14's case | has a raised protective ring round the camera plateau, "maybe 1-1.5 mm" | owner, second hand. **Not measured:** the ring's outside size, the case's size and thickness are all assumed, see below |

Strip thickness was not measured; the channel is 0.6 mm deep and the tray opening leaves 3 mm above the tray.

## Decisions
- **Ultra-wide lens at 75 mm** from lens to strip. The 1x lens cannot focus under 20 cm. Closer than
  ~6 cm the torch lights the strip too obliquely and the bottle would not fit for calibration.
- **Black walls, white tray.** Black walls plus one off-axis light is the standard way to keep
  mirror-like glare out of a colour measurement (it bounces away from the lens instead of veiling the
  colour). The tray is white so the camera's auto-exposure has a large white area to settle on, and it
  doubles as the white reference next to the strip. This reverses the earlier "white inside" advice.
- **Strip lies along the phone's length, on the lens side** (14 mm from the bump centre). All four pads
  are then the same distance from the flash, and the spot where the flash would mirror into the lens is
  14-23 mm away from the strip for any of the three lenses.
- **Drawer, not a slot.** The strip is laid into a channel on a tray and the tray is pushed in, so the
  wet pads never rub on anything. The tray's face plate stops against the wall (position datum) and
  covers the opening.
- **The tray clicks home.** The first tray slid in loose and fell out when the box was picked up. A slot
  beside each long edge of the tray now leaves a sprung arm (1.8 mm wide, 24 mm long), fixed at its
  inner end and free near the face plate. A bump at each tip stands 1.4 mm out past the tray's edge,
  above the body's guide rails. The sides of the opening squeeze the two bumps in over the last 6 mm of
  travel and they spring out behind the near wall as the face plate lands. **Only the tray is
  reprinted:** the bumps catch on the body as it is. The way in is a 30 degree ramp and the way out 45,
  so it holds better than it resists going in. At home the way-out ramp still presses 0.1 mm on the
  wall's inner corner, so the arms hold the face plate against the wall and the tray cannot rattle.
  Calculated, not measured: about 2.4 N to push in and 4.3 N to pull out (the tray weighs about 0.3 N),
  with the arms strained about 0.5 per cent. The first click tray (1.6 mm arms, 1.1 mm bumps, 0.15 mm
  of free play at home; calculated 1.2 N in, 2.2 N out) printed and worked but felt loose, so this one
  is about twice as firm. Change `CLICK_GRIP` for a firmer or softer click. The calibration slide has no click: a bump standing above
  its face would foul the slide holder.
- **Calibration uses a slide of its own.** The bottle's label is peeled off, trimmed to the colour chart
  (a 47 mm square: swatches 5 mm, 6.7 mm apart, rows 11.5 mm apart) and stuck flat on the calibration
  slide, a flat twin of the tray, so the chart sits at the height of the strip's pads. Four engraved
  corner ticks mark a 50 mm square that starts 24 mm from the slide's inner end: in the first real scan
  the torch's mirror patch sat 3 to 18 mm from that end, and a glossy label burns out under it. The two
  low ribs on the tray (meant to cradle the bottle) are no longer needed for calibration.
- **Body is 130 mm long** so the phone's centre of mass sits well inside the footprint and tapping the
  screen does not tip it.
- Bump hole is 73.6 x 45.6 mm (0.3 mm clearance per side). Corners: 9 mm at the two nearest the top of
  the phone, 5 mm at the other two.

## Where a phone sits (one lid per phone)
The body fixes where the strip and the calibration chart are, so a lid's job is to put the phone's
ultra-wide lens and torch in a good place over them. What matters most is the spot where the torch
mirrors into the lens: it is halfway between lens and torch, and the picture burns out for about 8 mm
around it. In the first real scan (17 Pro Max) that patch sat at about (0, -10) in the script's frame,
14 mm from the strip's centre line, which is where the top-left lens puts it; that is why that lens is
taken to be the ultra-wide.

- **iPhone 14 in its case.** The torch is 14.4 mm to the flash side of the ultra-wide lens and 1.7 mm
  nearer the top of the phone, so the phone lies the same way round as the 17 Pro Max and all four
  pads are about the same distance from the torch. The lid puts the lens at (0, -10): the mirror
  point is then at (7.2, -10.8), 21 mm from the strip's centre line (further than on the 17 Pro Max)
  and about 7 mm short of the calibration chart, much as before. The lens is 14 mm to one side of the
  strip, about 11 degrees off straight down. None of this depends on the case.
- **The hole was sized with printed test rings, not by measurement.** What drops into the lid is the case's ring
  round the camera plateau. The hole is 38.6 mm square with 13.5 mm corners, centred on the plateau:
  the plateau (34.75) plus a ring 1.5 mm wide on every side, a little play, and the usual 0.3 mm
  clearance. The ring itself was never measured; a test ring with this exact hole dropped in on the
  real phone (see "How the lid holes were sized"). How much play it has was not reported.
- **So the phone is always pushed into the same corner:** slide it towards its own top-left (looking
  at its back) until the ring stops against the hole. It then sits in the same place every time,
  whatever the play, and the app's remembered rings and the calibration are set from that position.
- If the ring does not go in at all, or the play is more than about 2 mm, measure the ring's outside
  (across and along the phone) and put it in `bump` for this phone, then re-run the script.
- Taking the case as about 75 x 150.5 mm, its right edge overhangs the lid by about 16 mm and its
  bottom end by about 29 mm (the 17 Pro Max overhangs by 42 mm). The centre of mass is about 21 mm
  inside the lid's edge.
- The lid is the same height as the first one. With a 2 mm case back the lens glass is about 1.4 mm
  below the lid's top, so about 76.6 mm above the strip.
- Along the phone's top and left edges the hole comes to within about 1 mm of the case's edge. If
  stray light shows there, black tape along those two edges closes it.

To add a phone: copy an entry in `PHONES`, fill in its measurements, and choose `at` so the mirror
point (printed by the script) is 20 mm or more from the strip's centre line and at y of -10 or less.

## How the lid holes were sized
Before printing each lid, a thin ring with just the lid's hole was printed and tried on the phone in its
case. Those rings are no longer kept; what they showed is built into the lids:
- 17 / 18 Pro Max: 5 mm corners all round let light in at the two corners nearest the top of the phone
  (the camera surround follows the phone's own corner there, about 10 mm). The hole now has 9 mm corners
  there and 5 mm at the other two.
- iPhone 14: 11 mm corners left a gap; 13.5 mm corners dropped in on the real phone in its case
  (17 November 2026). The hole is 38.6 mm square with 13.5 mm corners.

To check a new phone the same way, print just the lid, or temporarily cut a thin plate from the
lid's `hole_shape` in the script.

## Parts
| File | Colour | Print |
|---|---|---|
| `strip_box_body_black.stl` | black | upright as exported, no supports (one 60 mm bridge over the tray opening) |
| `strip_box_lid_black.stl` | black | iPhone 17 / 18 Pro Max in its case. Flipped, flat top on the bed, locating ring up, no supports |
| `strip_box_lid_iphone14_black.stl` | black | iPhone 14 in its case. Printed the same way. Ready to print (a copy exported before 17 November 2026 has 12.5 mm corners) |
| `strip_box_tray_white.stl` | white | flat as exported, no supports. Has the firmer click arms (v2); replaces the earlier trays, the body stays |
| `strip_box_calibration_slide_white.stl` | white | flat as exported, no supports |
| `strip_box_slide_holder.stl` | any | flat as exported (wall face down), no supports |
| `strip_box_bottle_clip.stl` | any | flat as exported (wall face down), no supports |

Solid volumes: body 103 cm3, lid 36 cm3 (iPhone 14 lid 44 cm3), tray 23 cm3, calibration slide 23 cm3, slide holder 14 cm3, bottle clip 10 cm3.

## Storage on the box
Two stick-on parts, so the printed body is kept. Fix them with superglue or thin double-sided tape.

- **Slide holder**, on the flash-side (+X) wall. The calibration slide goes in from the near end with the
  chart facing the box, until its face plate meets the end of the box. It rests on the lower rail and is
  held by its bare margins (0.8 mm shoulders), so the chart touches nothing. The lips are 45 degrees so
  the part prints without supports. A 32 mm length of the upper rail is cut free as a spring; its bump
  is pushed back about 1 mm by the slide's top edge (roughly 2 N), which is what stops the slide sliding
  out. **Keep glue off the back of that sprung length**, or it cannot move. Change `HOLD_GRIP` for a
  tighter or looser hold.
- **Bottle clip**, on the other side wall. The bottle snaps in sideways and lies along the box; the
  clip's mouth is 33.4 mm for the 36 mm bottle. The bottle's axis is 23.6 mm from the wall so the 46 mm
  cap clears it. If the bottle is loose or too tight, change `CLIP_R`.

Neither part has been printed or tried yet: the spring's grip and the clip's snap are calculated, not tested.

## App support
From app version 0.2.0: Settings -> Light box (live camera, torch, lens chooser, remembered rings, automatic
capture). From 0.6.0: Calibrate colours reads the whole chart from one picture of the calibration slide.
Calibration has been tested only against a simulated camera feed showing a shade photo of the label.

## Not yet verified
- Whether Safari lets the web app select the ultra-wide lens and switch the torch on with it.
- Whether the white tray is enough to stop the whites blowing out. If the app reports "too bright",
  add white paper to the floor beside the tray or to the walls; if too dark, the reverse.
- Which physical lens is the 17 Pro Max's ultra-wide (inferred from the torch's mirror patch, see above).

- The firmer tray click (v2) has not been printed. Its forces are calculated for a 0.3 mm gap each side
  of the tray; a body printed with a wider opening gives a softer click, a narrower one a firmer click.
  The first click tray felt looser than its numbers suggested, so the printed gap may be wider than 0.3.

For the iPhone 14 lid (a test ring has been tried on the phone; the lid itself has not been printed):
- Where the hole sits. The test ring showed the hole's size and corners suit the case's camera ring. It
  could not show that the ring is centred on the plateau, which is what puts the lens at (0, -10). If
  it is off, the app's rings and the calibration still follow the phone, as they are set from wherever
  it actually sits.
- Focus. The iPhone 14's ultra-wide lens is fixed-focus (the 17 Pro Max's focuses close). At about
  77 mm a rough calculation gives about half a millimetre of blur on a 5 mm pad, which should not
  matter for colour, but it has not been tried.
- Whether Safari on the iPhone 14 offers the ultra-wide lens with the torch.
- Colours. A different phone needs its own calibration with the slide, and calibrations are kept per
  person in the app, not per phone.

## Editing in Fusion 360
The STLs are meshes, fine for printing but awkward to edit. The script is the real source: it rebuilds
every part from the numbers at its top, so a change is made by editing a number and running it again.

**Load the script (once):**
1. Get the whole `enclosure/` folder onto the computer that runs Fusion (clone or download the repo).
   Keep `build_strip_box/` inside `enclosure/`: the script writes its STLs to `enclosure/export/`.
2. In Fusion, open the **Design** workspace and choose **Utilities > Add-Ins > Scripts and Add-Ins**
   (shortcut Shift+S).
3. On the **Scripts** tab click **+** (or the green plus next to "My Scripts") and choose
   **Script or add-in from device**, then pick the `enclosure/build_strip_box` folder. It contains
   `build_strip_box.py` and `build_strip_box.manifest`; Fusion expects the folder and the .py to share a
   name, so do not rename one without the other.
4. `build_strip_box` now appears in the list.

**Run it:** select it and click **Run**. It builds every part into a new untitled design (or rebuilds
the saved design "Pool Strip Reader Box - Multi Phone" if that is the open document), shows the chosen
phone sitting on its lid, overwrites the STLs in `enclosure/export/`, and prints sizes, volumes and the
torch mirror point for each phone in the **Text Commands** window (File > View > Show Text Commands).
Save the design if you want to keep it in Fusion; the STLs are already written either way.

**Edit it:** select it and click **Edit** to open the script in Fusion's code editor (VS Code), or open
the .py in any editor. After a change, run it again.
- Whole-box sizes (clearance, tray click, slide grip, bottle clip) are the named constants under the
  `PHONES` block, each with a comment saying what it does.
- `SHOW` picks which phone and lid appear on the box in Fusion. Every lid is exported whichever you pick.

**Change or add a phone:** only the lid changes, so the body, tray and the rest stay as printed.
1. Copy one of the entries in `PHONES` and give it a new `key`, `name` and `lid` file name.
2. Measure the phone **in its case**: `phone` (width, length, thickness), and the raised camera area that
   will drop into the hole: `bump` (across, along the phone), `bump_at` (its left and top edges from the
   case's left and top edges, looking at the back), and the hole's corner radii in `hole_r`. Apple's
   "Dimensional Drawings" PDFs give the bare phone and lens positions; the case adds to them.
3. Fill in `uw` (ultra-wide lens), `lenses` and `flash`, measured from the centre of the bump, and
   `proud` (how far the lens glass sits below the surface that rests on the lid).
4. Set `SHOW` to the new key, run the script and look at the printed torch mirror point. Adjust `at` until
   it is 20 mm or more from the strip's centre line and at y of -10 or less (see "Where a phone sits").
5. Print the new lid, try the phone in it, and calibrate colours in the app (Settings > Light box) with
   that phone, since each camera sees the chart differently.

The script needs Fusion's own Python (it imports `adsk`), so it cannot be run outside Fusion.
