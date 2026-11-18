# Fusion script: builds the pool test-strip light box. Run it through the Fusion MCP (or
# Utilities > Scripts); it builds the printable parts plus reference bodies and exports STLs
# next to this file. It rebuilds the saved design if that is the active document, otherwise
# it opens a new untitled one (see SAVED_NAME). To change a dimension, edit the block below
# and run it again.
#
# Only the lid (and its fit test ring) depends on the phone: add a phone to PHONES to get
# another lid. The body, tray, calibration slide and stick-on parts are shared.
#
# Frame (mm): origin on the table under the centre of the first phone's camera bump.
#   +X towards the flash side, -X towards the lens side (looking at the phone's back, top up)
#   +Y towards the bottom of the phone (towards the person), +Z up.
import adsk.core, adsk.fusion, os

# --- Phones: one lid each --------------------------------------------------------------
# bump     raised camera area that drops into the lid's hole: across / along the phone
# bump_at  the bump's left and top edges, from the phone's left and top edges (looking at its back)
# hole_r   hole corner radius: the two corners nearest the top of the phone, then the other two
# ring     the fit test ring's file: it has the lid's own hole
# rings    in place of `ring`, several rings to try, each (corner radii, file); the lid still has hole_r
# uw, lenses, flash   centres, from the centre of the bump: x to the right looking at the back,
#          y towards the bottom of the phone
# proud    how far the lens glass stands out of the back that rests on the lid
# at       where the centre of the bump sits on the box (see "Where a phone sits" in design-notes.md)
PHONES = [
    dict(key='iphone17promax', name='iPhone 17 Pro Max in ZAGG Luxe Snap case',
         # Measured: photo phone-back-with-ruler.HEIC plus the owner's calipers.
         phone=(82.0, 167.0, 13.0), phone_r=10.0,
         bump=(73.0, 45.0), bump_at=(4.5, 4.0), bump_r=6.0,
         hole_r=(9.0, 5.0),            # the bump's own are ~10 and ~5-6; fit ring v1 was snug at 5
         # Which lens is the ultra-wide was never confirmed on the phone. The first real scan had the
         # torch's mirror patch at about (0, -10), which is where the top-left lens puts it, so that one.
         uw=(-24.1, -9.7), lenses=[(-6.6, -0.2), (-23.6, 9.2)], flash=(24.5, -9.7),
         proud=3.0, at=(0.0, 0.0),
         lid='strip_box_lid_black.stl', ring='strip_box_fit_test_ring_v2.stl'),
    dict(key='iphone14', name='iPhone 14 in a case with a camera ring',
         # The phone: Apple's "iPhone 14 Dimensional Drawings" (2022-09-21), detail D, measured from
         # the phone's top-left corner: 71.52 x 146.71 x 7.80; plateau 1.15 to 35.90 both ways (34.75
         # square, corners about 11 scaled off the drawing); rear camera 1 at (11.88, 11.88), which is
         # the ultra-wide (its keep-out cone is 132.87 degrees, camera 2's is 81.99); camera 2 at
         # (24.63, 24.63); flash at (26.30, 10.21). Plateau stands 1.85 out of the back, lens glass 3.42.
         # The case: NOT MEASURED. All that is known is that it has a raised ring round the plateau,
         # "maybe 1-1.5 mm". The bump here is that ring's outside, taken as the plateau plus 1.5 mm a
         # side and a little play, centred on the plateau: the largest it should be, so the ring is
         # sure to go in. The case's outside size and the lens height are estimates and only move the
         # reference bodies. Replace them with measurements when there are any.
         phone=(75.0, 150.5, 10.5), phone_r=14.0,
         bump=(38.0, 38.0), bump_at=(1.3, 1.3), bump_r=13.5,   # corners as the fit ring found them (reference body only)
         # Fit ring v1 (11 mm corners) was tried on an iPhone 15 in a case: the size was right but the
         # corners showed a gap. A ring that follows the plateau has corners of about 12.5-13, so two
         # rings were printed, 12.5 (one notch) and 13.5 (two notches), to use the tightest that drops in
         # on the real phone. The two-notch ring dropped in on the iPhone 14 in its case (reported
         # 2026-11-17), so the lid's hole has 13.5 mm corners. Both rings are kept as they were printed.
         hole_r=(13.5, 13.5),
         rings=[((12.5, 12.5), 'strip_box_fit_test_ring_iphone14_v2_12.5mm_one_notch.stl'),
                ((13.5, 13.5), 'strip_box_fit_test_ring_iphone14_v2_13.5mm_two_notches.stl')],
         uw=(-6.645, -6.645), lenses=[(6.105, 6.105)], flash=(7.775, -8.315),
         proud=1.4, at=(6.65, -3.35),  # puts the ultra-wide lens at (0, -10)
         lid='strip_box_lid_iphone14_black.stl'),
]
SHOW = 'iphone14'    # which phone and lid are shown on the box (every lid is exported either way)
# --- Strip and bottle ----------------------------------------------------------------
STRIP_W, STRIP_L, PAD, PAD_GAP = 5.0, 70.0, 5.0, 3.0   # four 5 mm pads at the tip, 3 mm apart
BOTTLE_D, BOTTLE_L = 36.0, 85.0
# --- Design choices ------------------------------------------------------------------
FIT = 0.3            # clearance per side around the camera bump
WALL, FLOOR, LID_T = 2.4, 2.0, 4.0
TRAY_T = 3.0
LENS_TO_STRIP = 75.0 # lens glass to the strip, for the first phone: this set the height of the body
LENS_PROUD = 3.0     # that phone's lenses stand this far out of its back
X0, X1 = -45.0, 45.0 # body outside
Y0, Y1 = -32.0, 98.0 # long enough that the phone's centre of mass sits over the box
STRIP_X = -14.0      # strip centre line: lens side, away from the flash's mirror point
TRAY_X0, TRAY_X1 = -40.0, 20.0
SLIDE = 0.3          # clearance each side of the tray
OPEN_H = 6.0         # tray opening height (tray + pads + cradle ribs + bridge sag)
CLICK_GRIP = 1.4     # tray click: how far each bump stands out past the tray's edge (more = firmer click).
                     # 1.1 with a 1.6 arm printed and worked but was loose; this pair is about twice as firm
CLICK_ARM_T = 1.8    # width of the sprung arm that carries it (stiffness goes with the cube of this)
CLICK_ARM_L = 24.0   # the arm's length, from its root to the middle of the bump
CHART_SIDE = 50.0    # square marked on the calibration slide for the bottle's chart (the chart box is 47 mm)
CHART_FROM_END = 24.0  # its nearest edge, from the slide's inner end
# --- Storage on the outside of the box (two stick-on parts) ---------------------------
HOLD_BASE = 1.2      # slide holder: plate glued or taped to the +X side wall
HOLD_Z = 7.7         # where the stored slide's lower edge rests, above the table
HOLD_STANDOFF = 0.8  # shoulders keep the chart's face this far off the holder
HOLD_GRIP = 1.0      # how far the spring's bump is pushed back by the slide's edge
CLIP_BASE = 1.6      # bottle clip: plate glued or taped to the -X side wall
CLIP_R = 18.1        # bottle body radius plus a little (body is 36 mm across, label off)
CLIP_WALL = 1.6      # thickness of the clip's sprung arms
CLIP_AXIS = 23.6     # bottle axis from the wall: the 46 mm cap must clear the wall
CLIP_LEN = 30.0
CLIP_Y, CLIP_Z = 33.0, 40.0   # where the clip is shown on the wall (stick it wherever suits)

STRIP_Z = FLOOR + TRAY_T                       # tray top = strip plane
LID_Z0 = STRIP_Z + LENS_TO_STRIP - (LID_T - LENS_PROUD)   # lid underside = wall top
LID_Z1 = LID_Z0 + LID_T
PAD_ZONE = 4 * PAD + 3 * PAD_GAP               # 29 mm
TIP_Y = -PAD_ZONE / 2                          # pads centred under the lenses
EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'export')


# The saved design is "Pool Strip Reader Box - Multi Phone" (Admin Project). When that document is
# the active one, a run rebuilds it in place (save it afterwards to make a new version); with any
# other document active, a run opens a new untitled design and leaves the saved one alone.
SAVED_NAME = 'Pool Strip Reader Box - Multi Phone'


def run(_context: str):
    app = adsk.core.Application.get()
    doc = app.activeDocument
    if doc and doc.isSaved and doc.name.startswith(SAVED_NAME):
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent
        for occ in list(root.occurrences): occ.deleteMe()   # everything in it came from this script
        for b in list(root.bRepBodies): b.deleteMe()
    else:
        app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        design = adsk.fusion.Design.cast(app.activeProduct)
    design.designType = adsk.fusion.DesignTypes.DirectDesignType
    root = design.rootComponent
    tmp = adsk.fusion.TemporaryBRepManager.get()
    P, V = adsk.core.Point3D.create, adsk.core.Vector3D.create
    BT = adsk.fusion.BooleanTypes

    def box(x0, x1, y0, y1, z0, z1):
        centre = P((x0 + x1) / 20, (y0 + y1) / 20, (z0 + z1) / 20)
        return tmp.createBox(adsk.core.OrientedBoundingBox3D.create(centre, V(1, 0, 0), V(0, 1, 0), (x1 - x0) / 10, (y1 - y0) / 10, (z1 - z0) / 10))

    def cyl(x, y, z0, z1, r):
        return tmp.createCylinderOrCone(P(x / 10, y / 10, z0 / 10), r / 10, P(x / 10, y / 10, z1 / 10), r / 10)

    def add(a, b):
        tmp.booleanOperation(a, b, BT.UnionBooleanType); return a

    def cut(a, b):
        tmp.booleanOperation(a, b, BT.DifferenceBooleanType); return a

    def rbox(x0, x1, y0, y1, z0, z1, r):
        """Box with rounded vertical edges."""
        b = add(box(x0 + r, x1 - r, y0, y1, z0, z1), box(x0, x1, y0 + r, y1 - r, z0, z1))
        for cx in (x0 + r, x1 - r):
            for cy in (y0 + r, y1 - r): add(b, cyl(cx, cy, z0, z1, r))
        return b

    def hole_shape(p, cx, cy, z0, z1):
        """The camera-bump hole for phone `p`, centred on (cx, cy); its phone-top (-Y) corners have their own radius."""
        hx, hy = p['bump'][0] / 2 + FIT, p['bump'][1] / 2 + FIT
        a, b = p['hole_r']
        h = box(cx - hx, cx + hx, cy - hy + a, cy + hy - b, z0, z1)
        add(h, box(cx - hx + a, cx + hx - a, cy - hy, cy - hy + a, z0, z1))
        add(h, box(cx - hx + b, cx + hx - b, cy + hy - b, cy + hy, z0, z1))
        for sx in (-1, 1):
            add(h, cyl(cx + sx * (hx - a), cy - hy + a, z0, z1, a))
            add(h, cyl(cx + sx * (hx - b), cy + hy - b, z0, z1, b))
        return h

    def cyl_y(x, z, y0, y1, r):
        """Cylinder lying along Y."""
        return tmp.createCylinderOrCone(P(x / 10, y0 / 10, z / 10), r / 10, P(x / 10, y1 / 10, z / 10), r / 10)

    def shave(body, point, normal):
        """Remove everything on the `normal` side of the plane through `point` (mm)."""
        n = V(*normal); n.normalize()
        side = V(0, 1, 0) if abs(n.y) < 0.9 else V(1, 0, 0)
        w = n.crossProduct(side); w.normalize()
        centre = P(point[0] / 10 + n.x * 10, point[1] / 10 + n.y * 10, point[2] / 10 + n.z * 10)
        return cut(body, tmp.createBox(adsk.core.OrientedBoundingBox3D.create(centre, n, w, 20, 60, 60)))

    def laid_flat(body, wall_x, quarter):
        """A copy turned so the face that goes against the box wall lies on the print bed (z = 0)."""
        flat = tmp.copy(body)
        turn = adsk.core.Matrix3D.create()
        turn.setToRotation(quarter * 3.141592653589793 / 2, V(0, 1, 0), P(0, 0, 0))
        move = adsk.core.Matrix3D.create(); move.translation = V(0, 0, -abs(wall_x) / 10)
        turn.transformBy(move)
        tmp.transform(flat, turn)
        return flat

    def part(name, body, visible=True):
        occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
        occ.component.name = name
        made = occ.component.bRepBodies.add(body)
        made.name = name
        occ.isLightBulbOn = visible
        return occ, made

    ix0, ix1, iy0, iy1 = X0 + WALL, X1 - WALL, Y0 + WALL, Y1 - WALL   # cavity

    # ---- Body (black): floor, four walls, tray guides, tray opening in the near wall ----
    body = cut(rbox(X0, X1, Y0, Y1, 0, LID_Z0, 3), box(ix0, ix1, iy0, iy1, FLOOR, LID_Z0 + 1))
    rail_top = FLOOR + 2.5
    tray_end = TIP_Y - 6                                   # inner end of the tray
    add(body, box(ix0, TRAY_X0 - SLIDE, tray_end - 0.5, iy1, FLOOR - 0.1, rail_top))
    add(body, box(TRAY_X1 + SLIDE, TRAY_X1 + SLIDE + 1.9, tray_end - 0.5, iy1, FLOOR - 0.1, rail_top))
    add(body, box(ix0, TRAY_X1 + SLIDE + 1.9, tray_end - 2.4, tray_end - 0.5, FLOOR - 0.1, rail_top))
    cut(body, box(TRAY_X0 - SLIDE, TRAY_X1 + SLIDE, iy1 - 0.1, Y1 + 0.1, FLOOR, FLOOR + OPEN_H))

    # ---- Lids (black), one per phone: plate the phone lies on, hole for its camera bump, locating ring ----
    # ---- Fit test rings: print one first to check the bump hole against the phone ----
    lids, rings = {}, {}
    ring_y, ring_x = Y0 - 45, None
    for p in PHONES:
        ring = cut(box(ix0 + 0.25, ix1 - 0.25, iy0 + 0.25, iy1 - 0.25, LID_Z0 - 4, LID_Z0 + 0.1),
                   box(ix0 + 1.85, ix1 - 1.85, iy0 + 1.85, iy1 - 1.85, LID_Z0 - 5, LID_Z0 + 1))
        lid = add(rbox(X0, X1, Y0, Y1, LID_Z0, LID_Z1, 3), ring)
        lids[p['key']] = cut(lid, hole_shape(p, p['at'][0], p['at'][1], LID_Z0 - 6, LID_Z1 + 1))
        hx, hy = p['bump'][0] / 2 + FIT, p['bump'][1] / 2 + FIT
        rings[p['key']] = []
        # A phone has one ring with the lid's own hole (ring), or several to try (rings), each with its
        # own corner radii; those are told apart by their notches: one on the first, two on the next...
        for n, (hole_r, file) in enumerate(p.get('rings') or [(p['hole_r'], p['ring'])]):
            ring_x = 0.0 if ring_x is None else ring_x + hx + 10    # rings side by side in front of the box
            test = cut(rbox(ring_x - hx - 4, ring_x + hx + 4, ring_y - hy - 4, ring_y + hy + 4, 0, LID_T, 3),
                       hole_shape(dict(p, hole_r=hole_r), ring_x, ring_y, -1, LID_T + 1))
            # The notches are on the edge that goes towards the top of the phone.
            for k in range(n + 1):
                nx = ring_x + (k - n / 2) * 8
                cut(test, box(nx - 2, nx + 2, ring_y - hy - 4.1, ring_y - hy - 2.6, -1, LID_T + 1))
            label = f'Fit test ring: {p["name"]}' if n == 0 else \
                f'Fit test ring, {hole_r[0]:g} mm corners ({n + 1} notches): {p["name"]}'
            rings[p['key']].append((label, test, file))
            ring_x += hx + 4

    # ---- Tray (white): slides in from the near side; the strip lies in its channel ----
    tray = rbox(TRAY_X0, TRAY_X1, tray_end, Y1, FLOOR, STRIP_Z, 1.5)
    add(tray, box(TRAY_X0, TRAY_X1, Y1 - 3, Y1, FLOOR, STRIP_Z))                       # square outer end
    add(tray, box(TRAY_X0 - 3, TRAY_X1 + 3, Y1, Y1 + 2.4, FLOOR, FLOOR + OPEN_H + 3))  # face plate, covers the opening
    add(tray, rbox(STRIP_X - 12, STRIP_X + 12, Y1 + 2.3, Y1 + 14, FLOOR, FLOOR + 2.4, 3))  # pull tab
    cw = STRIP_W / 2 + 0.3
    handle_y = TIP_Y + STRIP_L
    cut(tray, box(STRIP_X - cw, STRIP_X + cw, TIP_Y - 0.2, handle_y + 2.5, STRIP_Z - 0.6, STRIP_Z + 1))   # strip channel, tip stop at the far end
    cut(tray, box(STRIP_X - 7, STRIP_X + 7, handle_y - 9.5, handle_y + 6.5, STRIP_Z - 1.6, STRIP_Z + 1))  # finger scoop under the handle end
    for side in (-1, 1):   # low ribs: guide the strip in, and cradle the bottle for calibration
        add(tray, box(STRIP_X + side * 6.5 - 0.6, STRIP_X + side * 6.5 + 0.6, TIP_Y - 3.5, handle_y - 11.5, STRIP_Z - 0.1, STRIP_Z + 1.5))
    # The click. A slot beside each long edge leaves a sprung arm, fixed at its inner end and free near the
    # face plate. At its tip a bump stands up above the body's guide rails and out past the tray's edge. As
    # the tray goes home the sides of the opening squeeze the two bumps in; they spring out again behind the
    # near wall as the face plate lands. Only the tray changes: the bumps catch on the body as it
    # is. The way in is a 30 degree ramp, the way out 45, so it holds better than it resists going in.
    out_foot = iy1 + SLIDE + 0.1                  # at home the way-out ramp still presses 0.1 on the wall's inner corner,
                                                  # so the arms hold the face plate against the wall: no rattle
    peak1 = out_foot - CLICK_GRIP                 # 45 degrees
    peak0 = peak1 - 1.0
    in_foot = peak0 - CLICK_GRIP / 0.5774         # 30 degrees
    arm_tip = out_foot + 0.15
    arm_root = (peak0 + peak1) / 2 - CLICK_ARM_L
    bump_z0 = rail_top + 0.4 - SLIDE              # the bump's 45 degree underside clears the rail's top corner by 0.4
    bump_z1 = FLOOR + OPEN_H - 0.7                # and its top passes under the opening's lintel
    for out, edge in ((-1, TRAY_X0), (1, TRAY_X1)):   # out: which way is outwards for this edge
        arm_in = edge - out * CLICK_ARM_T
        slot_in = arm_in - out * (CLICK_GRIP + 0.4)   # room for the arm to be pushed fully in
        cut(tray, box(min(arm_in, slot_in), max(arm_in, slot_in), arm_root, arm_tip + 0.8, FLOOR - 1, STRIP_Z + 1))
        cut(tray, cyl((arm_in + slot_in) / 2, arm_root, FLOOR - 1, STRIP_Z + 1, abs(arm_in - slot_in) / 2))  # round root
        cut(tray, box(min(edge + out, slot_in), max(edge + out, slot_in), arm_tip, arm_tip + 0.8, FLOOR - 1, STRIP_Z + 1))
        bump = box(min(arm_in, edge + out * CLICK_GRIP), max(arm_in, edge + out * CLICK_GRIP), in_foot, out_foot, bump_z0, bump_z1)
        shave(bump, (edge, 0, bump_z0), (out, 0, -1))
        shave(bump, (edge, in_foot, 0), (out, -0.5774, 0))
        shave(bump, (edge, out_foot, 0), (out, 1, 0))
        add(tray, bump)

    # ---- Calibration slide (white): a flat twin of the tray that carries the bottle's colour chart ----
    # Same outline, so it goes in the same opening and puts the chart at the height of the strip's pads.
    # Shown beside the box. Four engraved corner ticks mark a 50 mm square for the trimmed chart: it starts
    # CHART_FROM_END from the inner end, clear of the torch's mirror patch (seen 3-18 mm from that end).
    dx = X1 + 15 - TRAY_X0
    slide = rbox(TRAY_X0 + dx, TRAY_X1 + dx, tray_end, Y1, FLOOR, STRIP_Z, 1.5)
    add(slide, box(TRAY_X0 + dx, TRAY_X1 + dx, Y1 - 3, Y1, FLOOR, STRIP_Z))
    add(slide, box(TRAY_X0 + dx - 3, TRAY_X1 + dx + 3, Y1, Y1 + 2.4, FLOOR, FLOOR + OPEN_H + 3))
    tab_x = (TRAY_X0 + TRAY_X1) / 2 + dx
    add(slide, rbox(tab_x - 12, tab_x + 12, Y1 + 2.3, Y1 + 14, FLOOR, FLOOR + 2.4, 3))
    cx0, cx1 = tab_x - CHART_SIDE / 2, tab_x + CHART_SIDE / 2
    cy0 = tray_end + CHART_FROM_END
    cy1 = cy0 + CHART_SIDE
    for tx, sx in ((cx0, 1), (cx1, -1)):
        for ty, sy in ((cy0, 1), (cy1, -1)):
            cut(slide, box(min(tx, tx + sx * 5), max(tx, tx + sx * 5), ty - 0.3, ty + 0.3, STRIP_Z - 0.3, STRIP_Z + 1))
            cut(slide, box(tx - 0.3, tx + 0.3, min(ty, ty + sy * 5), max(ty, ty + sy * 5), STRIP_Z - 0.3, STRIP_Z + 1))

    # ---- Slide holder: sticks to the +X side wall and keeps the calibration slide with the box ----
    # The slide goes in from the near (+Y) end, chart facing the box, until its face plate meets the end
    # of the box. It rests on the lower rail; shoulders hold it by its bare margins so the chart touches
    # nothing; 45-degree lips keep it in (and print without supports, back face down). A sprung length of
    # the upper rail carries a bump that presses on the slide's top edge, which is what stops it sliding out.
    hx0, hx1 = X1, X1 + HOLD_BASE                     # base
    face = hx1 + HOLD_STANDOFF                         # the stored slide's chart face
    outer = face + TRAY_T                              # its other face
    lip0, lip1 = outer + 0.3, outer + 2.3
    plate_w = TRAY_X1 - TRAY_X0
    zl, zu = HOLD_Z, HOLD_Z + plate_w + 0.5            # rail faces below and above the slide
    ra, rb = Y1 - (Y1 - tray_end) - 1.5, Y1 - 2        # rails run the slide's length
    holder = box(hx0, hx1, ra - 1, Y1, zl - 3.7, zu + 3.0)
    add(holder, box(hx0, lip1, ra, rb, zl - 2.2, zl))                       # lower rail
    add(holder, box(hx1 - 0.1, face, ra, rb, zl - 0.1, zl + 2.8))           # lower shoulder
    add(holder, shave(box(lip0, lip1, ra, rb, zl - 0.1, zl + 2.0), (lip0, 0, zl), (-1, 0, 1)))
    add(holder, box(hx0, lip1, ra, rb, zu, zu + 2.2))                       # upper rail
    add(holder, box(hx1 - 0.1, face, ra, rb, zu - 2.8, zu + 0.1))           # upper shoulder
    add(holder, shave(box(lip0, lip1, ra, rb, zu - 2.0, zu + 0.1), (lip0, 0, zu), (-1, 0, -1)))
    s0, s1 = 24.0, 56.0                                # the sprung length: fixed at s1, free at s0
    cut(holder, box(hx0 - 1, lip1 + 1, s0 - 0.7, s0, zu - 4.5, zu + 4))      # frees its end
    cut(holder, box(hx0 - 1, face + 0.2, s0 - 0.7, s1, zu - 2.2, zu))        # frees it from the base and shoulder
    cut(holder, box(hx0 - 1, hx1 + 0.1, s0 - 0.7, s1, zu + 2.2, zu + 4))     # room to bend
    bump = box(hx0, outer + 0.2, s0, s0 + 13, zu - 0.5 - HOLD_GRIP, zu + 0.1)
    peak = zu - 0.5 - HOLD_GRIP
    shave(bump, (0, s0 + 5, peak), (0, 1.5, -8))       # long ramp the slide's leading edge rides up
    shave(bump, (0, s0 + 2, peak), (0, -1.5, -2))
    add(holder, bump)

    # ---- Bottle clip: sticks to the -X side wall; the bottle snaps in sideways and lies along the box ----
    wall = X0
    ax = wall - CLIP_AXIS
    ro = CLIP_R + CLIP_WALL
    c0, c1 = CLIP_Y - CLIP_LEN / 2, CLIP_Y + CLIP_LEN / 2
    reach = ro * 0.7071                                # the clip's outside is 45 degrees steep from here up, so it prints
    clip = box(wall - CLIP_BASE, wall, c0 - 5, c1 + 5, CLIP_Z - 22, CLIP_Z + 22)
    add(clip, box(ax + reach, wall - CLIP_BASE + 0.1, c0, c1, CLIP_Z - reach, CLIP_Z + reach))
    add(clip, cyl_y(ax, CLIP_Z, c0, c1, ro))
    cut(clip, cyl_y(ax, CLIP_Z, c0 - 1, c1 + 1, CLIP_R))
    cut(clip, box(ax - ro - 1, ax - 7.0, c0 - 1, c1 + 1, CLIP_Z - ro - 1, CLIP_Z + ro + 1))   # the mouth: 33.4 mm for a 36 mm bottle

    files = {'Body (black)': 'strip_box_body_black.stl', 'Strip tray (white)': 'strip_box_tray_white.stl',
             'Calibration slide (white)': 'strip_box_calibration_slide_white.stl',
             'Slide holder': 'strip_box_slide_holder.stl', 'Bottle clip': 'strip_box_bottle_clip.stl'}
    parts = [part('Body (black)', body), part('Strip tray (white)', tray),
             part('Calibration slide (white)', slide), part('Slide holder', holder), part('Bottle clip', clip)]
    for p in PHONES:
        for label, made, file in [(f'Lid (black): {p["name"]}', lids[p['key']], p['lid'])] + rings[p['key']]:
            parts.append(part(label, made, visible=p['key'] == SHOW))
            files[label] = file
    # The two stick-on parts are exported lying on the face that goes against the wall, ready to print.
    flat = {'Slide holder': part('PRINT slide holder', laid_flat(holder, X1, -1), visible=False)[1],
            'Bottle clip': part('PRINT bottle clip', laid_flat(clip, X0, 1), visible=False)[1]}

    # ---- Reference bodies (not printed): phones, lenses, flash, strip, bottle ----
    for p in PHONES:
        (w, l, t), (bw, bl), (ax_, ay_) = p['phone'], p['bump'], p['at']
        px0, py0 = ax_ - bw / 2 - p['bump_at'][0], ay_ - bl / 2 - p['bump_at'][1]
        lens_z = LID_Z1 - p['proud']
        phone = rbox(px0, px0 + w, py0, py0 + l, LID_Z1, LID_Z1 + t, p['phone_r'])
        add(phone, rbox(ax_ - bw / 2, ax_ + bw / 2, ay_ - bl / 2, ay_ + bl / 2, lens_z + 1.0, LID_Z1 + 0.1, p['bump_r']))
        part(f'REF phone: {p["name"]}', phone, visible=p['key'] == SHOW)
        optics = cyl(ax_ + p['uw'][0], ay_ + p['uw'][1], lens_z - 0.6, lens_z + 1.1, 7.5)
        for x, y in p['lenses']: add(optics, cyl(ax_ + x, ay_ + y, lens_z - 0.6, lens_z + 1.1, 7.5))
        add(optics, cyl(ax_ + p['flash'][0], ay_ + p['flash'][1], lens_z - 0.6, lens_z + 1.1, 3.2))
        part(f'REF lenses and flash: {p["name"]}', optics, visible=p['key'] == SHOW)
    strip = box(STRIP_X - STRIP_W / 2, STRIP_X + STRIP_W / 2, TIP_Y, handle_y, STRIP_Z - 0.6, STRIP_Z - 0.2)
    for i in range(4):
        y = TIP_Y + i * (PAD + PAD_GAP)
        add(strip, box(STRIP_X - STRIP_W / 2, STRIP_X + STRIP_W / 2, y, y + PAD, STRIP_Z - 0.25, STRIP_Z + 0.5))
    part('REF strip', strip)
    bottle = tmp.createCylinderOrCone(P(STRIP_X / 10, (TIP_Y - 3) / 10, (STRIP_Z + 0.3 + BOTTLE_D / 2) / 10), BOTTLE_D / 20,
                                      P(STRIP_X / 10, (TIP_Y - 3 + BOTTLE_L) / 10, (STRIP_Z + 0.3 + BOTTLE_D / 2) / 10), BOTTLE_D / 20)
    part('REF bottle (calibration only)', bottle, visible=False)
    stored = box(face, outer, tray_end, Y1, zl, zl + plate_w)
    add(stored, box(outer - (OPEN_H + 3), outer, Y1, Y1 + 2.4, zl - 3, zl + plate_w + 3))
    part('REF slide in its holder', stored)
    part('REF bottle in its clip', add(cyl_y(ax, CLIP_Z, CLIP_Y - 45, CLIP_Y + 18, 18), cyl_y(ax, CLIP_Z, CLIP_Y + 18, CLIP_Y + 40, 20.5)))

    # ---- Export and report ----
    os.makedirs(EXPORT_DIR, exist_ok=True)
    em = design.exportManager
    for occ, made in parts:
        options = em.createSTLExportOptions(flat.get(made.name, made), os.path.join(EXPORT_DIR, files[made.name]))
        options.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
        em.execute(options)
        bb = made.boundingBox
        size = [round((bb.maxPoint.asArray()[i] - bb.minPoint.asArray()[i]) * 10, 2) for i in range(3)]
        print(f'{made.name}: {size[0]} x {size[1]} x {size[2]} mm, {made.volume:.1f} cm3, solid={made.isSolid}, lumps={made.lumps.count}')
    print(f'lid underside z={LID_Z0}, lid top z={LID_Z1}, strip plane z={STRIP_Z}, pad zone y={TIP_Y}..{TIP_Y + PAD_ZONE}')
    for p in PHONES:
        lens = (p['at'][0] + p['uw'][0], p['at'][1] + p['uw'][1])
        flash = (p['at'][0] + p['flash'][0], p['at'][1] + p['flash'][1])
        mirror = ((lens[0] + flash[0]) / 2, (lens[1] + flash[1]) / 2)   # where the torch would mirror into the lens
        print(f'{p["name"]}: ultra-wide at ({lens[0]:.1f}, {lens[1]:.1f}), {LID_Z1 - p["proud"] - STRIP_Z:.1f} mm above the strip; '
              f'torch mirror point ({mirror[0]:.1f}, {mirror[1]:.1f}), {mirror[0] - STRIP_X:.1f} mm from the strip line')
    print('exported to', EXPORT_DIR)
    app.activeViewport.fit()
