/* ==========================================================================
   Particle clouds

   One module, mounted more than once. It builds a field of points and can show
   any form in the registry (see FORMS): three are in the header's default cycle
   (horizon, wave, torus), one is the constellation the skill panel shows, and
   the rest are built and available but unused — kept because the tuning is the
   expensive part, and because a fork picks its own cycle in profile.json. Loaded
   on demand by app.js, which is where every reason not to render it is checked;
   this module assumes it was called because it should run.

   Two mounts exist, and the split is the point. The three forms used to cycle
   in one instance in the header, which had the constellation, the only form
   that means anything, appearing nine seconds out of every twenty-seven and
   two thousand pixels away from the network it is an echo of. Nobody connects
   them at that distance. So:

     - the header cycles the abstract forms, on 30 seconds at the default length
       of three. Keeping more than one is what keeps the header alive; a single
       form here reduces to a slowly turning ball, which is the failure that
       killed the faceted crystal this replaced. A one-form cycle is allowed
       anyway, because it is the owner's header and the module has to survive
       being told to do it.
     - the skill network gets the constellation, standing still as a form and
       turning at a fraction of the header's rate. It sits behind a figure that
       is read, not watched, so it drifts and twinkles and does nothing else.

   Data binding is structural, not decorative. Every skill in profile.json gets
   its own lobe of the cloud, and in the header the points in that lobe carry
   its discipline's color from --c1..--c6, so the disciplines read as color
   families in both forms. Behind the network that is inverted: see `mono`.

   The morph runs on the GPU. Each point carries the position it is coming from
   and the position it is going to, and one uniform slides between them, so a
   morph costs nothing per frame. The only CPU work is a buffer copy per step.
   A single-form mount never steps at all. That one uniform would move every
   point in lockstep, so each point also carries its own window inside the
   morph, bows its path, and brightens with the distance it has to cover: see
   STAGGER below and `docs/motion.md`.
   ========================================================================== */

import * as THREE from "./vendor/three.module.min.js";

/* Points per skill lobe. Raised from 130 once the forms became geometric: a
   sphere is legible at any density because it has no detail to lose, where a
   ring drawn in too few grains reads as a dashed suggestion of a ring. Note this
   is the opposite lever from an earlier failed experiment, which added points
   *and* shrank them and only made the cloud mushier. Here the grain size is
   untouched and the form simply gets more of them. */
const PER_SKILL = 190;
const MIN_POINTS = 3000;
const MAX_POINTS = 10000;
/* Diameter in device pixels at unit distance, i.e. before the 1/z divide. This
   is the single most sensitive number here: the cloud covers the whole header,
   and additive points that overlap sum towards white, so a value that looks
   merely generous produces an opaque lozenge with the headline lost inside it.
   Grains, not dots. */
const POINT_PX = 21;
/* DWELL + MORPH is one form's turn, and the header shows three, so the full
   cycle is 3 * 10 = 30s. The budget was set on the cycle rather than on the
   dwell: a visitor's wait for any given shape is a whole cycle in the worst
   case, and much past 30s that is longer than most people are on the page.

   The cut came out of DWELL and not MORPH on purpose. The morph is the part
   worth watching now that it staggers, bows and flares, so shortening it to buy
   dwell time would be paying for stillness with the only motion here. */
/* A note for the next review round, since one is cheap to set up and was got
   wrong once. Reviewing candidates means a fast cycle — 1.4 dwell / 0.6 morph,
   so a dozen forms can be seen in one sitting — and at that cadence the three
   per-form easings do not all settle. MONO_EASE and HOLE_EASE at 1.4/s reach
   ~94% over a 2s turn; FACE_EASE's 0.7/s reaches only ~75%, so a `face` form is
   seen within 16 degrees of square rather than 10. Enough to judge a silhouette,
   not enough to cut a marginal form on. Give that one a look at the shipping
   cadence before deciding.

   A cycle longer than three forms is also a longer wait for any given shape:
   the budget above is on the cycle, so six forms is a 60s worst case. That is
   the reason setup caps a chosen cycle rather than a rendering limit. */
const DWELL = 7.0; // seconds a form is held
const MORPH = 3.0; // seconds spent moving to the next one
/* radians/sec, and the panel is the only mount that still turns this way. The
   header had one of these too, at 0.042, on the reasoning that a slow revolution
   spends less of its time in the orientations where a form's outline says least.
   That was treating a symptom: slowing a revolution does not remove the side
   profiles, it lingers on them. SWING replaced it outright.

   The panel keeps it because the constellation has no front to present. It is a
   diffuse shell of lobes, so every yaw is as good as every other, and there is
   no side profile to avoid. Tuned on its own terms besides, behind a figure that
   is read rather than watched. */
const SPIN_PANEL = 0.015;

/* The header does not spin. It swings.

   A full revolution necessarily passes through every yaw, including the two
   quarter-turn side profiles, and every form in this cycle is defined by what it
   looks like from the front. A plane seen edge-on is a line: the wave, which is
   the form doing most of the work here, spends part of every revolution as a
   bright horizontal streak with no surface to it. The ring goes to a bar. The
   network goes to a smear. Those orientations were never worth showing, and a
   slow spin does not make them rarer, only longer.

   So yaw is a bounded oscillation instead: `SWING` radians either side of
   square, and it never comes near PI/2. This is strictly more screen time in the
   orientations the forms were designed for, which is why it can also be a little
   quicker than the spin it replaces without reading as busier.

   SWING_PERIOD is a full there-and-back. Long enough that the reversal at each
   end is never a visible turn-around; the cloud simply seems to breathe. */
const SWING = 0.62; // ~36 degrees. PI/2 is 1.571, so the side profile is far off.
const SWING_PERIOD = 76; // seconds
/* Amplitude multiplier while a `face` form is the one being settled on. Not
   zero: a form pinned exactly square reads as a diagram rather than an object,
   and the small residual swing is what keeps a near and a far side legible. */
const FACE_SWING = 0.28;

/* Scroll exit. The header cloud does not turn as you scroll. It comes apart.

   A scroll-driven rotation was the other candidate and it fails on exactly the
   argument SWING is built on: a rotation has to pass through the orientations
   where a form's outline says nothing, and driving one from scroll hands the
   dwell time in those orientations to the visitor. Stop reading halfway down the
   page and the cloud parks in a side profile and stays there. A dispersal has no
   orientation to get wrong, and it only plays while the header is leaving, which
   is the one stretch where none of these forms still has to read.

   EXIT_RISE is in *view* units. The camera sits at z 4.4 with a 38 degree fov,
   so the visible half-height at the cloud's depth is about 1.5: this is a little
   under two screens of travel, and the fade is spent long before the furthest
   point would be. EXIT_SHEAR is the lateral spread, and it is what stops the
   cloud translating as a block; without it this reads as a page transition
   sliding an image off, not as the object coming apart.

   EXIT_STAGGER is the morph's trick reused (see `lead` in VERT): a per-point
   head start, divided back out so everything is still gone by uExit 1. Keyed to
   point size rather than to the random phase, so the large near-looking grains
   leave first and the cloud pulls apart front to back. Random would disperse it
   just as thoroughly and read as noise rather than as depth. */
const EXIT_RISE = 2.6;
const EXIT_SHEAR = 0.55;
const EXIT_STAGGER = 0.42;
/* Where in a point's own travel its fade begins. Not 0, and that is the whole
   difference between this reading as a departure and reading as the cloud
   simply thinning out. The rise is squared, so almost none of the distance is
   covered early; fading from 0 at the same time meant a point was down to
   two-thirds brightness before it had moved a tenth of the way, and what you saw
   was a dissolve with a slight drift in it. Holding full brightness through the
   first third puts the visible part of the fade on the part of the arc where the
   point is actually moving. */
const EXIT_FADE = 0.35;
/* The scroll window, as fractions of the mount's own height rather than in
   pixels, so it tracks the header across viewport sizes instead of needing a
   second number that can disagree with the CSS.

   The dead zone at the start is what makes this read as a response to scrolling
   rather than as something that was already happening: a two-line nudge on a
   trackpad should not visibly disturb the header. The far end is short of 1 so
   the cloud is spent *before* the IntersectionObserver stops the loop; run it to
   the edge and a fast scroll can freeze the cloud half dispersed, which is then
   what a scroll back up has to un-freeze. */
const EXIT_FROM = 0.1;
const EXIT_TO = 0.8;
/* Reciprocal seconds. Fast enough that it reads as scrubbed by the scroll and
   not as lagging behind it, slow enough that a flicked wheel is a swell rather
   than a snap. */
const EXIT_EASE = 5.0;

/* Shape of the morph itself.

   `uT` is one number for the whole cloud, so on its own the morph is a
   crossfade: every point leaves and arrives on the same frame, along a straight
   line, at the same brightness. Six thousand grains doing that in parallel read
   as one object being dragged between two poses. These four give each point its
   own window inside the morph, bow its path, and light it in proportion to how
   far it has to travel.

   STAGGER is the fraction of the morph spent waiting for the last points to
   start, which compresses each point's own transit to `1 - STAGGER` of MORPH.
   Raising it makes individual grains move *faster* even though the event still
   takes the same wall time; MORPH went from 2.4 to 3.0 to pay for that.

   All four are inert on the panel preset. It is single-form, so `uT` is pinned
   at 0 and `aFrom` equals `aTo`: travel is zero, the stagger has nothing to
   delay, and the arc has no distance to bow across. */
const STAGGER = 0.45;
const SWEEP = 0.7; // share of the stagger that is a wave across x, vs. per-point noise
const BOW = 0.13; // outward bulge at mid-flight, as a fraction of the distance travelled
const FLARE_SIZE = 0.55;
/* Kept far below the panel's flash values, and deliberately the smaller half of
   the flare. This fires behind the headline every 7.5 seconds, and under
   AdditiveBlending alpha over 1 drives points towards white, which is the
   POINT_PX lozenge failure arriving by a different road. Size carries most of
   it: the light theme composites normally and clamps alpha at opaque, so a
   flare carried by brightness would be a dark-theme-only effect. */
const FLARE_A = 0.3;

/* Target bounding radius every attractor is normalised to, matching the
   sphere's 1.28..1.44 and the knot's ~1.3, because SPAN_HEADER assumes that
   scale and the forms have to agree about how big the cloud is or a morph
   becomes a zoom. ATTRACTOR_TUBE is the scatter radius around the centreline;
   see `attractorForm` for why it cannot be zero. */
const ATTRACTOR_R = 1.3;
const ATTRACTOR_TUBE = 0.1;

/* The ring family. Tube is the band's thickness against a major radius of ~1, so
   these are ratios: below about 0.1 the ring starts breaking into dashes at this
   point count, and above about 0.25 the hole closes and it reads as a blob.
   TORUS_TILT is how far the ring's axis leans toward the camera, in radians; at
   0 the ring is horizontal and a level camera sees only its edge. */
const TORUS_TUBE = 0.11;
/* A flat ring lying under the type, seen from slightly above: a disc, not a
   standing hoop. The whole range was rendered and it is a narrow window.

   At PI/2 the axis is on the view direction and you look straight through the
   hole with the headline inside it. That is a clean composition but a vertical
   one, a frame around the type with no near side and no far side. Coming down
   through 0.42 it is still a hoop leaning back rather than a disc lying down.
   0.28 is where it reads as horizontal. Below that it fails quickly: by 0.15 the
   ellipse is thin enough that most of it sits inside the lozenge `uHole` fades
   and it washes out, and at 0 it is the faint horizontal smear that has been
   rejected twice, since edge-on it has no hole of its own left to line up with.

   An earlier note in docs/motion.md called intermediate tilts worse than either
   end. That was measured under the old free running spin, where a tilted ring
   was swept through every orientation and so was never actually seen as a tilt.
   Bounding the yaw is what makes any of this range usable. */
const TORUS_TILT = 0.28;
const RINGS_TUBE = 0.075; // thinner: three links share the light one ring had
const RINGS_TILT = Math.PI / 2;

/* `face` in the registry: forms that only read at one orientation, and so cannot
   be left to whatever angle the free spin happens to hold when their turn comes
   up.

   TORUS_TILT already builds the ring with its axis on the view direction, but
   `cloud.rotation.y` is a free running `clock * spin` and carries that axis
   around with everything else: a quarter turn later the viewer is looking at the
   ring's edge, which is the horizontal bar with dense ends this form was tuned
   to avoid. A full turn is ~150s against a 30s cycle, so the torus arrives 72
   degrees further round on each appearance and is face-on about one time in
   five. The tilt constant was never wrong. The phase it was *shown at* was.

   The network has the same failure for the same reason: its layers lie in the
   xy plane, so edge-on it collapses into a single vertical smear.

   The helix fails differently but just as hard. Its axis is x, so yaw
   foreshortens the length that is the entire point of the form: at the swing's
   36 degree extreme it is down to 0.81 of its span.

   The gate is named here for a reason unlike the others'. It does not collapse
   at any yaw — two masses are two masses from any angle. What it loses is the
   *symmetry* of the gap, and the gap is centred on the headline, so an off-square
   view puts more clearance on one side of the type than the other. At FACE_SWING
   the narrowing is about 1.5% and invisible; across the full arc it is not.

   A form marked `face` has the swing's *amplitude* eased down to FACE_SWING while
   it is the form being settled on, so it sits near square instead of riding the
   full arc. Scaling the amplitude rather than steering the angle is what keeps
   this free of any lurch: there is no correction to apply and nothing to fight,
   the arc simply narrows and the form is already where it needs to be. Under the
   old free spin this had to pull the angle toward the nearest multiple of PI,
   which was a quarter turn of catching up in the worst case and had to be hidden
   inside the morph. Bounding the yaw removed that problem rather than easing
   it. */
// Every form the registry knows keeps its `face` flag whether or not it is in
// anyone's cycle, like every other builder that is built and unused: the tuning
// is the expensive part, not the entry.
/* How fast the amplitude moves between 1 and FACE_SWING, in reciprocal seconds.
   Slow enough that the narrowing reads as the cloud settling rather than as the
   animation changing rate. */
const FACE_EASE = 0.7;

/* `hole` in the registry: a per-form multiplier on `uHole`, the floor the shader
   fades the cloud to behind the headline.

   That floor is 0.12, so a point in the middle of the frame is drawn at an
   eighth of its brightness. Every form in the cycle has been shaped around it
   without ever paying it: the torus's own hole lines up with the lozenge, and
   the wave sits low enough to be outside it. The network is the one form whose
   subject *is* the middle of the frame. Its nodes sit out at the edges and read
   fine, but the edges between them cross the centre, which is exactly where
   they are multiplied by 0.12, and no amount of extra points fixes an alpha
   problem. That was the real reason the connections would not show.

   Applied as a multiplier and not an absolute so the light theme, which fades
   to 0.08 because it composites normally instead of additively, keeps its own
   ratio. The network stays sparse in the middle either way, so lifting the
   floor for it costs the headline much less than lifting it globally would. */
/* How far a network node reaches up and down the next layer, in normalised
   layer position. Sets how steep the connections are and therefore how much
   they cross: at 0 every edge is horizontal and the figure is a set of rails,
   and much past 0.4 the fan is steep enough that the layers stop reading as
   layers. */
const EDGE_SPREAD = 0.3;
const HOLE_EASE = 1.4; // faster than FACE_EASE: brightness may settle inside the morph

/* Camera z. Named because three places depend on it and two of them are not the
   camera: the registry's `aspect` notes derive a ~3.1 half-width from it, and
   `planeForm`
   evaluates the projection itself to decide how wide the plane is at each depth.
   A camera moved without those following it silently mis-sizes both. */
const CAM_Z = 4.4;

/* `mono` in the registry: a per-form collapse of the angular color ramp onto a
   single hue.
   `ramp: 1` on the header samples all six palette hues by atan(p.y, p.x), which
   makes color describe the form's shape. A plane has no angular structure for
   that to describe — the sweep just lays a left-to-right rainbow across a flat
   surface and reads as a smear. So the flat forms go to one hue, with a
   sprinkle of points held at a second one. Eased, not switched, so the color
   arrives over the morph like the hole floor and the swing amplitude do.

   `ripples` is listed on the strength of a render rather than the argument. It
   does have angular structure, but about its own centre and not the origin the
   ramp measures from, and sampling atan(y, x) along a ring that sits well below
   that origin turns out to give a hard purple-left, gold-right split with the
   hues meeting at the top of each arc. That is the plane's rainbow smear
   arriving by a different road, and it was louder than on the plane, because
   here it lands on four separate arcs at once.

   `pool` and `murmuration` are still on the ramp deliberately. Both showed the
   same left-to-right sweep and on neither is it obviously wrong: on the pool it
   reads as a gradient across a bounded figure rather than as a smear across an
   unbounded one, and the murmuration's head and tail being different hues is at
   least describing the form's own axis. Left to be judged next to the mono
   versions rather than reasoned about. */
/* Of the second round, only `plume` stays on the ramp. It is the one form that
   spans a real range of atan(p.y, p.x) along its own axis, so the sweep runs up
   the column and describes it. `crest` and `canopy` are surfaces with no angular
   structure, which is the plane's case exactly. `crescent` and `wake` are the
   ripples case: both are low and wide, so the ramp samples only the bottom of
   the hue circle and splits them left against right — which on a crescent means
   two differently coloured horns and on a wake two differently coloured arms,
   and both forms depend on reading as one object. */
const MONO_EASE = 1.4; // matches HOLE_EASE: both settle inside the morph
/* Share of points kept at the accent hue. Selected from a hash of both seed
   components rather than from aSeed.x alone, which already drives the twinkle
   phase and the morph lead — keying off it directly would make every accent
   point one of the earliest to arrive and all twinkle together. */
const ACCENT_FRAC = 0.05;

/* The cloud is stretched to the box it has to fill rather than left as a ball:
   a ball centred in a wide box reads as an object someone dropped behind the
   text, where a band reads as the surface the text is sitting on. The header is
   roughly twice as wide as it is tall (measured 2.0 at 1280px up to 2.2 at
   1728px, since the height is fixed by `.masthead`'s min-height and only the
   width tracks the viewport). The skill panel is nearer 1.5, so it takes less
   stretch; that is why this is an option and not a constant. If either box's
   proportions change, `x` is what compensates. */
const SPAN_HEADER = [2.0, 1.28, 1.12];
const SPAN_PANEL = [1.5, 1.16, 1.12];

/* `aspect` in the registry: a per-form correction to SPAN, applied as a
   multiplier on top of it. A form without one takes the span as-is. See where it
   is applied for why the structured forms need it and the sphere and knot do
   not. */

/* ==========================================================================
   The form registry

   One entry per form, and it is the only place a form has to be named. Adding
   one is: write a `<name>Form(ctx)` builder above, add an entry here, done. It
   is then selectable from profile.json without touching this file again.

   Everything the rest of the module needs to know about a form lives in its
   entry. This used to be five objects — a builder map plus FORM_FACE,
   FORM_HOLE, FORM_MONO and FORM_ASPECT — keyed by the same strings in five
   places, which meant adding a form was five edits and a *renamed* form was
   four silent no-ops: a missing key in any of those maps is not an error, it is
   a default, so the form simply came out unsquared or the wrong color and
   nothing said why.

     build   the builder, taking the mount's ctx (see mountCloud). Required.
     face    only reads near square; the swing narrows to FACE_SWING for it
     hole    multiplier on the centre fade, default 1
     mono    collapses the color ramp to one hue, 0..1, default 0
     aspect  per-axis multiplier on SPAN, default [1, 1, 1]

   A builder stays here after it loses a review round. The tuning is the
   expensive part and the entry is not, and it is what makes the cut forms
   available to a fork whose page wants something this one did not.
   ========================================================================== */
const FORMS = {
  /* ---- the shipping header cycle ---- */
  /* Boundary, ground and ring: three compositions rather than three
     silhouettes. See PRESETS.header.forms. */
  horizon: { build: horizonForm, mono: 1 },
  wave: { build: waveForm },
  torus: { build: torusForm, face: true, aspect: [0.74, 1.12, 1.12] },

  /* ---- solids ---- */
  sphere: { build: sphereForm },
  knot: { build: knotForm },
  /* Wide enough after SPAN to run past the frustum, which crops the very
     outline they would be in a cycle for. At z 4.4 and a 38 degree fov the
     half-width is ~3.1, so anything reaching beyond that is off screen. */
  rings: { build: ringsForm, face: true, aspect: [0.8, 1.1, 1.1] },
  galaxy: { build: galaxyForm, aspect: [0.88, 1.05, 1.05] },
  /* The same frustum crop, and the one form that could least afford it. gate
     reaches gap 0.78 + bow 0.22 + depth 0.72 = 1.72, which the full 2.0 span
     puts at 3.44 against that ~3.1 half-width. What fell off the edge was the
     outer third of each column — the thinning tail that gives the mass a face
     toward the type and lets it dissolve rather than end. Cropped, the columns
     terminate at the frame instead, which is exactly the two-vertical-bars
     reading the bow was added to avoid: the composition was being undone at the
     edge while it was being fixed at the waist. 0.75 lands at 2.58 and leaves
     the tail inside the frame with room to spare. */
  gate: { build: gateForm, face: true, aspect: [0.75, 1.1, 1.1] },
  helix: { build: helixForm, face: true },
  /* Not a frustum problem like the two above but a density one. At the full
     stretch the four layers sit 1.7 units apart and each edge has to carry its
     points over that distance; pulling the layers together shortens every edge
     without touching the point budget, so the connections gain the brightness
     the extra width was spending. */
  network: { build: networkForm, face: true, hole: 4.5, aspect: [0.8, 1.06, 1.06] },

  /* ---- strange attractors, and the superformula solid ----
     All four read as the sphere with extra noise at this point size and this
     stretch; see the header preset. */
  thomas: { build: thomasForm },
  lorenz: { build: lorenzForm },
  aizawa: { build: aizawaForm, aspect: [0.66, 1.15, 1.15] },
  star: { build: starForm, aspect: [0.66, 1.15, 1.15] },

  /* ---- the ground family ----
     Surfaces rather than silhouettes, all of them mono for the reason above:
     a plane has no angular structure for the ramp to describe. */
  plane: { build: planeForm, mono: 1 },
  basin: { build: basinForm },
  rift: { build: riftForm, mono: 1 },
  pool: { build: poolForm },
  ripples: { build: ripplesForm, mono: 1 },
  terraces: { build: terracesForm, mono: 1 },
  crest: { build: crestForm, mono: 1 },
  canopy: { build: canopyForm, mono: 1 },
  wake: { build: wakeForm, mono: 1 },
  crescent: { build: crescentForm, face: true, mono: 1 },
  murmuration: { build: murmurationForm },
  plume: { build: plumeForm },

  /* ---- panel only ----
     The skill panel's single form. `panel` keeps it out of the header's picker:
     it is an echo of the network figure it sits behind, and it means nothing two
     thousand pixels away from it. */
  constellation: { build: constellationForm, panel: true },
};

/* Defaults, in one place, so a form's entry only says what is unusual about it.
   Read through a function rather than spread with `{...DEFAULTS, ...entry}` at
   the call sites because these are read every frame: `shown` changes on a morph
   boundary, but the lookup runs each tick. */
const FORM_DEFAULTS = { face: false, hole: 1, mono: 0, aspect: [1, 1, 1] };
/* Absence, not falsiness, is what selects a default. Written `f.hole || 1` this
   silently rewrote a stated `hole: 0` — a form asking for its centre fully faded
   — into the default of 1, which is the same "a missing key is not an error, it
   is a default" failure this registry exists to remove, arriving from the other
   side: a value the author actually wrote, overridden without a word. `mono`
   reads the same way for consistency, even though there the default and the
   falsy value happen to coincide today. */
function orDefault(v, dflt) {
  return v == null ? dflt : v;
}
function formMeta(name) {
  const f = FORMS[name] || {};
  return {
    face: orDefault(f.face, FORM_DEFAULTS.face),
    hole: orDefault(f.hole, FORM_DEFAULTS.hole),
    mono: orDefault(f.mono, FORM_DEFAULTS.mono),
    aspect: orDefault(f.aspect, FORM_DEFAULTS.aspect),
  };
}

/* The forms a header cycle may name. Exported so a fork has one authoritative
   list: `scripts/setup_profile.py` offers exactly these, and tests/test_profile.py
   reads them back out of this file rather than keeping a second copy. */
export const HEADER_FORMS = Object.keys(FORMS).filter(function (n) {
  return !FORMS[n].panel;
});

/* What a mount actually shows, given what it was asked for.

   A caller's list is filtered rather than trusted: the header's comes from
   profile.json by way of a data attribute, so an unknown name is a typo in a
   config file and not a programming error, and it must degrade to the preset's
   cycle instead of throwing inside a dynamic import whose only failure path in
   app.js is to delete the element. A list that survives filtering empty is the
   same case. */
function resolveForms(requested, fallback) {
  const asked = Array.isArray(requested) ? requested : [];
  const ok = asked.filter(function (n) {
    return FORMS[n] && !FORMS[n].panel;
  });
  return ok.length ? ok : fallback;
}

/* Everything that differs between the two mounts, kept here rather than passed
   in from app.js: these are tuning numbers for a rendering, and app.js's job is
   to decide *whether* to render, not how. A caller names a preset. */
const PRESETS = {
  header: {
    /* Three forms that differ by *composition*, not just by outline: a ground
       under the type (`wave`), a boundary behind it (`horizon`), a ring around
       it (`torus`).

       That is a later and better rule than silhouette alone, which is what the
       ring-only cycle was built on. Silhouette is necessary but it is not
       sufficient: three objects hanging in the middle of the frame are still
       three objects hanging in the middle of the frame, however different their
       edges. Changing where the form sits relative to the headline is a bigger
       difference than changing its shape.

       The sphere was dropped rather than demoted. It was the neutral state, and
       neutral turned out to mean nothing: once every other form was chosen for
       its outline, the one form with no outline to speak of was simply a pause
       in the cycle.

       Silhouette is still the floor, and it was arrived at the expensive way.
       Intricacy is not visible here at all: between the swing, the additive
       grains, `uHole` muting the interior and SPAN's 2:1 stretch, anything finer
       than the whole object is gone before a visitor sees it. Thomas, Lorenz,
       Aizawa and the star are all in the registry, all four are more interesting
       mathematically than anything in this list, and all four read as the sphere
       with extra noise. See docs/motion.md.

       ---- how the third slot was settled ----

       It was held by `network`, then `gate`, then `basin`, then `plane`, and it
       is now `horizon`. Two review rounds of candidates decided it, and between
       them they ruled out most of the space: every figure lying flat in the
       ground plane (the projection foreshortens all of them into the same band,
       so `pool`, `terraces`, `ripples` and `rift` all arrive as versions of
       `plane` — which lost with them), every form whose subject was a drawn
       structure, and a ground surface deformed into a feature (`crest`).

       `horizon` won on the one thing none of those had: a curved boundary the
       eye can trace, standing clear of the floor rather than lying in it. It is
       a *behind* where wave is a below and torus an around, which is what keeps
       this a cycle of compositions.

       Every builder cut here stays in the registry — the tuning is the expensive
       part, and a fork's page may want what this one did not.

       This list is only the default. `masthead_forms` in profile.json replaces
       it, and may name anything in the registry. */
    forms: ["horizon", "wave", "torus"],
    span: SPAN_HEADER,
    point: POINT_PX,
    // Bounded yaw, not a revolution. See SWING.
    swing: SWING,
    swingPeriod: SWING_PERIOD,
    // Color follows the form here, not the point. See uRamp in VERT.
    ramp: 1,
    tilt: 0.14, // amplitude of the slow nod on x, radians
    // Thin the cloud where the headline and tagline sit.
    hole: { dark: 0.12, light: 0.08 },
    /* Pulled back from 0.95 / 0.66. Two reasons compound: the forms carry more
       points than they used to, and a geometric form holds together at a lower
       opacity than a diffuse one did, because the shape is doing the work the
       brightness used to do. The cloud is meant to be the surface the type sits
       on, not a subject competing with it. */
    opacity: { dark: 0.76, light: 0.48 },
    parallax: true,
    // Disperses upward as the header scrolls away. See EXIT_RISE. The panel
    // cloud does not: it sits beside a figure the reader is working through, and
    // scrolling past it is how you *read* it, not how you leave it.
    exit: true,
  },
  panel: {
    forms: ["constellation"],
    span: SPAN_PANEL,
    // Finer grain than the header. The header cloud is the thing you are
    // looking at; this one is behind forty labels that have to stay readable,
    // and point size is what decides whether it is atmosphere or clutter.
    point: 17,
    // The constellation is a static echo of the figure drawn over it, so it
    // should look like it is settling rather than orbiting; at the header's rate
    // the two disagree about which is moving.
    spin: SPIN_PANEL,
    tilt: 0.05, // matched to the spin: a nod at the header's amplitude over a
    // near-stationary turn reads as wobble rather than as drift
    // No hole. The header thins its middle because one block of type sits
    // there; here the labels are spread over the whole panel, so a lozenge in
    // the centre would be a shape with no reason behind it.
    hole: { dark: 1, light: 1 },
    /* Lower than the header on both surfaces: it is competing with the network
       and its labels, not carrying the section on its own. The gap between the
       two numbers is much wider here than in the header, and that is not a
       taste call. Behind the network the points are small and dense, and small
       dark marks on paper read as grit in a way that small bright marks on
       black do not: at parity with the dark value this panel looked peppered.
       The header's points are large enough to read as light either way. */
    opacity: { dark: 0.6, light: 0.22 },
    /* How far each lobe leans toward its cluster in the network above, as a
       fraction of the full distance. Deliberately not 1.

       At 1 the lobes lock onto the clusters and the cloud becomes a blurred
       copy of the figure sitting directly behind the figure, which also makes
       it a second layer doing the nebula's job: those blobs already ride these
       same centroids. The three layers here are atmosphere, structure and
       figure, and locking collapses two of them into one.

       At about half, the cloud moves the same way without arriving, so the
       parallax between it and the nebula is what keeps them legible as separate
       depths. It reads as the background acknowledging the rearrangement rather
       than as a second plot of it. */
    follow: 0.5,
    // Slower than the nebula's 0.045 glide on purpose. The furthest layer back
    // should be the last to finish moving, or the depth ordering reads wrong.
    followEase: 0.03,
    // No parallax. Hovering this panel isolates a node, so the pointer already
    // means something here and a camera that also slides makes the figure feel
    // unsteady under the cursor.
    parallax: false,
    /* Response to the network's legend, when a caller supplies `filter`.
       `thin` is how far the cloud dims once everything but one discipline is
       filtered out: narrowing the legend is narrowing the subject, so the
       background recedes and leaves the surviving cluster more room. The nebula
       in front of it holds roughly constant total light across the same change,
       so the two carry different halves of the signal instead of doubling one.
       The flash comes in two strengths, and the split is what makes the loud one
       safe. `flash` is what a *tour* step gets: the tour fires every 4.6s on its
       own, and anything bright at that cadence is a strobe behind text people
       are reading. `flashStrong` is what a *click* gets, which is a person
       deliberately working the control and a moment that can afford a bang.
       `skillNet` decides which by setting `strong` on the shared filter state.

       The strong one also blooms the points (`flashSize`) rather than only
       raising alpha. Brightness alone on grains this small reads as the panel
       briefly changing exposure; growing them too is what makes it read as the
       cloud itself igniting. Alpha above 1 is deliberate and safe here: under
       AdditiveBlending it drives the points past their own color into white,
       which is exactly the look. The light theme composites normally, so it
       clamps at fully opaque and the same numbers land softer there. */
    thin: 0.62,
    flash: 0.42,
    flashMs: 520,
    flashStrong: 4.0,
    flashStrongMs: 950,
    flashSize: 0.95,
    /* How far the flash pulls the cloud toward the clicked chip's color. Full
       replacement rather than a wash: at 1.0 the burst is unambiguously that
       discipline's hue, which is the entire point of tinting it, and it is gone
       inside a second. Only the loud (click) flash tints; a tour step changes a
       whole pair at once, so no single color would be honest. */
    flashTint: 1.0,
  },
};

const VERT = [
  "attribute vec3 aFrom;",
  "attribute vec3 aTo;",
  "attribute vec3 aColor;",
  "attribute vec2 aSeed;", // x: phase, y: relative size
  "attribute float aGroup;", // which discipline this point's lobe belongs to
  "uniform float uT;",
  "uniform float uTime;",
  "uniform float uSize;",
  "uniform float uHole;", // how much of the cloud survives behind the type
  "uniform float uExit;", // scroll dispersal, 0 at the top of the page. See EXIT_RISE
  // Per-discipline lean toward where that cluster currently sits in the network
  // drawn over this cloud. In *view* space, so the cloud's own slow spin does
  // not carry the offset around with it.
  "uniform vec2 uGroupPos[6];",
  /* The palette again, but as a *ramp* around the form rather than one hue per
     point. Six discipline colors sprinkled at random over a sphere read as a
     nebula, which is what they were chosen for. Sprinkled over a ring they read
     as confetti and actively fight the geometry: the eye is given six competing
     hues where the form wants one continuous edge. Sampling the same six by
     angle instead turns them into a sweep around the object, so the color
     describes the shape instead of speckling it. uRampAmt is 0 on the panel,
     where discipline color still has to mean discipline. */
  "uniform vec3 uRamp[6];",
  "uniform float uRampAmt;",
  /* Collapses the ramp onto uRamp[0], holding an ACCENT_FRAC sprinkle at
     uRamp[3] instead. Both hues come out of the palette already in the uniform,
     so this needs no new color source and follows the theme for free: the two
     are opposite points on the six-hue wheel, which is what keeps the accent
     legible against the field. Driven per form by the registry's `mono`. */
  "uniform float uMonoAmt;",
  "varying vec3 vColor;",
  "varying float vAlpha;",
  // Injected rather than written out, so the tuning numbers have one home in JS
  // and the shader cannot drift from the constants above it.
  "const float ACCENT_FRAC = " + ACCENT_FRAC.toFixed(3) + ";",
  "const float STAGGER = " + STAGGER.toFixed(3) + ";",
  "const float SWEEP = " + SWEEP.toFixed(3) + ";",
  "const float BOW = " + BOW.toFixed(3) + ";",
  "const float FLARE_SIZE = " + FLARE_SIZE.toFixed(3) + ";",
  "const float FLARE_A = " + FLARE_A.toFixed(3) + ";",
  "const float EXIT_RISE = " + EXIT_RISE.toFixed(3) + ";",
  "const float EXIT_SHEAR = " + EXIT_SHEAR.toFixed(3) + ";",
  "const float EXIT_STAGGER = " + EXIT_STAGGER.toFixed(3) + ";",
  "const float EXIT_FADE = " + EXIT_FADE.toFixed(3) + ";",
  "void main() {",
  "  float travel = length(aTo - aFrom);",
  /* This point's own window inside the morph. Mostly a wave running across x,
     so the morph has a direction rather than happening everywhere at once, plus
     a per-point jitter so the wavefront is ragged instead of a curtain. The
     wave is in object space, so it turns with the cloud's own spin and does not
     always sweep the same way on screen.

     The clamp is load-bearing. `step` swaps the buffers on the frame `uT`
     returns to 0, so every point has to be exactly on `aTo` by `uT` 1; dividing
     by `1 - STAGGER` is what makes the last-starting point finish on time
     rather than being stranded mid-flight when the next dwell begins. */
  "  float sweep = clamp(aFrom.x * 0.19 + 0.5, 0.0, 1.0);",
  "  float lead = STAGGER * mix(aSeed.x, sweep, SWEEP);",
  "  float u = clamp((uT - lead) / (1.0 - STAGGER), 0.0, 1.0);",
  "  float t = u * u * (3.0 - 2.0 * u);", // smoothstep, so a morph eases both ends
  "  vec3 p = mix(aFrom, aTo, t);",
  // Peaks at 1 mid-flight and is exactly 0 at both ends, so everything hung off
  // it vanishes during a dwell and the cloud at rest is unchanged.
  "  float arch = 4.0 * u * (1.0 - u);",
  /* Arc, not a slide. Bowed outward from the cloud's centre, so a morph swells
     and reads as flocking rather than as parallel translation. Radial rather
     than a cross product against a fixed axis, which degenerates for any point
     that happens to travel along that axis. */
  "  p += normalize(p + vec3(1e-4)) * (BOW * travel * arch);",
  // Points that have to cross the cloud ignite; points that barely move stay
  // where they are. This is what makes a morph read as an event.
  "  float flare = arch * smoothstep(0.2, 1.8, travel);",
  // Between morphs the cloud still has to be alive, or it reads as a still
  // image with occasional cuts. Each point drifts on its own phase.
  "  p += 0.045 * vec3(sin(uTime * 0.61 + aSeed.x * 6.28),",
  "                    cos(uTime * 0.47 + aSeed.x * 4.11),",
  "                    sin(uTime * 0.39 + aSeed.y * 5.33));",
  "  vec4 mv = modelViewMatrix * vec4(p, 1.0);",
  /* Scroll dispersal, in *view* space and deliberately not in object space.

     Object space would be wrong twice over. The rise would be carried through
     the swing and the tilt, so "up" would drift with the cloud's own
     orientation instead of meaning up on screen. And it would move `p` out from
     under the ramp lookup at the bottom of this shader, which reads
     `atan(p.y, p.x)`: every point would change color on its way out, turning a
     departure into a departure plus a repaint.

     aSeed.y is a size multiplier in 0.5..2.0, normalised here to 0..1 so the
     biggest grains get a lead of 0 and go first. Squared on the rise so the
     cloud accelerates away rather than sliding at a constant rate: a linear
     version reads as the whole thing being dragged off by something else. */
  "  float sz = (aSeed.y - 0.5) / 1.5;",
  "  float exLead = EXIT_STAGGER * (1.0 - sz);",
  "  float ex = clamp((uExit - exLead) / (1.0 - EXIT_STAGGER), 0.0, 1.0);",
  "  mv.y += EXIT_RISE * ex * ex;",
  "  mv.x += EXIT_SHEAR * ex * (aSeed.x - 0.5);",
  /* Constant loop bound and a constant index into the uniform array. Indexing
     it directly with aGroup would be dynamic indexing, which GLSL ES 1.00 does
     not require an implementation to support; six iterations of a compare is
     the portable way to say the same thing and costs nothing at this count. */
  "  vec2 lean = vec2(0.0);",
  "  for (int gi = 0; gi < 6; gi++) {",
  "    if (gi == int(aGroup + 0.5)) lean = uGroupPos[gi];",
  "  }",
  "  mv.xy += lean;",
  "  gl_Position = projectionMatrix * mv;",
  // uSize is the diameter in device pixels at unit distance, so the whole cloud
  // scales with the canvas without the near points ballooning.
  "  gl_PointSize = uSize * aSeed.y * (1.0 + FLARE_SIZE * flare) / max(0.2, -mv.z);",
  // Thin the cloud out where the headline and tagline are. The ellipse is
  // squashed horizontally because the text block is wide and short, and it
  // fades to uHole rather than to nothing: an empty hole shaped like a lozenge
  // is more conspicuous than a slightly quieter middle.
  "  vec2 ndc = gl_Position.xy / max(0.0001, gl_Position.w);",
  "  float d = length(ndc * vec2(0.42, 0.85));",
  "  vAlpha = mix(uHole, 1.0, smoothstep(0.22, 0.95, d));",
  // Depth cue. Size already falls off with 1/z, but size alone reads as a flat
  // scatter of mixed grains; dimming the far side is what makes the same points
  // read as a volume with a front and a back.
  "  vAlpha *= mix(0.3, 1.0, smoothstep(6.4, 3.1, -mv.z));",
  "  vAlpha *= 0.6 + 0.4 * sin(uTime * 1.6 + aSeed.x * 11.0);", // twinkle
  // After the hole, so a point crossing the thinned middle stays thinned: the
  // flare is allowed to be bright, but not to redraw the gap the headline sits
  // in.
  "  vAlpha *= 1.0 + FLARE_A * flare;",
  /* The fade, last, so nothing above can add light back to a point that has
     already left. It has to be here rather than on the element's opacity for a
     reason that is not only tidiness: `uHole` is computed from the point's
     *screen* position, so a rising point leaves the thinned lozenge and its
     alpha climbs toward 1 on the way out. A fade on the container would be
     fighting that; a fade on vAlpha multiplies it away. Starts at EXIT_FADE, not
     at 0, and reaches nothing exactly at 1, where EXIT_STAGGER has already
     guaranteed every point has arrived. */
  "  vAlpha *= 1.0 - smoothstep(EXIT_FADE, 1.0, ex);",
  /* Ramp lookup. Constant loop bounds and constant indices, for the same reason
     uGroupPos is read this way: GLSL ES 1.00 does not require support for
     dynamic indexing into a uniform array. Wraps, so the sweep closes seamlessly
     on a form that comes back round to itself. */
  "  float ang = atan(p.y, p.x) / 6.2831853 + 0.5;",
  "  float f = ang * 6.0;",
  "  float i0 = floor(f);",
  "  float i1 = mod(i0 + 1.0, 6.0);",
  "  vec3 c0 = uRamp[0];",
  "  vec3 c1 = uRamp[0];",
  "  for (int k = 0; k < 6; k++) {",
  "    if (float(k) == i0) c0 = uRamp[k];",
  "    if (float(k) == i1) c1 = uRamp[k];",
  "  }",
  "  vColor = mix(aColor, mix(c0, c1, f - i0), uRampAmt);",
  /* Decorrelated from the twinkle and the morph lead: fract() of a weighted sum
     of both seed components, rather than aSeed.x, which both of those read. */
  "  float acc = step(1.0 - ACCENT_FRAC, fract(aSeed.x * 7.31 + aSeed.y * 3.17));",
  "  vColor = mix(vColor, mix(uRamp[0], uRamp[3], acc), uMonoAmt);",
  "}",
].join("\n");

const FRAG = [
  "uniform float uOpacity;",
  "uniform vec3 uTint;", // color of the chip that caused the current flash
  "uniform float uTintAmt;", // 0 at rest, so the cloud is its own color again
  "varying vec3 vColor;",
  "varying float vAlpha;",
  "void main() {",
  "  vec2 c = gl_PointCoord - 0.5;",
  "  float d = dot(c, c);",
  "  if (d > 0.25) discard;", // round points, no sprite texture to ship
  "  float a = smoothstep(0.25, 0.0, d);",
  // A uniform mix rather than a rewrite of the color attribute: tinting the
  // buffer would mean writing count*3 floats on every flash and again on every
  // decay step, to say something the GPU can do for free.
  "  vec3 col = mix(vColor, uTint, uTintAmt);",
  "  gl_FragColor = vec4(col, a * vAlpha * uOpacity);",
  "}",
].join("\n");

/* Deterministic PRNG. The cloud has to look the same on every load, or a
   reload silently becomes a different composition and there is no way to tell a
   layout change from noise while tuning it. */
function rng(seed) {
  let s = seed >>> 0;
  return function () {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

/* ==========================================================================
   The builders

   One per form, each returning a Float32Array of `count * 3` positions in form
   units, before SPAN and before `aspect`. They take the mount's `ctx` rather
   than closing over it — `count` and the seeded `rand` at minimum, plus `SPAN`
   and `FILL_W` for anything that has to fill the frame, plus the group fields
   for the constellation. That is the whole reason they sit out here at module
   scope instead of inside mountCloud: a form's builder and its tuning can then
   live in the same registry entry. See FORMS.

   Most of what is below is comment, and it is the point. Nearly every form here
   has at least one number that was wrong first, and the note says which way it
   failed. A builder that has lost a review round keeps its notes for the same
   reason it keeps its entry.
   ========================================================================== */

function sphereForm(ctx) {
  const { count, rand } = ctx;
  // Fibonacci shell: even coverage, no pole clustering.
  const a = new Float32Array(count * 3);
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    const t = (i + 0.5) / count;
    const y = 1 - 2 * t;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const th = golden * i;
    const shell = 1.28 + 0.16 * rand();
    a[i * 3] = Math.cos(th) * r * shell;
    a[i * 3 + 1] = y * shell;
    a[i * 3 + 2] = Math.sin(th) * r * shell;
  }
  return a;
}

function knotForm(ctx) {
  const { count, rand } = ctx;
  // (2,3) torus knot centreline with points scattered in the tube around it.
  const a = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const u = ((i / count) * Math.PI * 2 * 3) % (Math.PI * 2);
    const p = 2;
    const q = 3;
    const r = 0.62 + 0.34 * Math.cos(q * u);
    const cx = r * Math.cos(p * u);
    const cy = r * Math.sin(p * u);
    const cz = 0.34 * Math.sin(q * u);
    const rad = 0.1 * Math.sqrt(rand());
    const ang = rand() * Math.PI * 2;
    a[i * 3] = cx * 1.35 + Math.cos(ang) * rad;
    a[i * 3 + 1] = cy * 1.35 + Math.sin(ang) * rad;
    a[i * 3 + 2] = cz * 1.35 + (rand() - 0.5) * 0.2;
  }
  return a;
}

/* ---- strange attractors ----------------------------------------------
   One integrator, one resampler, one scatter. Everything specific to a given
   attractor is its derivative function and four numbers, which is the whole
   reason these are worth having: the machinery below is the expensive part
   and it is written once, so a fifth attractor costs three lines.

   Four steps, and three of them are load bearing rather than polish:

     1. Integrate with RK4. Euler at a step size coarse enough to cover the
        attractor in reasonable time visibly spirals off the sheets.
     2. Discard a burn-in prefix. From an arbitrary seed the trajectory falls
        onto the attractor from wherever it started, and those points would
        ship as a stray tail hanging off the form.
     3. Resample by *arc length*, not by time step. The trajectory slows down
        in places and a fixed dt piles points there; overlapping additive
        points sum towards white, which is the POINT_PX lozenge failure
        arriving by a third road.
     4. Scatter into a tube. An attractor is a 1D curve, so `count` points on
        a bare centreline is a thread, where the sphere is a 2D shell and
        reads as volume. This is not optional: skipping it reproduces the
        constellation's "six piles of speckles" problem exactly. */
function attractorForm(ctx, o) {
  const { count, rand } = ctx;
  const deriv = o.deriv;
  const h = o.h;
  const steps = o.steps || 26000;
  const path = new Float64Array(steps * 3);
  const k1 = [0, 0, 0];
  const k2 = [0, 0, 0];
  const k3 = [0, 0, 0];
  const k4 = [0, 0, 0];
  let x = o.start[0];
  let y = o.start[1];
  let z = o.start[2];

  function rk4() {
    deriv(x, y, z, k1);
    deriv(x + h * 0.5 * k1[0], y + h * 0.5 * k1[1], z + h * 0.5 * k1[2], k2);
    deriv(x + h * 0.5 * k2[0], y + h * 0.5 * k2[1], z + h * 0.5 * k2[2], k3);
    deriv(x + h * k3[0], y + h * k3[1], z + h * k3[2], k4);
    x += (h / 6) * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0]);
    y += (h / 6) * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1]);
    z += (h / 6) * (k1[2] + 2 * k2[2] + 2 * k3[2] + k4[2]);
  }

  for (let i = 0; i < (o.burn || 3000); i++) rk4(); // the spiral-on, thrown away
  for (let i = 0; i < steps; i++) {
    rk4();
    path[i * 3] = x;
    path[i * 3 + 1] = y;
    path[i * 3 + 2] = z;
  }

  // Cumulative arc length along the trajectory, then `count` samples spaced
  // evenly along *it* rather than evenly in time.
  const cum = new Float64Array(steps);
  let total = 0;
  for (let i = 1; i < steps; i++) {
    const dx = path[i * 3] - path[(i - 1) * 3];
    const dy = path[i * 3 + 1] - path[(i - 1) * 3 + 1];
    const dz = path[i * 3 + 2] - path[(i - 1) * 3 + 2];
    total += Math.sqrt(dx * dx + dy * dy + dz * dz);
    cum[i] = total;
  }

  const pts = new Float64Array(count * 3);
  let j = 1;
  for (let i = 0; i < count; i++) {
    const want = ((i + 0.5) / count) * total;
    while (j < steps - 1 && cum[j] < want) j++;
    const span = cum[j] - cum[j - 1];
    const f = span > 0 ? (want - cum[j - 1]) / span : 0;
    for (let c = 0; c < 3; c++) {
      const p0 = path[(j - 1) * 3 + c];
      pts[i * 3 + c] = p0 + (path[j * 3 + c] - p0) * f;
    }
  }

  // Centre on the mean and normalise the bounding radius, so every attractor
  // arrives at the scale SPAN_HEADER already assumes. Without this each one
  // would need its own hand-found scale factor and would drift out of
  // agreement with the sphere and the knot the moment either changed.
  const mean = [0, 0, 0];
  for (let i = 0; i < count; i++)
    for (let c = 0; c < 3; c++) mean[c] += pts[i * 3 + c] / count;
  let maxR = 1e-9;
  for (let i = 0; i < count; i++) {
    const dx = pts[i * 3] - mean[0];
    const dy = pts[i * 3 + 1] - mean[1];
    const dz = pts[i * 3 + 2] - mean[2];
    maxR = Math.max(maxR, Math.sqrt(dx * dx + dy * dy + dz * dz));
  }
  const s = (o.radius || ATTRACTOR_R) / maxR;

  /* Which way up. These systems are conventionally plotted with z vertical
     and three.js has y up, so an attractor written straight out lies on its
     side and the shape everyone recognises is lost. `up` names the axis that
     should become y. */
  const up = o.up || "z";
  const iy = up === "z" ? 2 : up === "x" ? 0 : 1;
  const ix = up === "z" ? 0 : up === "x" ? 1 : 0;
  const iz = up === "z" ? 1 : up === "x" ? 2 : 2;

  const tube = o.tube == null ? ATTRACTOR_TUBE : o.tube;
  const a = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    // Uniform direction, radius skewed towards the centreline so the tube has
    // a dense core and a soft edge rather than reading as a hose.
    const rr = tube * Math.pow(rand(), 0.5);
    const th = rand() * Math.PI * 2;
    const ph = Math.acos(2 * rand() - 1);
    a[i * 3] = (pts[i * 3 + ix] - mean[ix]) * s + rr * Math.sin(ph) * Math.cos(th);
    a[i * 3 + 1] = (pts[i * 3 + iy] - mean[iy]) * s + rr * Math.sin(ph) * Math.sin(th);
    a[i * 3 + 2] = (pts[i * 3 + iz] - mean[iz]) * s + rr * Math.cos(ph);
  }
  return a;
}

/* The attractors themselves. Each is a derivative and a step size; the
   constants are the standard published ones, because these are the parameter
   values at which each system is the shape it is famous for. */
function thomasForm(ctx) {
  /* b is the one dial that matters. Above ~0.33 it collapses into a simple
     closed orbit and reads as a worse copy of the knot; below ~0.19 it goes
     diffuse. Isotropic and dense: the tangle. */
  const b = 0.208;
  return attractorForm(ctx, {
    deriv: function (x, y, z, k) {
      k[0] = Math.sin(y) - b * x;
      k[1] = Math.sin(z) - b * y;
      k[2] = Math.sin(x) - b * z;
    },
    start: [0.1, 0, 0],
    h: 0.05,
    steps: 30000,
    up: "y",
  });
}

function lorenzForm(ctx) {
  // The butterfly. Two lobes and an obvious silhouette, which is what makes
  // it the one form here a visitor might actually recognise.
  return attractorForm(ctx, {
    deriv: function (x, y, z, k) {
      k[0] = 10 * (y - x);
      k[1] = x * (28 - z) - y;
      k[2] = x * y - (8 / 3) * z;
    },
    start: [0.9, 0, 1],
    h: 0.004,
    steps: 30000,
    tube: 0.075, // finer: the sheets are thin, and a fat tube fills the gap
  });
}

function aizawaForm(ctx) {
  // A spindle wound around a sphere: the most ornamental of the four, and the
  // only one whose interior reads as full rather than as a shell.
  const a = 0.95;
  const b = 0.7;
  const c = 0.6;
  const d = 3.5;
  const e = 0.25;
  const f = 0.1;
  return attractorForm(ctx, {
    deriv: function (x, y, z, k) {
      k[0] = (z - b) * x - d * y;
      k[1] = d * x + (z - b) * y;
      k[2] =
        c + a * z - (z * z * z) / 3 - (x * x + y * y) * (1 + e * z) + f * z * x * x * x;
    },
    start: [0.1, 0, 0],
    h: 0.008,
    /* Fewer steps than the other two, and it costs nothing visually. What
       decides coverage is how much of the attractor the trajectory walks, not
       how finely it is sampled, because the resampler then spreads `count`
       points evenly along whatever arc length it was handed. Measured at
       1280px: 11ms here, roughly two and a half times that at 30000, for the
       same form. Worth knowing before raising it. */
    steps: 12000,
  });
}

/* Gielis superformula, as a 3D surface from two of them crossed. The odd one
   out on purpose: four attractors is one idea four times, and this is a 2D
   shell like the sphere, so it needs no tube scatter and it reads hard and
   symmetric where an attractor reads soft and chaotic. Sampled on the same
   Fibonacci lattice as sphereForm, so coverage stays even and no pole
   clusters, then pushed out by the two radii. */
function starForm(ctx) {
  const { count, rand } = ctx;
  const a = new Float32Array(count * 3);
  const golden = Math.PI * (3 - Math.sqrt(5));
  // m is the symmetry, i.e. how many points it has. n1 below 1 is what makes
  // the arms concave and spiky rather than a rounded blob.
  function sf(ang, m, n1, n2, n3) {
    const t = (m * ang) / 4;
    const p1 = Math.pow(Math.abs(Math.cos(t)), n2);
    const p2 = Math.pow(Math.abs(Math.sin(t)), n3);
    return Math.pow(p1 + p2, -1 / n1);
  }
  let maxR = 1e-9;
  for (let i = 0; i < count; i++) {
    const t = (i + 0.5) / count;
    const phi = Math.asin(1 - 2 * t); // -pi/2..pi/2, even in area
    const th = golden * i;
    const r1 = sf(th, 7, 0.2, 1.7, 1.7);
    const r2 = sf(phi, 3, 0.3, 1.7, 1.7);
    // A little thickness, or a shell of points on an exact surface reads as a
    // wireframe with moire rather than as an object.
    const shell = 0.94 + 0.12 * rand();
    const x = r1 * Math.cos(th) * r2 * Math.cos(phi) * shell;
    const y = r2 * Math.sin(phi) * shell;
    const z = r1 * Math.sin(th) * r2 * Math.cos(phi) * shell;
    a[i * 3] = x;
    a[i * 3 + 1] = y;
    a[i * 3 + 2] = z;
    maxR = Math.max(maxR, Math.sqrt(x * x + y * y + z * z));
  }
  // Same normalisation contract as the attractors: arrive at ATTRACTOR_R so
  // every form agrees about the size of the cloud.
  const s = ATTRACTOR_R / maxR;
  for (let i = 0; i < count * 3; i++) a[i] *= s;
  return a;
}

/* ---- forms that differ by silhouette ---------------------------------
   What this header can actually show is an outline. The spin is 84s for a
   full turn, the grains are large and additive, `uHole` mutes the interior,
   and SPAN stretches everything 2:1: between them, structure finer than the
   whole object is thrown away before a visitor sees it. Aizawa and the star
   failed here for exactly that reason. Both are genuinely intricate and both
   are, at arm's length, spheroids, so they read as the sphere with extra
   noise. The forms below were chosen the other way round, by asking what the
   silhouette does, and each is deliberately dumb up close. */

/* One ring, built in the xz plane and then tilted up about x.

   The tilt is the whole difference between a ring and a band. Built flat, its
   axis is vertical and a camera at y 0 sees it edge-on, which is a horizontal
   bar with dense ends: the shape reads as the sphere squashed, not as a ring
   with a hole. Tilting the axis toward the camera opens it into an ellipse.

   It does still pass through edge-on: the spin sweeps the ring's axis around a
   cone, so twice per 84s revolution the axis is square to the view. That is
   not worth engineering away. A ring turning through its own edge is the most
   legible thing a ring can do, and it is the reason this form does not need
   internal structure to stay interesting. */
function ringPoints(ctx, a, at, n, o) {
  const { rand } = ctx;
  /* Built in the xz plane, so its axis starts vertical, then rotated about x
     and about y and finally moved along x.

     `rotX` is what decides whether it is a ring at all. At 0 the axis is
     vertical and a camera at y 0 sees only the edge: a horizontal bar with
     dense ends, which reads as the sphere squashed. At PI/2 the axis points
     at the camera and the viewer looks straight through the hole, which is
     the whole reason this form is in the cycle. `rotY` then swings that hole
     away again, which is what makes one link of a chain sit across another. */
  const R = o.R;
  const tube = o.tube;
  const spread = o.spread == null ? 0.4 : o.spread;
  const cx = Math.cos(o.rotX);
  const sx = Math.sin(o.rotX);
  const cy = Math.cos(o.rotY || 0);
  const sy = Math.sin(o.rotY || 0);
  for (let i = 0; i < n; i++) {
    // Golden angle around the major circle so points do not band into spokes;
    // uniform around the minor one.
    const u = (at + i) * Math.PI * (3 - Math.sqrt(5));
    const v = rand() * Math.PI * 2;
    const rr = tube * (1 - spread + spread * rand());
    const ring = R + rr * Math.cos(v);
    let px = ring * Math.cos(u);
    let py = rr * Math.sin(v);
    let pz = ring * Math.sin(u);
    // about x
    const y1 = py * cx - pz * sx;
    const z1 = py * sx + pz * cx;
    // about y
    const x2 = px * cy + z1 * sy;
    const z2 = -px * sy + z1 * cy;
    a[(at + i) * 3] = x2 + (o.cx || 0);
    a[(at + i) * 3 + 1] = y1;
    a[(at + i) * 3 + 2] = z2;
  }
}

function torusForm(ctx) {
  const { count } = ctx;
  /* A ring, and the one form that keeps its own opening roughly over the one
     `uHole` already cuts, so the header's constraint works for it instead of
     against it. Nothing else here reads as unmistakably *not* a ball.

     Flat rather than face-on, see TORUS_TILT. At 0.28 the opening no longer
     sits over the lozenge the way the face-on ring's did, which is the price
     of lying it down, and it is also why the tilt cannot go much lower: the
     flatter the ellipse, the more of it falls inside the fade.

     TORUS_TUBE is thin on purpose. At the 0.36 this first shipped with, the
     tube was over a third of the major radius and the hole closed up into a
     fat doughnut that read as a blob again; the ring only becomes a *ring*
     once the band is narrow enough that the gap dominates. */
  const a = new Float32Array(count * 3);
  ringPoints(ctx, a, 0, count, {
    R: 1.02,
    tube: TORUS_TUBE,
    rotX: TORUS_TILT,
    spread: 0.35,
  });
  for (let i = 0; i < count * 3; i++) a[i] *= 1.3;
  return a;
}

function ringsForm(ctx) {
  const { count } = ctx;
  /* Three interlocking links, laid along the width of the masthead like a
     chain, not three bands sharing a plane.

     Rings on one plane cannot interlock; that is what a plane means. So the
     middle link is swung most of a right angle away from the outer two on
     `rotY`, and the centres are set a little closer together than the radius
     so consecutive links actually pass through one another rather than
     merely touching. The outer two face the viewer, the middle one is seen
     nearly edge-on, and the alternation is what reads as a chain.

     An earlier attempt made them concentric and coplanar, which was legible
     and wrong: it read as a record, and rings that share a plane are exactly
     the ones that cannot link. An earlier attempt still made them three
     different axes about a shared centre, armillary style, which read as a
     ball because three ellipses at three angles just fill in a sphere. The
     chain is the version that is both legible and actually interlocked. */
  const a = new Float32Array(count * 3);
  const per = Math.floor(count / 3);
  /* Small. Three links at the torus's radius reach well past the frustum once
     SPAN has stretched them, so what survives on screen is three arcs and no
     chain. The whole form has to fit inside the frame or the interlocking,
     which is the only reason it is here, is the part that gets cropped. */
  const R = 0.52;
  const gap = R * 0.86; // < R, so neighbours overlap instead of abutting
  ringPoints(ctx, a, 0, per, { R: R, tube: RINGS_TUBE, rotX: RINGS_TILT, rotY: 0, cx: -gap });
  ringPoints(ctx, a, per, per, {
    R: R,
    tube: RINGS_TUBE,
    rotX: RINGS_TILT,
    /* The cross link, and this is a brightness constraint as much as a shape
       one. Nearer a right angle it is edge-on, which collapses a third of the
       cloud's points into a narrow bar; under additive blending that bar sums
       towards white and reads as a flare, not a ring. At 1.0 it is a clear
       ellipse, still obviously across the other two. */
    rotY: 1.0,
    cx: 0,
  });
  ringPoints(ctx, a, per * 2, count - per * 2, {
    R: R,
    tube: RINGS_TUBE,
    rotX: RINGS_TILT,
    rotY: 0,
    cx: gap,
  });
  for (let i = 0; i < count * 3; i++) a[i] *= 1.3;
  return a;
}

function helixForm(ctx) {
  const { count, rand } = ctx;
  /* A double helix laid along x, because the header is twice as wide as it is
     tall and this is the only form here that uses that: it runs the length of
     the masthead instead of sitting in the middle of it. Two strands so the
     crossing pattern reads even when the whole thing is small. */
  const a = new Float32Array(count * 3);
  const turns = 3.2;
  const rad = 0.62;
  for (let i = 0; i < count; i++) {
    const t = (i + 0.5) / count;
    const strand = i % 2 === 0 ? 0 : Math.PI; // the two backbones
    const ang = t * turns * Math.PI * 2 + strand;
    // Tapered at both ends, so it reads as an object rather than as something
    // cropped by the edges of the header.
    const taper = 0.55 + 0.45 * Math.sin(Math.PI * t);
    const jitter = 0.075 * Math.pow(rand(), 0.5);
    const jt = rand() * Math.PI * 2;
    a[i * 3] = (t * 2 - 1) * 1.55;
    a[i * 3 + 1] = Math.cos(ang) * rad * taper + Math.cos(jt) * jitter;
    a[i * 3 + 2] = Math.sin(ang) * rad * taper + Math.sin(jt) * jitter;
  }
  return a;
}

function gateForm(ctx) {
  const { count, rand } = ctx;
  /* Two columns of points at the left and right of the frame with an empty
     middle: the type stands in the gap rather than in front of anything.

     Held the third slot and no longer does; kept built and tuned. It is still
     the only form here whose composition is defined by what it leaves out.
     Every other form in the file is mass with a shape; this is a shape made of
     absence, and it remains the only *beside* the file has — the one placement
     wave (mass below), torus (mass around) and basin (also below) do not cover
     between them. That is what makes it the first thing to reach for if the
     cycle ever wants a third region again.

     It is also the only form that gets `uHole` for free. The centre fade is a
     tax every other form pays — it is what cost the network a debugging round —
     and a form with nothing in the middle never comes near it. Nothing here is
     tuned around the lozenge, because nothing here is inside it.

     Curved rather than straight. Two vertical bars read as a UI frame, a
     cropping device around the content; bowing them outward at the waist makes
     them read as two masses the type is standing between. */
  const a = new Float32Array(count * 3);
  const gap = 0.78; // half-width of the clearing, in pre-SPAN units
  for (let i = 0; i < count; i++) {
    const side = i % 2 === 0 ? -1 : 1;
    const t = (i + 0.5) / count;
    // Denser at the inner edge, thinning outward, so each column has a defined
    // face toward the type and dissolves at the frame edge instead of ending.
    const depth = Math.pow(rand(), 0.55);
    const y = (rand() * 2 - 1) * 1.02;
    // The bow: widest clearing at the vertical middle, where the type is.
    const bow = 0.22 * Math.cos((y / 1.02) * Math.PI * 0.5);
    a[i * 3] = side * (gap + bow + depth * 0.72);
    a[i * 3 + 1] = y * (1 - 0.18 * depth); // tapered, so the columns are lens shaped
    a[i * 3 + 2] = (rand() * 2 - 1) * 0.5 + Math.sin(t * 11.0) * 0.12;
  }
  return a;
}

function planeForm(ctx) {
  const { count, rand, SPAN, FILL_W } = ctx;
  /* A flat ground plane in perspective, sitting just under the paragraph.

     It replaced `basin`, which was correct on paper and too close to the wave
     on screen: both are mass below the type, and a parabola full of scatter is
     a wave with a symmetric profile. This differs by *surface* rather than by
     silhouette — a defined far edge, a density that thickens with distance,
     and level ground where the wave has a rolling one.

     Structure has failed here three times (helix, the network's rails, lattice
     at two spacings), always for the same reason: those forms need resolvable
     spacing between lines, and POINT_PX 21 closes it. A filled plane does not
     ask for that. It reads by its edge and its density gradient, which is what
     survives at this point size.

     ---- placement, which perspective decides and not the y value ----

     At z 4.4 every position scales by 4.4 / (4.4 - z), so a plane's far edge
     rides UP the screen and its near edge rides DOWN and OUT. Both ends have
     to be checked and the naive version fails at both: a plane at y -0.66
     spanning z +1.15..-1.8 puts its far edge at -0.564, inside the tagline,
     while its near edge reaches |x| 4.30 against a frame of 3.1.

     ---- width, which is where the surface area comes from ----

     A finite rectangle in world space converges to a trapezoid on screen, and
     the first version did: 3.04 wide at the near edge down to 1.71 at the far
     one, leaving most of the back half of the frame empty. A real ground plane
     does not do that. It runs off the sides at every depth and the frustum
     crops it, so it fills the frame all the way back.

     Building it that way literally would mean authoring a plane wide enough to
     survive the near edge's 1.217x magnification and letting the GPU discard
     most of it — points paid for and never seen, out of a fixed budget. So the
     half-width is solved per depth instead: TARGET / k, where k is the same
     projection the camera applies. Every point lands inside the frame and the
     plane still fills it edge to edge at every depth. Identical on screen to
     the infinite version, with nothing spent off-camera.

     t is biased toward the far edge because that widening cancels the
     compression that would otherwise pack the back of the plane denser than
     the front. Uniform t now reads flat; density thickening with distance is
     the depth cue that survives once the outline stops providing one.

     The motes are what stop it reading as a decal: a share of the points lift
     off the surface so there is air above it. Their lift fades to zero with
     distance, because near the far edge the same lift would push points back
     up into the paragraph, and because motes hanging over the horizon is not
     what depth looks like anyway. */
  const a = new Float32Array(count * 3);
  const Y = -0.9;
  const Z_NEAR = 0.7;
  const Z_FAR = -2.1;
  const TARGET = FILL_W; // measured off the camera and the mount, see FILL_W
  for (let i = 0; i < count; i++) {
    const t = Math.pow(rand(), 0.72); // biased toward the far edge
    const z = Z_NEAR + (Z_FAR - Z_NEAR) * t;
    // The projection itself, so the plane's width cannot drift from the camera.
    const k = CAM_Z / (CAM_Z - z * SPAN[2]);
    const lift = rand() < 0.12 ? Math.pow(rand(), 1.8) * 0.3 * (1 - t) : 0;
    a[i * 3] = (rand() * 2 - 1) * (TARGET / k);
    a[i * 3 + 1] = Y + lift + (rand() - 0.5) * 0.036;
    a[i * 3 + 2] = z;
  }
  return a;
}

/* ---- candidates for the third slot ------------------------------------
   Six forms under review against `plane`, five of them its family. They share
   its two proven devices — the per-depth half-width solve, so the surface
   fills the frame at every depth without paying for points the frustum would
   discard, and a density gradient rather than resolvable line spacing, which
   is what has killed structure here three times.

   None of them is tuned yet. The numbers below are first estimates derived
   from the same projection arithmetic `planeForm` documents (at CAM_Z 4.4 a
   point scales by 4.4 / (4.4 - z), the frame half-width is ~3.05 and its
   half-height ~1.47), placed so nothing lands in the type's y -0.95..+1.34,
   and they are meant to be moved after the first render, not defended. */

function riftForm(ctx) {
  const { count, rand, SPAN, FILL_W } = ctx;
  /* The ground plane split by a canyon running away from the viewer: two
     masses whose inner edges converge, with the headline sitting over the
     point they converge on.

     This is the only candidate that adds a *composition* rather than a better
     version of one already in the cycle. `gate` is the file's only `beside`
     and it lost because two vertical masses read as a UI frame around the
     content; the same negative space built out of perspective instead reads as
     ground with a gap in it, because converging edges are a depth cue and
     parallel ones are a border.

     The convergence runs the natural way — wide at the near edge, closed at
     the far one — so the empty V opens toward the bottom of the frame and its
     apex points up at the type. Reversing it (a valley widening away) puts the
     void behind the headline instead and is the other version worth a render
     if this one reads as two separate planes rather than one split one. */
  const a = new Float32Array(count * 3);
  const Y = -0.9;
  const Z_NEAR = 0.7;
  const Z_FAR = -2.1;
  const TARGET = FILL_W;
  /* Inner edge of each mass at the near edge, in TARGET's units, so 0.42 is
     ~0.84 on screen against a frame half-width of 3.05. A slot, not a chasm.

     The first render used 1.05 and reproduced the `gate` failure outright:
     at that width the two masses sit against the left and right frame edges
     with the whole middle empty, which is two masses beside the type however
     it was built, and the convergence that was supposed to distinguish it is
     off in the part of the figure the eye has already stopped reading. Narrow
     enough and the ground still reads as one surface with a cut in it, which
     is the only version of this idea worth having. */
  const GAP_NEAR = 0.42;
  const GAP_FAR = 0.0; // closed: the two masses meet at the vanishing point
  for (let i = 0; i < count; i++) {
    const t = Math.pow(rand(), 0.72);
    const z = Z_NEAR + (Z_FAR - Z_NEAR) * t;
    const k = CAM_Z / (CAM_Z - z * SPAN[2]);
    const gap = GAP_NEAR + (GAP_FAR - GAP_NEAR) * t;
    const side = i % 2 === 0 ? -1 : 1;
    /* Biased toward the inner edge, so each mass has a defined face on the
       canyon and dissolves toward the frame, which is `gate`'s device: an
       edge the eye can find is what makes the gap a shape rather than a gap. */
    const u = Math.pow(rand(), 0.62);
    const lift = rand() < 0.12 ? Math.pow(rand(), 1.8) * 0.3 * (1 - t) : 0;
    a[i * 3] = (side * (gap + (TARGET - gap) * u)) / k;
    a[i * 3 + 1] = Y + lift + (rand() - 0.5) * 0.036;
    a[i * 3 + 2] = z;
  }
  return a;
}

function horizonForm(ctx) {
  const { count, rand, SPAN, FILL_W } = ctx;
  /* The plane with an arc for a far edge instead of a straight one, and a
     surface that falls away with distance.

     A straight far edge reads as a table edge. The claim to test is that a
     curved one reads as a limb, and that the difference is worth a slot.

     Both numbers exist because the naive version is invisible. Curving the
     boundary in *world* space buys almost nothing on screen: the far edge is
     already compressed by the 1/z divide, so a sag that looks large in the
     geometry arrives as about 2% of the frame height. SAG is therefore in
     depth — the middle of the boundary runs furthest back and the sides come
     forward to meet the near edge — which is worth about 10% of frame height
     and is the largest arc available before the surface stops having any
     depth at the sides at all. DROP is the surface falling as it recedes, and
     it does the second job: without it the far edge rides up to y -0.75 and
     sits inside the tagline, which is the failure `planeForm` already
     documents. SAG is the constant to move first. */
  const a = new Float32Array(count * 3);
  const Y = -0.86;
  const Z_NEAR = 0.7;
  const Z_FAR = -2.35; // at the middle of the arc, where the surface runs furthest
  /* 1.45 on the first render was a real arc but a soft one, and the sides ran
     to screen y -1.47, exactly the frame bottom, so the ends of the boundary
     were being cropped rather than curving. Trading some DROP for more SAG
     fixes both: the sides come up to -1.33 and the arc's rise is ~14% of the
     frame height instead of 10%. */
  const SAG = 1.9; // how far the sides of the boundary come forward
  const DROP = 0.3; // extra fall of the surface at the far edge
  const TARGET = FILL_W;
  for (let i = 0; i < count; i++) {
    const u = rand() * 2 - 1; // normalised across the frame, before the divide
    const t = Math.pow(rand(), 0.72);
    const zFar = Z_FAR + SAG * u * u;
    const z = Z_NEAR + (zFar - Z_NEAR) * t;
    const k = CAM_Z / (CAM_Z - z * SPAN[2]);
    const lift = rand() < 0.12 ? Math.pow(rand(), 1.8) * 0.3 * (1 - t) : 0;
    a[i * 3] = (u * TARGET) / k;
    a[i * 3 + 1] = Y - DROP * t * t + lift + (rand() - 0.5) * 0.036;
    a[i * 3 + 2] = z;
  }
  return a;
}

function poolForm(ctx) {
  const { count, rand } = ctx;
  /* A bounded ellipse lying on the ground under the type, dense at its rim.

     `plane` reads by being unbounded; this reads by being finite, which is the
     same "differs by surface, not by silhouette" argument that put `plane`
     over `basin`, run the other way. It gets its ellipse from the projection
     rather than from a tilt constant.

     The near risk is the torus, since a bright rim around a darker middle is
     what a ring is. Two things separate them and both are worth checking on
     the render rather than believing here: the interior is populated rather
     than empty, and it sits low, at the same y as the plane. That second one
     also exempts it from the failure that bounds TORUS_TILT below 0.28 — a
     near-flat ring washes out because it lies inside the lozenge `uHole`
     fades, and this lies well under it. */
  const a = new Float32Array(count * 3);
  const Y = -0.9;
  /* Pushed back from -0.6 after the first render, where the near arc ran off
     the bottom of the mount and the pool read as a patch with no near side.
     Depth is what lifts a ground figure up the screen; moving it in y instead
     would have taken it toward the tagline. */
  const Z_C = -0.85; // centre depth
  const RX = 1.3; // ~2.6 on screen after SPAN
  const RZ = 1.5;
  const RIM_FRAC = 0.5; // share of points held in the rim band
  for (let i = 0; i < count; i++) {
    const th = rand() * Math.PI * 2;
    /* Rim band, or uniform by *area* inside it. sqrt is what makes the
       interior even: sampling r flat would pile the fill into the middle and
       hand the rim its contrast for the wrong reason. */
    const r =
      rand() < RIM_FRAC
        ? 0.84 + rand() * 0.16
        : Math.sqrt(rand()) * 0.86;
    const lift = rand() < 0.1 ? Math.pow(rand(), 1.8) * 0.26 : 0;
    a[i * 3] = Math.cos(th) * r * RX + (rand() - 0.5) * 0.05;
    a[i * 3 + 1] = Y + lift + (rand() - 0.5) * 0.04;
    a[i * 3 + 2] = Z_C + Math.sin(th) * r * RZ;
  }
  return a;
}

function ripplesForm(ctx) {
  const { count, rand } = ctx;
  /* Four concentric rings on the ground plane, spacing widening outward.

     Below and around at once, and the one candidate that is deliberately
     structure after structure has failed three times here. The defence is
     scale: helix, the network's rails and lattice all asked for spacing
     between *lines* at POINT_PX 21, and the closest two rings here are ~0.75
     screen units apart, which is on the order of 175px at a 1440 viewport. If
     this fails it will fail as a shape, not as a resolution problem.

     Radii stop at 1.72 because 1.85 puts the outer ring's flanks at ~3.2
     against the frame's 3.05 and crops the one edge the form is for. Points
     are dealt per ring in proportion to circumference, so the outer rings do
     not thin out into dashes — the failure TORUS_TUBE's lower bound is
     about. */
  const a = new Float32Array(count * 3);
  const Y = -0.9;
  const Z_C = -0.85; // pushed back for the same reason as `pool`'s
  /* 1.72 still cropped on the first render — the outer ring's flanks sit at
     the shallowest depth of any point in the figure, so they take the largest
     k and project wider than the arithmetic on the centre depth suggests. */
  const RADII = [0.36, 0.68, 1.06, 1.48];
  const TUBE = 0.055;
  const RZ = 1.15; // rings are wider across x than across z, matching the box
  let total = 0;
  for (let j = 0; j < RADII.length; j++) total += RADII[j];
  for (let i = 0; i < count; i++) {
    // Pick a ring with probability proportional to its circumference.
    let pick = rand() * total;
    let j = 0;
    while (j < RADII.length - 1 && pick > RADII[j]) {
      pick -= RADII[j];
      j++;
    }
    const th = rand() * Math.PI * 2;
    const r = RADII[j] + (rand() - 0.5) * 2 * TUBE;
    const lift = rand() < 0.08 ? Math.pow(rand(), 1.8) * 0.22 : 0;
    a[i * 3] = Math.cos(th) * r;
    a[i * 3 + 1] = Y + lift + (rand() - 0.5) * 0.035;
    a[i * 3 + 2] = Z_C + Math.sin(th) * r * RZ;
  }
  return a;
}

/* ---- second candidate round -------------------------------------------
   The first round cut everything that lay flat in the ground plane, `plane`
   included, on the grounds that the projection foreshortens any such figure
   into the same band. `horizon` and `murmuration` survived: one has a curved
   boundary the eye can trace, the other is asymmetric mass with no structure
   in it. These five come from those two theses, plus the one region of the
   frame nothing has ever used.

   ---- the constraint that placed three of them ----

   All five were positioned against `uHole` rather than against the type
   alone, because working the shader's own arithmetic turns out to say
   something the file has never written down. The fade is
   `length(ndc * vec2(0.42, 0.85))` ramped over 0.22..0.95, and the two axes
   are nothing like equally weighted. Vertical distance escapes it: a point at
   the bottom of the frame is at ndc.y -1 and comes out at 0.96 of full alpha.
   Horizontal distance essentially does not: 0.42 * 1.0 is 0.42 even at the
   frame's left or right edge, which is alpha 0.28, so a point on the frame's
   horizontal midline is muted almost wherever it sits.

   That is a much stronger statement than "the middle is quieter", and it is
   the real reason every form that has ever worked here lives low. It also
   kills a `beside` built as a standing figure out at high |x| — the belly of
   a sideways C at screen x -2.5 computes to alpha 0.21 — which is a cheaper
   way to learn it than another round of gate.

   So the ceiling on each of these is set by where its *subject* is, not by
   where the type is. The crest's peak, the crescent's horns and the wake's
   apex are each placed where they come out at ~0.45 to 0.55 of full alpha,
   and each is lower than the composition would otherwise want. */

function crestForm(ctx) {
  const { count, rand, SPAN, FILL_W } = ctx;
  /* Ground that rears into a breaking crest on one side: a long shallow face
     to the left, a hump lifting out of it to the right.

     The direct synthesis of the two survivors — `horizon`'s traceable curved
     boundary and `murmuration`'s asymmetry — on geometry already known to
     work.

     ---- why the lift is a function of depth and not only of x ----

     The obvious build, a ground plane whose height rises toward one side,
     cannot work, and the arithmetic says so before a render does. Screen y is
     Y * SPAN[1] * k, so the same world height arrives at a different height on
     screen at every depth: at the far edge the ground already sits at screen
     -0.72 and has nowhere to rise to, while at the near edge it sits at -1.34
     and a rise large enough to read there puts the far points up at -0.41,
     inside the chips. A lift applied across all depths at once cannot be both
     visible near and clear of the type far.

     So the crest is localised in depth as well as across the frame — a hump at
     one distance, which is what a real crest is anyway. It peaks at t 0.32,
     where the ground sits at screen -1.05 and the peak lands at -0.75. That
     last number is the `uHole` ceiling above: at the crest's own x, -0.75 is
     alpha 0.53 and -0.30 would have been 0.30. */
  const a = new Float32Array(count * 3);
  const Y = -0.86;
  const Z_NEAR = 0.7;
  const Z_FAR = -2.1;
  const TARGET = FILL_W;
  const START = 0.4; // where across the frame the ground begins to lift, in u
  /* 0.245 on the first render and the hump was invisible — the surface read
     as `plane` with a slightly soft right side. Two things were wrong and only
     one of them was the height. See the `t` sampling below for the other. */
  const RISE = 0.36; // peak height above the floor, in form units
  const T_PEAK = 0.32; // depth the crest breaks at
  const T_SPAN = 0.3; // how much depth it occupies either side of that
  const CURL = 0.42; // how far the lip leans toward the viewer
  for (let i = 0; i < count; i++) {
    /* Two fifths of the points are dealt into the crest's own span so the
       hump has mass rather than only an outline. The rest spread over the
       whole width, which is what keeps the shallow face reading as the same
       surface the crest rises out of. */
    const inCrest = rand() < 0.4;
    const u = inCrest ? START + (1 - START) * rand() : rand() * 2 - 1;
    /* The ground's points bias far, which is what gives every form in this
       family a dense top boundary. The crest's own points must not: the hump
       lives at t 0.32 and the far bias deals almost nothing there, so the
       first render built a crest and then starved it of the points that would
       have drawn it. That was the larger half of why it did not read. Its
       share is dealt around T_PEAK instead, which is also the only way the
       near face gets enough grains to be a face. */
    const t = inCrest
      ? Math.min(1, Math.max(0, T_PEAK + (rand() + rand() - 1) * T_SPAN * 0.8))
      : Math.pow(rand(), 0.72);
    const across = Math.max(0, (u - START) / (1 - START));
    const dt = Math.abs(t - T_PEAK) / T_SPAN;
    const along = dt < 1 ? Math.pow(Math.cos(dt * Math.PI * 0.5), 1.5) : 0;
    const lift = RISE * Math.pow(across, 1.4) * along;
    // The lip leans forward as it lifts, so the crest has a near face.
    const z = Z_NEAR + (Z_FAR - Z_NEAR) * t + CURL * lift;
    const k = CAM_Z / (CAM_Z - z * SPAN[2]);
    a[i * 3] = (u * TARGET) / k;
    a[i * 3 + 1] = Y + lift + (rand() - 0.5) * 0.036;
    a[i * 3 + 2] = z;
  }
  return a;
}

function crescentForm(ctx) {
  const { count, rand } = ctx;
  /* A shallow arc across the lower frame, thick at the belly and tapering to
     points at both ends, with air underneath it.

     `horizon`'s thesis taken as far as it goes: outline and nothing else. It
     stands in the xy plane rather than lying in the ground plane, which is
     what `pool` did wrong — a bounded figure on the floor is foreshortened
     into a band, and a bounded figure facing the camera is not.

     ---- why it is this shallow, and what it has to be told apart from ----

     The arc wants to be deeper. Horns at screen -0.35 would make a proper
     crescent, and at that height they compute to alpha 0.29, so the taper —
     the entire identifying feature — fades out exactly where it happens.
     Dropping them to -0.75 brings that to 0.53 and costs the arc its depth:
     67 degrees of circle rather than 103.

     Which lands it close to `basin`, rejected for reading as a second wave.
     The differences are real but they are differences of *edge*, so this is
     the candidate most likely to be settled by looking rather than arguing.
     basin is a filled parabola with its thickness hanging downward and no
     lower boundary at all; this is a band with two edges, empty space beneath
     the belly, and ends that taper to nothing instead of running out of the
     frame. Asymmetry is the other separator, and it is in arc length rather
     than in height — one horn reaches further round than the other. Lifting a
     horn instead would put it back on the muted midline. */
  const a = new Float32Array(count * 3);
  const Z_C = -0.4;
  const CY = 1.5214; // centre of the circle, above the frame
  const R = 2.5974;
  const TH_L = -0.63; // the longer horn
  const TH_R = 0.585;
  const TUBE = 0.22; // radial half-thickness at the belly
  for (let i = 0; i < count; i++) {
    const th = TH_L + (TH_R - TH_L) * rand();
    const end = th < 0 ? TH_L : TH_R;
    // Tapered: thickness goes to zero at whichever end this point is nearer.
    const w = TUBE * Math.pow(1 - Math.abs(th / end), 0.5);
    const r = R + (rand() - 0.5) * 2 * w;
    a[i * 3] = -Math.sin(th) * r;
    a[i * 3 + 1] = CY - Math.cos(th) * r;
    // A little depth, or a shell of points on an exact curve reads as a wire.
    a[i * 3 + 2] = Z_C + (rand() - 0.5) * 0.36;
  }
  return a;
}

function plumeForm(ctx) {
  const { count, rand } = ctx;
  /* A column of density rising out of the bottom edge in the left margin,
     spreading and thinning as it climbs.

     The first `beside` since `gate`, and built the way `gate` was not. Two
     bars read as a UI frame because they had edges and symmetry; this has
     neither. It is `murmuration`'s thesis — distribution as the whole subject
     — stood upright and moved out of the way of the type.

     It is also the one form here that *wants* the centre fade. Its base sits
     at alpha 0.96 and its top at 0.33, so `uHole` does the dissipation for
     free, in the same way the torus's own hole lines up with the lozenge. The
     top is dim because it is meant to be.

     Left rather than right because the headshot is upper-right, and a plume
     climbing into it would read as smoke off a portrait. */
  const a = new Float32Array(count * 3);
  const X_C = -1.107; // ~-2.05 on screen, clear of the bio card's -1.40
  const Y_BASE = -1.224; // below the frame edge, so it enters rather than starts
  const Y_TOP = 0.506;
  for (let i = 0; i < count; i++) {
    /* Biased to the base, but far less than the first render, which put a
       solid white lozenge at the foot of the plume.

       That is the POINT_PX failure arriving by a road the file has not
       written down. The documented version is grains too large: additive
       points that overlap sum toward white, so an opaque blob appears where
       the cloud is meant to be a texture. Point *size* is not the only way to
       get overlap. A base half-width of 0.07 is 33px on screen, and biasing
       the length by an exponent of 1.7 dealt a large share of the cloud into
       that column, which sums to white just as effectively as one grain drawn
       at four times the diameter. Density is the second lever on the same
       failure, and this form is the first one here dense enough to find it.

       Both numbers move: the base is 0.26 wide, which is ~120px, and the
       bias comes down to 1.25. The shape is the same and the foot is a
       texture rather than a lozenge. */
    const s = Math.pow(rand(), 1.25);
    const halfw = 0.26 + 0.24 * Math.pow(s, 0.8);
    // Leaning out as it climbs, plus a slow wander, so it is not a cone.
    const cx = X_C - 0.15 * s + 0.09 * Math.sin(s * 5.5);
    const g = () => (rand() + rand() + rand() - 1.5) * 0.66;
    a[i * 3] = cx + g() * halfw;
    a[i * 3 + 1] = Y_BASE + (Y_TOP - Y_BASE) * s + (rand() - 0.5) * 0.05;
    a[i * 3 + 2] = -0.3 + g() * 0.35;
  }
  return a;
}

function wakeForm(ctx) {
  const { count, rand } = ctx;
  /* Two arms diverging from a point in the distance toward the near corners,
     with a sparse disturbance filling the wedge between them.

     `rift`'s geometry rebuilt as mass instead of absence. The converging-lines
     composition was worth having and the negative-space version could not hold
     it: at any gap wide enough to see, the two banks sat against the frame
     edges and became `gate`. Drawn as mass the arms cannot separate into two
     objects, because the thing between them is the figure and not the ground.

     The fill is the load-bearing third of it. Two streaks alone are two
     streaks, and a fifth of the points scattered across the wedge is what
     makes them one wake.

     Its depth range is short — 0.25 to -1.6, against the ground forms' 0.7 to
     -2.1 — and its floor is lower, at -1.02. Both come from the apex. Run back
     to -2.1 the apex sits at screen -0.75 and computes to alpha 0.29, which is
     the one point in the figure that cannot afford to be dim. Pulling it
     forward and dropping the floor brings it to 0.45, and the near end then
     has to be reined in to -1.39 so it does not fall out of the frame. */
  const a = new Float32Array(count * 3);
  const Y = -1.02;
  const Z_NEAR = 0.25;
  const Z_FAR = -1.6;
  const APEX = 0.15; // half-separation of the arms where they meet
  const SPREAD = 0.85; // extra half-separation at the near end
  /* Down from 0.2, and the arms thinned to match. The first render came out as
     one lumpy diagonal band rather than as two arms: the fill was carrying
     enough points to close the wedge it was only supposed to suggest, and
     arms 0.33 thick at the near end met it in the middle. The wake needs the
     space between the arms to be visibly emptier than the arms. */
  const FILL = 0.12; // share of points in the wedge rather than on the arms
  for (let i = 0; i < count; i++) {
    const s = Math.pow(rand(), 1.15); // mild bias to the apex
    const z = Z_FAR + (Z_NEAR - Z_FAR) * s;
    const arm = APEX + SPREAD * Math.pow(s, 1.15);
    const side = i % 2 === 0 ? -1 : 1;
    let x;
    if (rand() < FILL) {
      x = (rand() * 2 - 1) * arm; // anywhere across the wedge
    } else {
      // Thickening toward the near end, where the disturbance has spread.
      const w = 0.04 + 0.17 * s;
      x = side * arm + (rand() - 0.5) * 2 * w;
    }
    a[i * 3] = x;
    a[i * 3 + 1] = Y + (rand() - 0.5) * 0.07;
    a[i * 3 + 2] = z + (rand() - 0.5) * 0.12;
  }
  return a;
}

function canopyForm(ctx) {
  const { count, rand, SPAN, FILL_W } = ctx;
  /* Mass hanging from the top of the frame, its underside arching up over the
     middle so the name sits inside the arch.

     The one placement the file has never used. Measuring where the hero type
     sits established that mass has three places to go — below, around, beside
     — and above was not among them because the type reaches y +1.34 of ±1.47.
     That measurement is right about the centre and wrong about the ends: out
     past |x| 1.9 the frame is clear from the top all the way down to the bio
     card, which is the gap an arch fits through and a band does not.

     Its underside is the subject, so the points are dealt toward that boundary
     rather than toward the top edge — the same choice that gives `horizon` a
     crisp far edge instead of a haze. Filling downward from the frame edge
     would put the density where nothing is being described.

     The ends are at screen +0.72 and not lower for the usual reason: at +0.45
     they compute to alpha 0.39 and the arch fades out at both feet, which is
     the whole shape. +0.72 brings that to 0.58. The centre of the underside
     clears the headshot's +1.24 by a little and the name's +0.94 by more.

     Highest risk of the five by a distance, and the risk is not subtle: this
     is the only candidate that puts mass above the headline, where it can
     crowd the name rather than sit under it. */
  const a = new Float32Array(count * 3);
  const TARGET = FILL_W;
  const MID = 1.0; // underside at the centre, in form units (~+1.28 on screen)
  const ENDS = 0.5625; // underside at the frame edges (~+0.72)
  const TOP = 1.266; // where the mass stops (~+1.62, just off frame)
  for (let i = 0; i < count; i++) {
    const u = rand() * 2 - 1;
    const z = -0.5 + rand() * 0.9;
    const k = CAM_Z / (CAM_Z - z * SPAN[2]);
    const floor = MID - (MID - ENDS) * u * u;
    /* Half the points into a band on the boundary itself, the rest spread up
       to the frame edge. Without the band the arch is a gradient and stops
       being a line the eye can follow. */
    const v = rand() < 0.5 ? rand() * 0.22 : rand();
    a[i * 3] = (u * TARGET) / k;
    a[i * 3 + 1] = floor + (TOP - floor) * v + (rand() - 0.5) * 0.04;
    a[i * 3 + 2] = z;
  }
  return a;
}

function terracesForm(ctx) {
  const { count, rand, SPAN, FILL_W } = ctx;
  /* Three filled shelves at rising heights and increasing depths, each one a
     frame-filling plane, with the dark bands between them as the subject.

     Also structure, and it dodges the resolution failure a different way to
     `ripples`: every element is a *slab*, so nothing here needs spacing
     between lines, only between surfaces. Thin the slabs and this becomes the
     lattice failure exactly.

     All three shelves are at the same y, and the separation is entirely in
     depth. The first render stepped them in y as well and the terracing did
     not read: a shelf raised in y and pushed back in z is lifted twice, so
     the far one climbed into the tagline while the gaps between them stayed
     the same size. Level shelves let the 1/z divide do all of it — the near
     one spans screen y -1.37..-1.12, the far one -0.80..-0.72, and each dark
     band is around half the height of the shelf below it. The whole figure
     then lives between -1.37 and -0.72 against a frame bottom of -1.47, which
     is the envelope `plane` already occupies. */
  const a = new Float32Array(count * 3);
  const TARGET = FILL_W;
  // [y, near z, far z] per shelf, near to far.
  const SHELVES = [
    [-0.9, 0.62, -0.1],
    [-0.9, -0.55, -1.2],
    [-0.9, -1.7, -2.35],
  ];
  for (let i = 0; i < count; i++) {
    const s = SHELVES[i % SHELVES.length];
    const t = Math.pow(rand(), 0.72);
    const z = s[1] + (s[2] - s[1]) * t;
    const k = CAM_Z / (CAM_Z - z * SPAN[2]);
    const lift = rand() < 0.1 ? Math.pow(rand(), 1.8) * 0.22 * (1 - t) : 0;
    a[i * 3] = (rand() * 2 - 1) * (TARGET / k);
    a[i * 3 + 1] = s[0] + lift + (rand() - 0.5) * 0.036;
    a[i * 3 + 2] = z;
  }
  return a;
}

function murmurationForm(ctx) {
  const { count, rand } = ctx;
  /* A swept teardrop swarm: a dense head low and to the left, tapering into a
     tail that rises and recedes to the right.

     The one candidate with no structure at all. Density is the channel that
     demonstrably survives here — it is what carries `plane`'s depth once the
     outline stops providing any — and this is the form that uses nothing else.
     It is also the only asymmetric form in the file. Every other one is
     mirrored about x 0, and asymmetry is a larger compositional difference
     than most changes of shape, which is the argument that put `basin` over
     another ring.

     Its way of failing is `sphere`'s: an object with no outline was a pause in
     the cycle rather than a form in it. The taper is what is supposed to buy
     an outline, so if this reads as a blob, that is the answer and not a
     tuning problem. */
  const a = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    /* Biased toward the head, which is where the form's mass has to be, but
       only just. At 1.6 on the first render the tail was starved: the taper
       is what gives this its outline, and a tail too faint to see leaves a
       blob with a wisp, which is the `sphere` failure this form has to avoid.
       The exponent and the radius floor below are the two numbers that trade
       head density against whether there is a tail at all. */
    const s = Math.pow(rand(), 1.15);
    const cx = -1.15 + 2.7 * s;
    const cy = -0.86 + 0.62 * Math.pow(s, 1.5);
    const cz = 0.5 - 1.6 * s;
    const r = 0.2 + 0.42 * Math.pow(1 - s, 0.6);
    // Three rolls summed: a rounded falloff, so the swarm has no hard edge.
    const g = () => (rand() + rand() + rand() - 1.5) * 0.66;
    a[i * 3] = cx + g() * r;
    a[i * 3 + 1] = cy + g() * r * 0.72;
    a[i * 3 + 2] = cz + g() * r;
  }
  return a;
}

function basinForm(ctx) {
  const { count, rand } = ctx;
  /* A bowl: the floor runs deep under the type and lifts into the side
     margins, so the type sits *in* something rather than on top of it. Built,
     tuned, and out of the cycle: on screen it read as a second wave, which is
     the risk that was flagged when it landed. Kept for the same reason gate is.

     The wave's relationship to the type is contact along a single edge; this
     one surrounds it on three sides without closing into the torus's ring.

     The parabola is what keeps it clear. At the tagline's edge (u 0.567) the
     floor is at -0.582, below the tagline's -0.53, and it only descends from
     there inward. */
  const a = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const u = rand() * 2 - 1;
    const floor = -1.08 + 1.55 * u * u;
    // Thickness hangs downward from the floor line, densest at the line.
    const t = Math.pow(rand(), 0.7);
    a[i * 3] = u * 1.5 + (rand() - 0.5) * 0.12;
    a[i * 3 + 1] = floor - t * 0.3 + (rand() - 0.5) * 0.06;
    a[i * 3 + 2] = (rand() * 2 - 1) * 0.5;
  }
  return a;
}

function galaxyForm(ctx) {
  const { count, rand } = ctx;
  /* Two logarithmic arms and a bulge, in the xy plane so it faces the camera.
     The slow spin carries it to edge-on and back over the cycle, which is the
     one form here that gains from the rotation rather than merely surviving
     it. It also rhymes with the nebula already on the page. */
  const a = new Float32Array(count * 3);
  const arms = 2;
  const tightness = 2.6;
  for (let i = 0; i < count; i++) {
    const t = (i + 0.5) / count;
    if (t < 0.22) {
      // Bulge. Without a dense centre a spiral reads as loose streaks.
      const rr = 0.34 * Math.pow(rand(), 0.6);
      const th = rand() * Math.PI * 2;
      const ph = Math.acos(2 * rand() - 1);
      a[i * 3] = rr * Math.sin(ph) * Math.cos(th) * 1.3;
      a[i * 3 + 1] = rr * Math.sin(ph) * Math.sin(th) * 1.3;
      a[i * 3 + 2] = rr * Math.cos(ph) * 0.7;
      continue;
    }
    const u = (t - 0.22) / 0.78;
    const arm = (i % arms) * ((Math.PI * 2) / arms);
    const rr = 0.22 + u * 1.05;
    // Scatter across the arm, widening outward the way real arms do.
    const th = Math.log(rr / 0.22) * tightness + arm + (rand() - 0.5) * (0.42 + u * 0.5);
    a[i * 3] = Math.cos(th) * rr * 1.28;
    a[i * 3 + 1] = Math.sin(th) * rr * 1.28;
    a[i * 3 + 2] = (rand() - 0.5) * (0.16 + 0.1 * (1 - u)); // thin disc
  }
  return a;
}

function waveForm(ctx) {
  const { count, rand, SPAN, FILL_W } = ctx;
  /* A rippled surface receding from the viewer, sitting below the type.

     A different family from the rings entirely: not an object in the middle of
     the frame but a *ground* under it, which is the one composition the
     header's shape actually asks for. It is also the only form here that gets
     depth from perspective rather than from the shader's distance fade, since
     a plane running away from the camera is foreshortened by the projection
     itself.

     Its width is solved per depth, the way `planeForm` documents. It used to
     be a fixed 1.62 at every depth, which is a rectangle in world space and a
     trapezoid on screen: the far edge reached only 2.45 of the frame's 3.43
     and left the back of the header empty, while the near edge ran to 4.75 and
     spent points outside the frustum. Both ends were wrong in opposite
     directions, which is why the shortfall was never obvious — the near edge
     looked like it was covering. The ripple phase still keys off the world x,
     so the wavelength stays constant in world space and the ripples read
     finer with distance, which is what perspective does to them anyway. */
  const a = new Float32Array(count * 3);
  const cols = Math.round(Math.sqrt(count * 2.6));
  for (let i = 0; i < count; i++) {
    const cx = i % cols;
    const cz = Math.floor(i / cols);
    const rows = Math.ceil(count / cols);
    // Jittered lattice, not a strict grid: an exact grid at this point size
    // produces moire against the pixel grid as it turns.
    const u = (cx + 0.5 + (rand() - 0.5) * 0.7) / cols;
    const v = (cz + 0.5 + (rand() - 0.5) * 0.7) / Math.max(1, rows);
    const z = (v * 2 - 1) * 1.25;
    const k = CAM_Z / (CAM_Z - z * SPAN[2]);
    const x = ((u * 2 - 1) * FILL_W) / k;
    // Two crossed waves at different rates, so the surface never reads as one
    // repeating corrugation.
    const h = 0.3 * Math.sin(x * 2.1 + z * 0.9) + 0.16 * Math.cos(z * 2.6 - x * 0.7);
    a[i * 3] = x;
    a[i * 3 + 1] = h - 0.62; // dropped below the headline
    a[i * 3 + 2] = z;
  }
  return a;
}

function networkForm(ctx) {
  const { count, rand } = ctx;
  /* Four layers of nodes with their connections drawn as points: the one form
     in the file that is *about* anything, and the only one whose subject
     matches what the page is for.

     Two thirds of the points go on the edges rather than the nodes. Nodes
     alone read as four vertical smudges; it is the connections that make it a
     network, exactly as in the skill figure further down the page. */
  const a = new Float32Array(count * 3);
  const layers = 4;
  /* Symmetric front to back as well as top to bottom. It was [5, 7, 7, 4],
     which is what a real architecture looks like and what a diagram of one
     does not: an almost-symmetric figure is read as a botched symmetric one
     rather than as an accurate asymmetric one, so the asymmetry cost accuracy
     nothing and legibility a lot. */
  const perLayer = [4, 7, 7, 4];
  const nodes = [];
  const byLayer = [];
  for (let l = 0; l < layers; l++) {
    const x = (l / (layers - 1) - 0.5) * 2.6;
    byLayer.push([]);
    for (let n = 0; n < perLayer[l]; n++) {
      /* Full height on purpose, and two narrower variants were tried and
         rejected. The headline occupies the centre of this header, so it
         masks whatever sits behind it: compressing the layers into a tight
         band puts *more* of the form behind the type, not less, and the
         version at 1.18 came out fainter than this one. Dropping that band
         below the headline instead, which is what makes the wave work, only
         got it clipped by the masthead's bottom edge and reading as a worse
         wave. The nodes have to reach past the type to be seen at all. */
      const y = (n / Math.max(1, perLayer[l] - 1) - 0.5) * 1.62;
      /* Planar, where every node used to take a random z of up to +-0.25.
         That scatter was the largest single source of the wrongness: it
         breaks the mirror in the one axis the viewer cannot measure, so the
         figure looked subtly off without looking like anything in particular.
         A diagram of a network is drawn flat. Depth still comes from the
         scatter inside each node blob and from the swing turning the whole
         plane, which is a better kind of depth here because it is coherent. */
      const nd = [x, y, 0, l];
      nodes.push(nd);
      byLayer[l].push(nd);
    }
  }

  /* A fixed, sparse edge list, built once, rather than a fresh random pair per
     point.

     Drawing a point on a randomly chosen pair means every adjacent pair gets
     drawn: 5*7 + 7*7 + 7*4 is 112 edges sharing the edge budget, about 60
     points each spread along a line that is nearly two units long once SPAN
     has stretched it. That is well under the density at which a line of
     additive grains reads as a line, so the figure came out as a uniform haze
     with four bright smudges in it, which is exactly the "I had no idea what
     it was" failure. Three outgoing edges per node is ~50 edges at more than
     twice the density each, and sparsity is what a network diagram looks like
     anyway: the eye needs to be able to follow one connection. Two per node
     rather than three after the first pass still read as haze: the count came
     down again and the node share with it, so the same budget concentrates on
     ~38 edges. Note that point count was never the main lever here. Its `hole`
     was.

     The picks were random, which is the other half of why this looked wrong.
     Sparsity fixed the density but a random graph is not what anyone pictures
     when they picture a network, and no amount of tuning a random draw makes
     it symmetric.

     So connectivity is worked out for the upper half of each layer only and
     then mirrored onto the lower half, which makes the symmetry structural
     rather than something the seed has to be lucky enough to produce.

     Within the half, a node reaches for the next layer at its own *normalised*
     position plus and minus EDGE_SPREAD, so layers of different sizes still
     line up. The spread is the point. Connecting each node to its two nearest
     neighbours instead is also symmetric, and was tried and rejected: nearest
     neighbours are all nearly horizontal, so the figure came out as seven
     parallel rails with no crossings, which is tidy and is not a network. What
     makes a network diagram legible as one is edges that fan out from a node
     and cross the ones fanning out from its neighbours.

     Deduped, since the mirror of a centre node's edge is often that same
     edge. */
  const edges = [];
  for (let l = 0; l < layers - 1; l++) {
    const A = byLayer[l];
    const B = byLayer[l + 1];
    const seen = {};
    for (let i = 0; i < A.length; i++) {
      const pa = A.length === 1 ? 0.5 : i / (A.length - 1);
      if (pa > 0.5 + 1e-6) continue; // the lower half arrives as a mirror
      const targets = [pa - EDGE_SPREAD, pa + EDGE_SPREAD];
      for (let e = 0; e < targets.length; e++) {
        // Nearest node in the next layer to where this one is reaching.
        let j = 0;
        let best = Infinity;
        for (let q = 0; q < B.length; q++) {
          const pb = B.length === 1 ? 0.5 : q / (B.length - 1);
          const dd = Math.abs(pb - targets[e]);
          if (dd < best) {
            best = dd;
            j = q;
          }
        }
        const pair = [
          [i, j],
          [A.length - 1 - i, B.length - 1 - j],
        ];
        for (let p = 0; p < pair.length; p++) {
          const key = pair[p][0] + ":" + pair[p][1];
          if (seen[key]) continue;
          seen[key] = 1;
          edges.push([A[pair[p][0]], B[pair[p][1]]]);
        }
      }
    }
  }

  const nodeShare = Math.floor(count * 0.26);
  for (let i = 0; i < count; i++) {
    if (i < nodeShare) {
      const nd = nodes[i % nodes.length];
      const rr = 0.075 * Math.pow(rand(), 0.5);
      const th = rand() * Math.PI * 2;
      const ph = Math.acos(2 * rand() - 1);
      a[i * 3] = nd[0] + rr * Math.sin(ph) * Math.cos(th);
      a[i * 3 + 1] = nd[1] + rr * Math.sin(ph) * Math.sin(th);
      a[i * 3 + 2] = nd[2] + rr * Math.cos(ph);
      continue;
    }
    /* Round robin over the edge list, not a random draw from it. Random
       assignment leaves some edges a third brighter than others purely by
       luck, and an uneven edge is read as a meaningful one. */
    const ed = edges[(i - nodeShare) % edges.length];
    const t = rand();
    a[i * 3] = ed[0][0] + (ed[1][0] - ed[0][0]) * t + (rand() - 0.5) * 0.03;
    a[i * 3 + 1] = ed[0][1] + (ed[1][1] - ed[0][1]) * t + (rand() - 0.5) * 0.03;
    a[i * 3 + 2] = ed[0][2] + (ed[1][2] - ed[0][2]) * t + (rand() - 0.5) * 0.03;
  }
  return a;
}

function constellationForm(ctx) {
  const { count, rand, skills, nGroups, perLobe } = ctx;
  /* One lobe per skill, lobes gathered by discipline. Disciplines are spread
     around a ring and their skills sit in a shell around that centre, which
     is the same shape the network below the page settles into: clusters, held
     apart, connected by the space between them. */
  const a = new Float32Array(count * 3);
  const centres = [];
  for (let g = 0; g < nGroups; g++) {
    const th = (g / nGroups) * Math.PI * 2;
    centres.push([Math.cos(th) * 1.15, Math.sin(th) * 0.62, Math.sin(th * 1.7) * 0.5]);
  }
  // Kept so `frame` can work out how far each lobe would have to lean to
  // reach its cluster. Only this form has lobes, so only this form fills it,
  // and an empty list is what disables the whole behaviour on the header.
  ctx.lobeCentres = centres;
  let s = 0;
  for (let lobe = 0; lobe < skills.length; lobe++) {
    const c = centres[skills[lobe]];
    // Where this skill sits inside its discipline's cluster.
    const off = [(rand() - 0.5) * 0.62, (rand() - 0.5) * 0.5, (rand() - 0.5) * 0.5];
    for (let k = 0; k < perLobe; k++, s++) {
      /* Lobe radius. This was 0.1 while the constellation was one of three
         forms cycling in the header, where it was seen for nine seconds and
         tight lobes read as stars. Standing still behind the network it has
         to hold up under a long look, and at 0.1 it read as six separate
         piles of speckles with dead space between them rather than as one
         cloud with dense places in it. Wide enough that neighbouring lobes
         touch is the difference between grain and dust. */
      const rr = 0.2 * Math.pow(rand(), 0.5);
      const th = rand() * Math.PI * 2;
      const ph = Math.acos(2 * rand() - 1);
      a[s * 3] = c[0] + off[0] + rr * Math.sin(ph) * Math.cos(th);
      a[s * 3 + 1] = c[1] + off[1] + rr * Math.sin(ph) * Math.sin(th);
      a[s * 3 + 2] = c[2] + off[2] + rr * Math.cos(ph);
    }
  }
  return a;
}

export function mountCloud(opts) {
  const canvas = opts.canvas;
  const host = canvas.parentElement;
  const cfg = PRESETS[opts.preset] || PRESETS.header;
  const SPAN = cfg.span;

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: false });
  } catch (e) {
    return null; // no context: app.js drops the element
  }
  // Additive points over a header-sized area are fill-rate bound, so the pixel
  // ratio is capped below the crisp-text threshold on purpose. Nothing here has
  // an edge sharp enough to show it.
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
  renderer.setClearAlpha(0);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 3, 0.1, 100);
  camera.position.set(0, 0, CAM_Z);

  /* How wide the frame actually is, in form units, for the forms built to fill
     it. Measured off the camera and the mount instead of assumed.

     Every frame-filling builder used to hardcode a half-width of 3.05 world
     units, on the note that "at z 4.4 and a 38 degree fov the half-width is
     ~3.1". The fov is *vertical*, so that was only ever true at one aspect
     ratio, and not the one the mount has: at 1440px the box is 1631 by 720, and
     the real half-width is 3.43. A ground plane built to 3.05 therefore stopped
     0.38 units short on each side and drew a hard vertical edge ~50px inside
     the canvas. That edge, not the canvas boundary, is the border that was
     visible in every render of this family.

     Deriving it also means the CSS above and this file can no longer disagree.
     Widening the mount now widens the forms to match, where before it would
     have moved the canvas edge out and left the forms' own edge exactly where
     it was, making the border *more* obvious rather than less.

     OVERRUN is on top of that, so the ground runs off the sides rather than
     ending flush with them: an edge landing exactly on the boundary is still an
     edge if anything ever nudges the two apart. It also covers a resize, since
     forms are built once at mount and never rebuilt — a window widened by up to
     12% keeps its cover, and past that the mask has the rest.

     In form units, i.e. before SPAN multiplies them, which is why SPAN[0] is
     divided back out here. */
  const OVERRUN = 1.12;
  const FRAME_H = CAM_Z * Math.tan((camera.fov * Math.PI) / 360);
  const FRAME_W =
    (FRAME_H * (host.clientWidth || 1)) / Math.max(1, host.clientHeight || 1);
  const FILL_W = (FRAME_W * OVERRUN) / SPAN[0];

  const cloud = new THREE.Group();
  scene.add(cloud);

  /* ---- who each point belongs to -------------------------------------- */
  const groups = opts.groups.length ? opts.groups : [["Skills", ["one", "two", "three"]]];
  const nGroups = Math.min(groups.length, 6);
  const skills = []; // {group}
  groups.forEach(function (g, gi) {
    (g[1] || []).forEach(function () {
      skills.push(gi % nGroups);
    });
  });
  if (!skills.length) skills.push(0);

  const N = Math.max(MIN_POINTS, Math.min(MAX_POINTS, skills.length * PER_SKILL));
  const perLobe = Math.ceil(N / skills.length);
  const count = perLobe * skills.length;

  const ownerGroup = new Uint8Array(count);
  for (let i = 0; i < count; i++) ownerGroup[i] = skills[Math.floor(i / perLobe)];

  /* ---- the forms this mount shows --------------------------------------
     The builders live at module scope and take this ctx, because everything
     they need from a mount is these five numbers plus the seeded PRNG. Hoisting
     them out is what lets one registry entry hold a form's builder *and* its
     tuning; while they were closures the two had to live in separate maps keyed
     by the same string. */
  const ctx = {
    count: count,
    // Deterministic: the cloud has to look the same on every load, or a reload
    // silently becomes a different composition. See rng().
    rand: rng(0x5eed),
    SPAN: SPAN,
    FILL_W: FILL_W,
    // Only the constellation reads these, and only to put one lobe per skill.
    skills: skills,
    nGroups: nGroups,
    perLobe: perLobe,
    lobeCentres: [], // written by constellationForm, in pre-SPAN units
  };
  // The per-point seeds below are drawn from the same stream as the forms, and
  // deliberately: one PRNG per mount is what makes a reload identical, and two
  // would drift apart the moment either side changed how many rolls it takes.
  const rand = ctx.rand;

  /* Only the forms this mount actually shows are built. A single-form mount
     never morphs, so building the rest would be allocating more Float32Arrays
     of `count * 3` to never read them. */
  const names = resolveForms(opts.forms, cfg.forms);
  const forms = names.map(function (name) {
    return FORMS[name].build(ctx);
  });
  /* SPAN, but a form may resist part of it.

     The stretch exists so the cloud fills a wide box and reads as a surface the
     text sits on rather than a ball dropped behind it, and for a sphere or a
     knot that costs nothing: their interest is the silhouette, and a stretched
     silhouette is still a silhouette. A form whose interest is *internal*
     structure is a different case. At the full 2.0 the winding of an attractor
     and the arms of the star are smeared sideways until both read as the same
     oval band as the sphere, which defeats the point of having four forms.

     The registry's `aspect` divides the stretch back out per axis, per form. Not
     to 1: at no stretch these sit as a ball in the middle of a wide header,
     which is the composition the span was introduced to fix. Partway is the
     trade. */
  forms.forEach(function (f, fi) {
    const asp = formMeta(names[fi]).aspect;
    for (let i = 0; i < count; i++) {
      f[i * 3] *= SPAN[0] * asp[0];
      f[i * 3 + 1] *= SPAN[1] * asp[1];
      f[i * 3 + 2] *= SPAN[2] * asp[2];
    }
  });
  // The lobe centres are positions in the same space, so they take the same
  // scaling. Missing this puts the lean's origin somewhere the points are not.
  const lobeCentres = ctx.lobeCentres.map(function (c) {
    return new THREE.Vector3(c[0] * SPAN[0], c[1] * SPAN[1], c[2] * SPAN[2]);
  });
  const morphs = forms.length > 1;

  /* ---- geometry --------------------------------------------------------- */
  const geo = new THREE.BufferGeometry();
  const from = new THREE.BufferAttribute(forms[0].slice(), 3);
  // With one form the shader still mixes from->to every frame; pointing both at
  // the same positions makes that mix a no-op rather than a special case.
  const to = new THREE.BufferAttribute((morphs ? forms[1] : forms[0]).slice(), 3);
  const color = new THREE.BufferAttribute(new Float32Array(count * 3), 3);
  const seed = new THREE.BufferAttribute(new Float32Array(count * 2), 2);
  const groupAttr = new THREE.BufferAttribute(Float32Array.from(ownerGroup), 1);
  for (let i = 0; i < count; i++) {
    // Size is skewed, not uniform: squaring the roll leaves most points as fine
    // grain and a few as bright motes. An even spread gives every point the
    // same weight, which is what makes a particle field look like static.
    const r = rand();
    seed.setXY(i, rand(), 0.5 + 1.5 * r * r);
  }
  geo.setAttribute("position", from); // never read by the shader; three wants it for the draw range
  geo.setAttribute("aFrom", from);
  geo.setAttribute("aTo", to);
  geo.setAttribute("aColor", color);
  geo.setAttribute("aSeed", seed);
  geo.setAttribute("aGroup", groupAttr);
  geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 4); // fixed: the points move every frame

  const material = new THREE.ShaderMaterial({
    uniforms: {
      uT: { value: 0 },
      uTime: { value: 0 },
      uSize: { value: POINT_PX },
      uHole: { value: 0.12 },
      // Both mounts carry it because VERT is shared; only a `cfg.exit` mount
      // ever moves it off 0.
      uExit: { value: 0 },
      uOpacity: { value: 1 },
      uTint: { value: new THREE.Color(1, 1, 1) },
      uTintAmt: { value: 0 },
      // Six vec2s rewritten per frame, rather than count*2 floats of attribute.
      // Same reasoning as uTint: say it once and let the GPU spread it.
      uGroupPos: {
        value: [0, 1, 2, 3, 4, 5].map(function () {
          return new THREE.Vector2(0, 0);
        }),
      },
      uRamp: {
        value: [0, 1, 2, 3, 4, 5].map(function () {
          return new THREE.Color(1, 1, 1);
        }),
      },
      uRampAmt: { value: cfg.ramp || 0 },
      uMonoAmt: { value: 0 },
    },
    vertexShader: VERT,
    fragmentShader: FRAG,
    transparent: true,
    depthWrite: false,
    depthTest: false, // an unordered additive point cloud has nothing to occlude
  });
  const points = new THREE.Points(geo, material);
  points.frustumCulled = false;
  cloud.add(points);

  /* ---- theme ------------------------------------------------------------ */
  const tmp = new THREE.Color();
  const hsl = { h: 0, s: 0, l: 0 };
  let baseOpacity = 1;
  let baseHole = 1;
  let holeNow = -1; // eased toward baseHole * the form's `hole`; -1 snaps on frame one
  let monoNow = -1; // same convention as holeNow
  let baseSize = cfg.point;
  let thin = 1; // eased filter response, so a legend click does not step
  let tintStr = null; // last parsed flash color, to skip re-parsing per frame
  function paint() {
    const light = opts.isLight();
    /* In this figure color already means discipline: every neuron in the
       network and every legend chip is tinted from --c1..--c6. A point cloud
       behind it carrying those same six hues puts discipline color behind
       discipline-colored nodes, which is the collision the nebula was just
       taken off the spectrum to avoid. So the panel cloud is painted in one
       off-scale hue and the *shape* carries the meaning: the constellation is
       still one lobe per skill, gathered by discipline. Structure from the
       geometry, meaning from nothing in the color. The header has no such
       encoding on top of it, so there the six hues are free to be used. */
    const mono = opts.mono && opts.mono();
    const pal = mono ? null : opts.palette();
    for (let i = 0; i < count; i++) {
      tmp.set(mono || pal[ownerGroup[i] % pal.length]);
      color.setXYZ(i, tmp.r, tmp.g, tmp.b);
    }
    color.needsUpdate = true;

    // The same palette the per-point colors come from, handed to the shader as
    // an ordered ramp. Repainted on a theme flip like everything else here.
    if (cfg.ramp) {
      const slots = material.uniforms.uRamp.value;
      const src = pal || [mono];
      for (let k = 0; k < slots.length; k++) {
        slots[k].set(src[k % src.length]);
        /* Pushed apart rather than used as-is. The six discipline colors were
           picked to sit together as a family, which is right where they label
           six things and wrong where they are a gradient: neighbouring stops
           come out close enough that the sweep reads as one muddy hue. Raising
           saturation and alternating lightness separates adjacent stops so the
           sweep is visible as a sweep. The hues themselves are untouched, so
           this is still recognisably the page's palette. */
        slots[k].getHSL(hsl);
        slots[k].setHSL(
          hsl.h,
          Math.min(1, hsl.s * 1.45),
          Math.min(0.86, Math.max(0.24, hsl.l + (k % 2 ? 0.1 : -0.07)))
        );
      }
    }

    /* Additive blending is how a point cloud glows, and it only glows against
       something dark: on a near-white page every additive point drives its
       pixel further towards white and the whole cloud disappears. The light
       theme composites normally instead, which makes the same points read as
       pigment on paper rather than as light. */
    material.blending = light ? THREE.NormalBlending : THREE.AdditiveBlending;
    // Held separately because `frame` scales it by the filter response every
    // tick; writing the uniform directly here would be overwritten anyway, and
    // a theme flip mid-flash would otherwise cancel the flash.
    baseOpacity = light ? cfg.opacity.light : cfg.opacity.dark;
    material.uniforms.uOpacity.value = baseOpacity;
    // Same reasoning as baseOpacity: the form's `hole` scales this every tick, so the
    // theme sets the base and `frame` owns the uniform.
    baseHole = light ? cfg.hole.light : cfg.hole.dark;
    material.uniforms.uHole.value = baseHole;
    material.needsUpdate = true;
  }
  paint();
  opts.onThemeChange(paint);

  /* ---- size ------------------------------------------------------------- */
  /* Declared here and not down in the motion block with the rest of the exit
     state, because `resize` writes them and `resize` runs during mount, well
     before that block is reached. A `let` further down is in its temporal dead
     zone at that point, so the first assignment throws a ReferenceError from
     inside mountCloud, which app.js catches and answers by removing the mount:
     the failure mode is not a broken exit, it is no cloud at all. */
  let exitFrom = 0; // px of scroll before the dispersal starts
  let exitSpan = 1; // px it is spread over after that
  function resize() {
    const w = host.clientWidth || 1;
    const h = host.clientHeight || 1;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    // Point size is in device pixels, so without this the cloud is visibly
    // coarser on a small header than on a wide one.
    // Held separately for the same reason as baseOpacity: the flash bloom
    // scales uSize every frame, so resize has to update the base it scales
    // rather than the uniform, or a resize mid-flash would cancel the bloom and
    // a bloom would survive the next resize.
    baseSize = cfg.point * renderer.getPixelRatio() * Math.min(1.35, Math.max(0.75, w / 1000));
    material.uniforms.uSize.value = baseSize;
    // Cached here rather than measured per scroll event: `h` is already in hand,
    // and reading it in the handler would put a layout flush on every wheel tick.
    exitFrom = h * EXIT_FROM;
    exitSpan = Math.max(1, h * (EXIT_TO - EXIT_FROM));
  }
  resize();
  if ("ResizeObserver" in window) new ResizeObserver(resize).observe(host);
  else window.addEventListener("resize", resize);

  /* ---- motion ------------------------------------------------------------ */
  let clock = 0;
  let phase = 0; // seconds into the current dwell+morph cycle
  let next = 2 % forms.length; // index of the form after the one in `to`
  let last = 0;
  let raf = 0;
  let onScreen = true;
  let first = true;
  // Only the panel integrates this; the header's yaw is a function of the clock.
  let spinY = 0;
  let swingAmp = 1; // eased between 1 and FACE_SWING, see `face` in the registry
  let px = 0; // pointer, in -1..1 across the viewport
  let py = 0;
  let camX = 0;
  let camY = 0;
  let scrolled = 0; // window.scrollY, sampled by the listener below
  let exitNow = 0; // eased toward the scroll position, drives uExit
  /* The loop is stopped while the header is off screen, so a scroll back up
     restarts it with `exitNow` holding whatever the value was when it stopped,
     which by then is a long way from where the scroll position now says it
     should be. Easing from there would play the gather as a visible catch-up on
     re-entry. So the first frame of any restart assigns instead of easing. Same
     shape as `holeNow < 0` above, and for the same reason. */
  let exitSnap = true;

  if (cfg.exit) {
    /* The handler does one property read and nothing else: no rect, no math, no
       uniform write. `frame` owns the easing and the clamp, so this costs the
       same whether the visitor is scrolling gently or throwing the wheel. */
    window.addEventListener(
      "scroll",
      function () {
        scrolled = window.scrollY || window.pageYOffset || 0;
      },
      { passive: true }
    );
    scrolled = window.scrollY || window.pageYOffset || 0; // a reload can restore mid-page
  }

  if (cfg.parallax) {
    window.addEventListener(
      "pointermove",
      function (e) {
        px = (e.clientX / window.innerWidth) * 2 - 1;
        py = (e.clientY / window.innerHeight) * 2 - 1;
      },
      { passive: true }
    );
  }

  function step() {
    // The form that was the destination becomes the origin, and the next one
    // is loaded behind it. Copying is cheaper than keeping three attributes
    // live and rebinding, and it happens once every nine seconds.
    from.array.set(to.array);
    to.array.set(forms[next]);
    from.needsUpdate = true;
    to.needsUpdate = true;
    next = (next + 1) % forms.length;
  }

  /* Lean each lobe toward where its cluster currently sits in the network drawn
     over this cloud.

     Worked in *view* space, not local. The cloud turns slowly on its own axis,
     so a local offset would be carried around by that spin and a lobe pulled
     toward the left of the panel would end up somewhere else a minute later.
     Adding it after the modelView transform means the lean is a screen-space
     statement, which is what "under that cluster" actually means.

     The scale factor is the half-height of the view frustum at the lobe's own
     depth, so a lobe further from the camera leans further in world units to
     cover the same distance on screen. Using a single depth for all six would
     make the near lobes overshoot their clusters and the far ones fall short. */
  const leanTmp = new THREE.Vector3();
  function updateLean(dt) {
    if (!cfg.follow || !lobeCentres.length) return;
    const cents = opts.filter && opts.filter.centroids;
    const slots = material.uniforms.uGroupPos.value;
    const k = Math.min(1, dt * 60 * cfg.followEase);
    const halfFov = ((camera.fov * 0.5) * Math.PI) / 180;

    cloud.updateMatrixWorld();
    for (let g = 0; g < slots.length; g++) {
      const c = g < lobeCentres.length && cents ? cents[g] : null;
      let tx = 0;
      let ty = 0;
      if (c) {
        // Where this lobe is right now, in view space.
        leanTmp.copy(lobeCentres[g]).applyMatrix4(cloud.matrixWorld);
        leanTmp.applyMatrix4(camera.matrixWorldInverse);
        const halfH = Math.tan(halfFov) * Math.abs(leanTmp.z);
        const halfW = halfH * camera.aspect;
        // Canvas y runs down and view y runs up, so the sign flips.
        tx = cfg.follow * (c.x * halfW - leanTmp.x);
        ty = cfg.follow * (-c.y * halfH - leanTmp.y);
      }
      // Eased toward zero as well as toward a target, so a discipline being
      // filtered out returns its lobe to the baked ring rather than stranding
      // it at the last place that cluster happened to be.
      slots[g].x += (tx - slots[g].x) * k;
      slots[g].y += (ty - slots[g].y) * k;
    }
  }

  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.05, last ? (now - last) / 1000 : 0.016);
    last = now;
    clock += dt;
    phase += dt;

    if (!morphs) {
      // One form: the shader's per-point drift and twinkle are the only motion,
      // which is the whole intent behind the network.
      material.uniforms.uT.value = 0;
    } else if (phase < DWELL) {
      material.uniforms.uT.value = 0;
    } else if (phase < DWELL + MORPH) {
      material.uniforms.uT.value = (phase - DWELL) / MORPH;
    } else {
      material.uniforms.uT.value = 0;
      phase = 0;
      step();
    }
    material.uniforms.uTime.value = clock;

    /* Filter response: a uniform dim as the legend narrows, plus a decaying
       lift whenever the visible set changes. Both act on one scalar over the
       whole cloud, so neither says anything about which points belong to which
       discipline — which is the point, since the cloud's lobes and the
       network's clusters stop agreeing on position under any filter. */
    if (opts.filter && cfg.thin != null) {
      const f = opts.filter;
      const frac = f.total > 0 ? Math.min(1, Math.max(0, f.n / f.total)) : 1;
      const target = cfg.thin + (1 - cfg.thin) * frac;
      // Eased rather than assigned: the network takes a beat to relayout after
      // a filter, and a background that has already finished changing while the
      // figure is still moving reads as two unrelated events.
      thin += (target - thin) * Math.min(1, dt * 3.2);

      const strong = !!f.strong;
      const span = strong ? cfg.flashStrongMs : cfg.flashMs;
      const peak = strong ? cfg.flashStrong : cfg.flash;
      const age = now - f.changedAt;
      let lift = 0;
      if (age >= 0 && age < span) {
        const k = 1 - age / span;
        lift = peak * k * k; // quadratic: a hard onset and a long tail
      }
      material.uniforms.uOpacity.value = baseOpacity * thin * (1 + lift);
      // Bloom, on the loud flash only. `peak` is the divisor so this tracks the
      // same decay curve as the brightness rather than needing its own.
      const decay = peak > 0 ? lift / peak : 0; // 1 at onset, 0 when spent
      const bloom = strong ? 1 + cfg.flashSize * decay : 1;
      material.uniforms.uSize.value = baseSize * bloom;

      /* Tint, on the loud flash only, in the color of the chip that caused it.
         Parsed only when the string changes: THREE.Color.set does string
         parsing, and this runs every frame. */
      if (strong && f.tint) {
        if (f.tint !== tintStr) {
          tintStr = f.tint;
          material.uniforms.uTint.value.set(tintStr);
        }
        material.uniforms.uTintAmt.value = cfg.flashTint * decay;
      } else {
        material.uniforms.uTintAmt.value = 0;
      }
    }

    /* Free spin, then the `face` correction on top of it.

       The form being squared up is the one that will be on screen when the
       motion stops: during a dwell that is the form being held, and during a
       morph it is the one arriving, not the one leaving. Starting the
       correction at the *morph* is what keeps it invisible. The cloud is
       already rearranging itself for those three seconds, so a turn folds into
       motion the viewer is watching anyway, and the form is square by the time
       it settles. Deferring it to the dwell would instead put a lone rotation
       of up to a quarter turn under an otherwise still cloud. */
    if (morphs) {
      /* Whichever form the motion is settling on: during a dwell that is the
         form being held, during a morph the one arriving. Both the swing
         amplitude and the hole floor key off it, so a form's orientation and
         its brightness arrive together rather than the cloud lighting up a beat
         before it has finished becoming the thing that needed the light. */
      const shown = names[(phase < DWELL ? next + forms.length - 2 : next + forms.length - 1) % forms.length];
      const meta = formMeta(shown);
      const ampWant = meta.face ? FACE_SWING : 1;
      swingAmp += (ampWant - swingAmp) * Math.min(1, dt * FACE_EASE);
      const holeWant = baseHole * meta.hole;
      holeNow = holeNow < 0 ? holeWant : holeNow + (holeWant - holeNow) * Math.min(1, dt * HOLE_EASE);
      material.uniforms.uHole.value = holeNow;
      // Keyed off the same `shown` for the same reason: the horizon's color
      // should arrive with the horizon, not a beat before or after it.
      const monoWant = meta.mono;
      monoNow = monoNow < 0 ? monoWant : monoNow + (monoWant - monoNow) * Math.min(1, dt * MONO_EASE);
      material.uniforms.uMonoAmt.value = monoNow;
    }
    /* The header swings within SWING and never reaches a side profile; the panel
       has one form with no front to present and keeps the plain revolution. */
    if (cfg.swing) {
      cloud.rotation.y = cfg.swing * swingAmp * Math.sin((clock * Math.PI * 2) / cfg.swingPeriod);
    } else {
      spinY += cfg.spin * dt;
      cloud.rotation.y = spinY;
    }
    cloud.rotation.x = Math.sin(clock * 0.17) * cfg.tilt;

    /* Scroll dispersal. Monotonic in scroll position and with a real resting
       state at the top of the page, which is the property that makes this
       reversible: scrolling back up gathers the cloud along the same path
       rather than replaying an animation. */
    if (cfg.exit) {
      const want = Math.min(1, Math.max(0, (scrolled - exitFrom) / exitSpan));
      exitNow = exitSnap ? want : exitNow + (want - exitNow) * Math.min(1, dt * EXIT_EASE);
      exitSnap = false;
      material.uniforms.uExit.value = exitNow;
    }

    updateLean(dt);

    // Parallax, eased hard. The cloud is behind the type, so the pointer should
    // shift the view of it, not push the points around: moving the points is
    // what makes a background look like it wants to be clicked.
    camX += (px * 0.42 - camX) * Math.min(1, dt * 1.6);
    camY += (-py * 0.2 - camY) * Math.min(1, dt * 1.6);
    camera.position.x = camX;
    camera.position.y = camY;
    camera.lookAt(0, 0, 0);

    renderer.render(scene, camera);
    if (first) {
      first = false;
      host.classList.add("in");
    }
  }

  function start() {
    if (raf || !onScreen || document.hidden) return;
    last = 0;
    exitSnap = true; // see the declaration: never ease across a stopped loop
    raf = requestAnimationFrame(frame);
  }
  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
  }

  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) {
      onScreen = entries[0].isIntersecting;
      if (onScreen) start();
      else stop();
    }).observe(host);
  }
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stop();
    else start();
  });
  canvas.addEventListener("webglcontextlost", function (e) {
    e.preventDefault();
    stop();
  });

  start();
  return { stop: stop };
}
