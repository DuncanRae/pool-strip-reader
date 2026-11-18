"""Pool water chemistry: what each reading means, what to add, and how much.

Doses are computed from first principles (mass balance, and the carbonate
equilibrium for anything that moves pH) and then deliberately kept on the
cautious side, because a test strip is only accurate to about one colour step.
The plan always ends with "retest", never with a second helping.

Every threshold and every claim in the advice text is tied to a published
source, listed in SOURCES. Where sources disagree the more cautious is used.
Nothing here comes from pool-shop custom: no routine "shock", no acid poured
in one spot, no fixed chlorine target that ignores stabiliser.
"""
import bisect
import datetime as dt
import math

PARAMS = ('fc', 'ta', 'ph', 'cya')
LABELS = {'fc': 'Free chlorine', 'ta': 'Total alkalinity', 'ph': 'pH', 'cya': 'Stabiliser'}
UNITS = {'fc': 'ppm', 'ta': 'ppm', 'ph': '', 'cya': 'ppm'}
# The range each strip pad can show.
SCALE = {'fc': (0, 10), 'ta': (0, 240), 'ph': (6.2, 8.4), 'cya': (0, 250)}

DEFAULT_SETTINGS = {
    'volumeLitres': 120000,
    'surface': 'gunite',
    # The range each reading is left alone in. pH 7.2-7.8 (CDC allows 7.0-7.8).
    # Alkalinity 80-180: topped up below the ideal 80, lowered only above the
    # accepted maximum of 180. The chlorine range here is the no-stabiliser
    # case; the real one follows stabiliser (FC_RULE).
    'ideal': {'fc': [1, 3], 'ta': [80, 180], 'ph': [7.2, 7.8], 'cya': [30, 50]},
    # Alkalinity aims at 90: standards give 80-100 as ideal where the chlorine is a hypochlorite.
    'aim': {'fc': 3, 'ta': 90, 'ph': 7.5, 'cya': 40},
    'products': {
        # strength: % available chlorine / % HCl by weight / % purity.
        'chlorine': {'name': 'HTH Granular+', 'strength': 60, 'stock': None},
        'acid': {'name': 'Pool acid', 'kind': 'liquid', 'strength': 31.5, 'stock': None},
        'alkalinityUp': {'name': 'HTH Alkalinity Up', 'strength': 100, 'stock': None},
        'phUp': {'name': 'Soda ash', 'strength': 100, 'stock': None},
        'stabiliser': {'name': 'HTH Stabiliser', 'strength': 100, 'stock': None},
    },
    'calciumHardness': None,
    'tds': 1000,
    'waterTempC': 25,
    'testIntervalDays': 7,
    'hotDayC': 32,
    'strips': {'remaining': 25, 'expiry': '2028-04'},
    'notifications': True,
}
# What the pool is lined with. The doses are the same for every surface; what
# changes is how granules may be added and what the water balance index means,
# because only plaster is made of the calcium that the index is about.
SURFACES = {
    'gunite': {'label': 'Concrete or plaster', 'word': 'plaster', 'plaster': True},
    'fibreglass': {'label': 'Fibreglass', 'word': 'gelcoat', 'plaster': False},
    'vinyl': {'label': 'Vinyl liner', 'word': 'liner', 'plaster': False},
}


def surface_of(settings):
    return SURFACES.get(settings.get('surface'), SURFACES['gunite'])


STOCK_UNITS = {'chlorine': 'g', 'acid': 'ml', 'alkalinityUp': 'g', 'phUp': 'g', 'stabiliser': 'g'}
# Which readings each product moves; used to keep drift estimates honest.
AFFECTS = {'chlorine': ('fc',), 'acid': ('ph', 'ta'), 'alkalinityUp': ('ta', 'ph'), 'phUp': ('ph', 'ta'), 'stabiliser': ('cya',)}

# --- Carbonate equilibrium (25 C) -------------------------------------------
# Apparent constants for pool water (about 27 C, 1000 ppm dissolved solids), not
# the textbook pure-water values. With these the model reproduces Wojtowicz's
# worked example (pH 8.0 to 7.4 at 100 ppm carbonate alkalinity and 50 ppm
# cyanuric acid lowers total alkalinity by 8.6 ppm) to within 2%.
K1, K2, KW, K_CYA = 10 ** -6.29, 10 ** -10.10, 1e-14, 10 ** -6.80
TA_PER_EQ = 50000.0      # mg/L as CaCO3 per equivalent per litre
CYA_MG_PER_MOL = 129070.0


def _alphas(h):
    d = h * h + K1 * h + K1 * K2
    return K1 * h / d, K1 * K2 / d


def _other_alk(ph, cya):
    """Alkalinity that is not carbonate: cyanurate plus water itself (eq/L)."""
    h = 10 ** -ph
    return cya / CYA_MG_PER_MOL * K_CYA / (K_CYA + h) + KW / h - h


def _carbon(ph, ta, cya):
    """Total dissolved carbonate (mol/L) implied by a pH and alkalinity."""
    a1, a2 = _alphas(10 ** -ph)
    return max(ta / TA_PER_EQ - _other_alk(ph, cya), 1e-5) / (a1 + 2 * a2)


def _alk(ph, carbon, cya):
    a1, a2 = _alphas(10 ** -ph)
    return carbon * (a1 + 2 * a2) + _other_alk(ph, cya)


def _ph_for(alk, carbon, cya):
    lo, hi = 4.0, 11.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if _alk(mid, carbon, cya) < alk: lo = mid
        else: hi = mid
    return (lo + hi) / 2


def acid_equivalents(ph, target, ta, cya):
    """Equivalents of strong acid per litre of pool to move pH down to target."""
    return max(0.0, ta / TA_PER_EQ - _alk(target, _carbon(ph, ta, cya), cya))


def soda_ash_moles(ph, target, ta, cya):
    """Moles of sodium carbonate per litre of pool to move pH up to target."""
    carbon = _carbon(ph, ta, cya)
    a1, a2 = _alphas(10 ** -target)
    g = a1 + 2 * a2
    return max(0.0, (carbon * g + _other_alk(target, cya) - ta / TA_PER_EQ) / (2 - g))


def after_bicarb(ph, ta, cya, rise):
    """pH after sodium bicarbonate raises alkalinity by `rise` ppm."""
    carbon = _carbon(ph, ta, cya) + rise / TA_PER_EQ
    return _ph_for((ta + rise) / TA_PER_EQ, carbon, cya)


def after_soda_ash(ph, ta, cya, moles):
    carbon = _carbon(ph, ta, cya) + moles
    alk = ta / TA_PER_EQ + 2 * moles
    return _ph_for(alk, carbon, cya), alk * TA_PER_EQ


# --- Product arithmetic ------------------------------------------------------
def chlorine_grams(rise_ppm, litres, strength):
    return rise_ppm * litres / 1000 / (strength / 100)


def bicarb_grams(rise_ppm, litres, strength=100):
    return rise_ppm * litres * 1.679 / 1000 / (strength / 100)


def stabiliser_grams(rise_ppm, litres, strength=100):
    return rise_ppm * litres / 1000 / (strength / 100)


def acid_amount(equivalents, product):
    """(amount, unit) of the configured acid delivering this many equivalents."""
    strength = product.get('strength') or 31.5
    if product.get('kind') == 'dry':          # sodium bisulphate, 120.06 g per equivalent
        return equivalents * 120.06 / (strength / 100), 'g'
    density = 1 + 0.005 * strength             # kg/L, good to 1% between 10% and 36% HCl
    molarity = 10 * density * strength / 36.46
    return equivalents / molarity * 1000, 'ml'


# --- Presentation helpers ----------------------------------------------------
def _nice(value, steps=((50, 5), (200, 10), (1000, 25), (5000, 50), (10 ** 9, 100))):
    for limit, step in steps:
        if value < limit:
            return max(step, round(value / step) * step)
    return value


def amount_text(amount, unit, cup_grams=None):
    amount = _nice(amount)
    trim = lambda number: f'{number:.2f}'.rstrip('0').rstrip('.')
    if unit == 'g':
        text = f'{trim(round(amount / 1000, 1))} kg' if amount >= 1000 else f'{amount:g} g'
        if cup_grams and cup_grams / 2 <= amount <= cup_grams * 6:
            cups = round(amount / cup_grams * 2) / 2
            text += f' (about {cups:g} cup{"" if cups == 1 else "s"})'
        return text
    return f'{trim(amount / 1000)} L' if amount >= 1000 else f'{amount:g} ml'


def status_of(key, value, ideal):
    low, high = ideal[key]
    if value < low: return 'low'
    if value > high: return 'high'
    return 'ok'


# --- Water balance (Langelier Saturation Index) ------------------------------
def carbonate_alkalinity(ph, ta, cya):
    """Alkalinity that actually buffers against scale/etching (ppm)."""
    h = 10 ** -ph
    return max(1.0, ta - cya / CYA_MG_PER_MOL * K_CYA / (K_CYA + h) * TA_PER_EQ)


def saturation_index(ph, ta, cya, calcium, temp_c=25, tds=1000):
    a = (math.log10(max(tds, 10)) - 1) / 10
    b = -13.12 * math.log10(temp_c + 273) + 34.55
    c = math.log10(max(calcium, 1)) - 0.4
    d = math.log10(carbonate_alkalinity(ph, ta, cya))
    return ph - ((9.3 + a + b) - (c + d))


def balance(values, settings, temp_c=None):
    calcium = settings.get('calciumHardness')
    surface, fibreglass = surface_of(settings), settings.get('surface') == 'fibreglass'
    if not calcium:
        return {'available': False, 'text': 'Add a calcium hardness reading (a pool shop can test it) in Settings to see whether the water is '
            + ('etching or scaling your plaster.' if surface['plaster'] else 'likely to leave scale.')
            + ' Calcium-based chlorine such as HTH granular adds about 0.7-0.8 ppm of calcium hardness for every ppm of chlorine, so hardness climbs through a season and is worth having tested a couple of times a year.'
            + (' Fibreglass pool makers set their own calcium limits, some far lower than for plaster, so check your pool\'s care guide.' if fibreglass else '')}
    temp = temp_c if temp_c is not None else settings.get('waterTempC', 25)
    index = round(saturation_index(values['ph'], values['ta'], values['cya'], calcium, temp, settings.get('tds', 1000)), 2)
    if index < -0.3:
        state = 'corrosive'
        text = ('Water is hungry: over time it etches plaster and grout. Raising pH or alkalinity brings it back.' if surface['plaster']
                else f'Water is hungry for calcium. That etches plaster and grout; a {surface["label"].lower()} pool has no plaster to lose, and guidance differs on how much this matters for it, so go by your pool maker\'s care guide before raising anything.')
    elif index > 0.3:
        state, text = 'scaling', 'Water is over-saturated: it tends to leave scale and go cloudy. Lowering pH brings it back.'
        if fibreglass: text += ' On fibreglass this is the side to avoid: calcium together with high pH is what discolours gelcoat.'
    else: state, text = 'balanced', 'Water is neither etching nor scaling the pool surface.'
    return {'available': True, 'index': index, 'state': state, 'text': text, 'tempC': round(temp, 1)}


# --- The plan ----------------------------------------------------------------
SAFETY = 'Add one product at a time with the pump running, at least an hour apart. Never mix chemicals together, not even two kinds of chlorine, and always add chemical to water, never water to chemical.'

# How much free chlorine a pool needs depends on its stabiliser (cyanuric acid),
# which holds most of the chlorine in reserve. CDC: at least 1 ppm, or 2 ppm when
# stabiliser is used. Falk et al. 2019: keep stabiliser no more than 20 times
# the free chlorine. The ideal band is that minimum plus `span`.
FC_RULE = {'plainMin': 1.0, 'stabilisedMin': 2.0, 'ratio': 20.0, 'cyaFrom': 20.0, 'span': 2.0, 'cap': 8.0}
TA_ACCEPTED = (60, 180)      # ANSI/APSP/ICC-11: minimum and maximum total alkalinity
CYA_PH_WINDOW = (7.0, 8.4)   # the strip's stabiliser pad is only valid inside this pH range

SOURCES = [
    {'title': 'CDC: Home pool and hot tub water treatment and testing',
     'url': 'https://www.cdc.gov/healthy-swimming/about/home-pool-and-hot-tub-water-treatment-and-testing.html',
     'used': 'Free chlorine at least 1 ppm, or 2 ppm when stabiliser is used. pH 7.0 to 7.8. Strips are less accurate than drop (DPD) kits, and above 10 ppm a test can bleach out and read low.'},
    {'title': 'Falk et al. 2019, Water 11(6):1314: Assessing the impact of cyanuric acid on bather\'s risk of gastrointestinal illness at swimming pools',
     'url': 'https://www.mdpi.com/2073-4441/11/6/1314',
     'used': 'Chlorine and stabiliser must be judged together: keep stabiliser no more than 20 times the free chlorine.'},
    {'title': 'CDC Model Aquatic Health Code, 5th edition',
     'url': 'https://www.cdc.gov/model-aquatic-health-code/php/our-work/index.html',
     'used': 'Free chlorine above 10 ppm with swimmers present is the level at which a public pool must close.'},
    {'title': 'ANSI/APSP/ICC-11: Water quality in public pools and spas',
     'url': 'https://webstore.ansi.org/standards/apsp/ansiapsp112019',
     'used': 'Total alkalinity 60 to 180 ppm, ideally 80 to 100 where the chlorine is a hypochlorite. Calcium hardness 150 to 1000 ppm.'},
    {'title': 'NSW Health: Stabiliser (cyanurate) use in outdoor swimming pools',
     'url': 'https://www.health.nsw.gov.au/environment/factsheets/Pages/stabiliser-cyanurate.aspx',
     'used': 'Most of the sunlight protection is reached by about 40 mg/L of stabiliser; more adds little. Limit 50 mg/L.'},
    {'title': 'Wojtowicz, Journal of the Swimming Pool and Spa Industry: the carbonate system and carbon dioxide loss in pool water',
     'url': 'https://poolhelp.com/?p=995',
     'used': 'The acid and soda ash arithmetic. Why pH climbs by itself (carbon dioxide escaping), faster at higher alkalinity. Carbonate alkalinity is total alkalinity less about a third of the stabiliser.'},
    {'title': 'Pickens: Relative effects of pH and cyanurate on disinfection',
     'url': 'https://www.poolhelp.com/wp-content/uploads/2017/11/Relative-Effects-of-pH-and-Cyanurate-on-Disinfection.pdf',
     'used': 'With stabiliser present, the active chlorine is far less sensitive to pH than in unstabilised water.'},
    {'title': 'onBalance: The acid column myth',
     'url': 'https://www.poolhelp.com/home/onbalance-research/education/the-acid-column-myth/',
     'used': 'Pouring acid in one spot does not lower alkalinity any more than spreading it, and leaves strongly acidic water on the pool floor.'},
    {'title': 'HTH Dry Chlorinator Granular: product label accepted by the US EPA (registration 1258-427)',
     'url': 'https://www3.epa.gov/pesticides/chem_search/ppls/001258-00427-20231206.pdf',
     'used': 'Granules that settle on the floor must be brushed away, and the label gives a separate method "to avoid bleaching vinyl liner or paint". This is why pools that are not plaster are told never to let granules lie.'},
    {'title': 'River Pools: fibreglass pool care guide (one maker\'s limits)',
     'url': 'https://www.riverpoolsandspas.com/hubfs/2025%20River%20Pools%20Care%20Guide.pdf',
     'used': 'This maker asks for calcium hardness under 120 ppm, no calcium hypochlorite, and a saturation index never above +0.3, because calcium with high pH discolours gelcoat. Makers differ, so the app raises these as things to check in your own pool\'s care guide and does not change its ranges.'},
    {'title': 'Aqua Magazine: Cal hypo, the facts',
     'url': 'https://www.aquamagazine.com/retail/article/15120781/cal-hypo-the-facts',
     'used': 'Calcium hypochlorite adds about 0.8 ppm of calcium hardness per ppm of chlorine.'},
]


def chlorine_basis(readings):
    """Which chlorine rule applies: 'plain', 'stabilised', or 'unknown' when the stabiliser pad cannot be trusted."""
    cya, ph = readings['cya'], readings['ph']
    if cya.get('confidence') == 'poor' or ph.get('confidence') == 'poor': return 'unknown'
    if not CYA_PH_WINDOW[0] <= float(ph['value']) <= CYA_PH_WINDOW[1]: return 'unknown'
    return 'plain' if float(cya['value']) < FC_RULE['cyaFrom'] else 'stabilised'


def effective_ideal(readings, settings):
    """The ideal ranges for this set of readings: as configured, except that the chlorine range follows stabiliser."""
    ideal = {key: list(pair) for key, pair in settings['ideal'].items()}
    basis = chlorine_basis(readings)
    if basis == 'plain': low = FC_RULE['plainMin']
    elif basis == 'unknown': low = FC_RULE['stabilisedMin']
    else: low = max(FC_RULE['stabilisedMin'], math.ceil(float(readings['cya']['value']) / FC_RULE['ratio'] * 2) / 2)
    low = min(low, FC_RULE['cap'])
    ideal['fc'] = [low, low + FC_RULE['span']]
    return ideal


def analyse(readings, settings, temp_c=None):
    """Statuses, an ordered dosing plan and notes for one set of readings.

    `readings` maps fc/ta/ph/cya to {'value': number, optional 'atMin'/'atMax',
    optional 'confidence'}. A reading whose colour was a poor match to the chart
    is "unsure": it gets no advice of its own, and any dose that depends on it
    is held back, because a guess must never turn into kilograms of product.
    """
    litres = settings['volumeLitres']
    aim, products = settings['aim'], settings['products']
    surface = surface_of(settings)
    v = {k: float(readings[k]['value']) for k in PARAMS}
    ideal = effective_ideal(readings, settings)
    basis = chlorine_basis(readings)
    status = {k: status_of(k, v[k], ideal) for k in PARAMS}
    unsure = [k for k in PARAMS if readings[k].get('confidence') == 'poor']
    steps, notes, held = [], [], set()
    span = lambda key: f'{ideal[key][0]:g}-{ideal[key][1]:g}'

    def trusted(needs):
        """True when every reading this advice rests on can be relied on."""
        if not set(needs) & set(unsure): return True
        if needs[0] not in unsure: held.add(needs[0])
        return False

    def step(key, product, amount, unit, title, how, why, cups=None, needs=None):
        if not trusted(needs or (key.split('-')[0],)): return
        name = products[product]['name']
        steps.append({
            'key': key, 'param': key.split('-')[0], 'product': product, 'productName': name,
            'amount': _nice(amount), 'unit': unit, 'amountText': amount_text(amount, unit, cups),
            'title': title, 'how': how, 'why': why,
        })

    def note(text, *needs):
        if trusted(needs): notes.append(text)

    ph_now, ta_now = v['ph'], v['ta']
    dissolve = 'Pre-dissolve in a bucket of pool water and pour around the deep end. Run the pump for at least 4 hours.'

    # 1. Alkalinity first: it is the buffer that makes pH hold still.
    if status['ta'] == 'low':
        rise = min(aim['ta'] - v['ta'], 40)
        grams = bicarb_grams(rise, litres, products['alkalinityUp']['strength'])
        expected = after_bicarb(v['ph'], v['ta'], v['cya'], rise)
        context = ('Below 60 ppm the water has too little buffer and pH swings easily.' if v['ta'] < TA_ACCEPTED[0]
                   else f'That is still inside the {TA_ACCEPTED[0]}-{TA_ACCEPTED[1]} ppm that pool standards accept, so this is a top-up, not a rescue.')
        staged = ' This is the first stage; retest before adding more.' if aim['ta'] - v['ta'] > 40 else ''
        step('ta-up', 'alkalinityUp', grams, 'g', 'Raise alkalinity', dissolve,
             f'Alkalinity is {v["ta"]:g} ppm (aim for {span("ta")}). {context} This lifts it by about {rise:g} ppm'
             + ('.' if 'ph' in unsure else f' and nudges pH to roughly {expected:.1f}.') + staged, 250, needs=('ta',))
        ph_now, ta_now = expected, v['ta'] + rise

    # 2. pH. Alkalinity is only brought down with acid once it is above the accepted maximum.
    acid_cap = litres / 50000 * 1000  # ml of liquid acid per application
    too_alkaline = v['ta'] > TA_ACCEPTED[1]
    if status['ph'] == 'high' or (too_alkaline and v['ph'] >= ideal['ph'][0] + 0.1):
        target = ideal['ph'][0] if too_alkaline else aim['ph']
        # Any alkalinity increaser from step 1 is already in the water by now.
        eq = acid_equivalents(ph_now, target, ta_now, v['cya']) * litres
        amount, unit = acid_amount(eq, products['acid'])
        cap = acid_cap if unit == 'ml' else acid_cap * 1.4
        capped = amount > cap
        if capped:
            eq *= cap / amount; amount = cap
        ta_drop = eq / litres * TA_PER_EQ
        if status['ph'] != 'high':
            why = (f'pH is fine, but alkalinity is {v["ta"]:g} ppm, above the {TA_ACCEPTED[1]} that pool standards accept, and acid is what brings it down. '
                   f'This takes it down about {ta_drop:.0f} ppm. Afterwards, aerating the water (returns pointed up, fountains on) brings pH back up without restoring the alkalinity; repeat until it is under {TA_ACCEPTED[1]}.')
        else:
            why = f'pH is {v["ph"]:g} (aim for {span("ph")}). '
            why += ('With stabiliser in the water, pH has only a modest effect on how well chlorine works. The reasons to bring it down are scale and cloudy water, which calcium-based chlorine makes more likely, and comfort.'
                    if basis == 'stabilised' else 'Above about 8.0, chlorine without stabiliser loses much of its killing power, and scale and cloudy water become more likely.')
            if too_alkaline: why += f' It also takes alkalinity down about {ta_drop:.0f} ppm, which is above the accepted maximum too.'
        if readings['ph'].get('atMax'):
            why += ' The strip tops out at 8.4, so the real pH may be higher.'
        if capped:
            why += ' This is the most to add in one go; retest tomorrow and repeat if needed.'
        step('ph-down', 'acid', amount, unit, 'Lower pH' if status['ph'] == 'high' else 'Lower alkalinity',
             'Dilute in a bucket of pool water (acid into water) and pour it slowly in front of a return jet with the pump running, moving around the deep end. Pouring it all in one spot does not lower alkalinity any better and leaves strong acid sitting on the ' + surface['word'] + '. Run the pump for 4 hours.' if unit == 'ml'
             else dissolve, why, needs=('ph', 'ta'))
    elif status['ph'] == 'low':
        if status['ta'] == 'low':
            note(f'pH is low at {v["ph"]:g}, but so is alkalinity. Raising alkalinity lifts pH as well (to about {ph_now:.1f}), so do that first and retest before adding anything for pH.', 'ph', 'ta')
        else:
            moles = soda_ash_moles(v['ph'], aim['ph'], v['ta'], v['cya'])
            full = moles * 105.99 * litres / (products['phUp']['strength'] / 100)
            grams = min(full, litres / 10000 * 150)
            step('ph-up', 'phUp', grams, 'g', 'Raise pH',
                 dissolve + ' Aerating the water (returns pointed up, fountains on) also raises pH with no chemical at all, because it drives off dissolved carbon dioxide.',
                 f'pH is {v["ph"]:g} (aim for {span("ph")}). Low pH makes water corrosive: it {"etches plaster and attacks" if surface["plaster"] else "attacks"} metal fittings. Alkalinity is already fine at {v["ta"]:g} ppm, so soda ash is the right tool: it lifts pH strongly without piling on alkalinity.'
                 + (' This is the most to add in one go; retest tomorrow and repeat if needed.' if full > grams else '')
                 + (' The strip bottoms out at 6.2, so the real pH may be lower.' if readings['ph'].get('atMin') else ''), needs=('ph', 'ta'))
    if 120 < v['ta'] <= TA_ACCEPTED[1]:
        note(f'Alkalinity is {v["ta"]:g} ppm: more than the 80-120 the bottle calls ideal, but inside the {TA_ACCEPTED[0]}-{TA_ACCEPTED[1]} ppm that pool standards accept, so there is nothing to fix. Its one effect is that pH climbs faster, because more carbon dioxide escapes from the water.', 'ta')
    elif too_alkaline:
        if status['ph'] == 'low':
            note(f'Alkalinity is high at {v["ta"]:g} ppm, but pH is low so acid is the wrong tool right now. Fix pH first; alkalinity can come down later.', 'ta', 'ph')
        elif v['ph'] < ideal['ph'][0] + 0.1:
            note(f'Alkalinity is high at {v["ta"]:g} ppm. With pH already at the bottom of its range, leave it for now; it comes down gradually with normal acid use.', 'ta', 'ph')

    # 3. Chlorine. How much is enough depends on stabiliser.
    low, high = ideal['fc']
    reason = {
        'stabilised': f'With stabiliser at about {v["cya"]:g} ppm it needs at least {low:g} ppm: stabiliser holds most of the chlorine in reserve, so the minimum rises with it (2 ppm, or a twentieth of the stabiliser level if that is more).',
        'plain': 'With little or no stabiliser, 1 ppm is the minimum that keeps the water sanitary.',
        'unknown': 'The stabiliser reading could not be relied on, so this uses the 2 ppm minimum that applies whenever stabiliser is in the water.',
    }[basis]
    if status['fc'] == 'low':
        grams = chlorine_grams(high - v['fc'], litres, products['chlorine']['strength'])
        why = f'Free chlorine is {v["fc"]:g} ppm (aim for {span("fc")}). {reason}'
        if v['fc'] == 0:
            why += ' If a lot of chlorine went in during the last day or two, a zero can instead be a pad bleached by very high chlorine (above 10 ppm); do not add more in that case.'
        if status['ph'] == 'high' and not {'ph', 'ta'} & set(unsure):
            why += ' Add it at least an hour after the acid, never together.'
        if basis == 'plain':
            why += ' Without stabiliser, sunlight destroys chlorine within hours, which is why it goes in at dusk.'
        if settings.get('surface') == 'fibreglass':
            why += ' Most granular chlorine is calcium hypochlorite, and some fibreglass pool makers tell owners not to use it because calcium can discolour gelcoat. Check your pool\'s care guide.'
        how = ('Add in the evening with the pump running: pre-dissolve in a bucket of pool water and pour around the deep end, or scatter it over the deep end and brush any granules off the floor. Use a clean, dry scoop.' if surface['plaster']
               else f'Add in the evening with the pump running. Do not let granules settle: undissolved chlorine bleaches {"a liner" if surface["word"] == "liner" else surface["word"]}. Use the method your product\'s label gives for this kind of pool, usually pre-dissolved in a bucket of pool water and poured around the deep end, and brush away at once anything that reaches the floor. Use a clean, dry scoop.')
        step('fc-up', 'chlorine', grams, 'g', 'Add chlorine', how, why, 250, needs=('fc',))
    elif v['fc'] >= 10:
        note('Chlorine is at the top of the strip (10 ppm or more). Do not add any, and keep swimmers out: 10 ppm is the level at which public-pool codes close a pool. Leave the cover off so sunlight can bring it down, and retest tomorrow. No test is reliable this high.', 'fc')
    elif status['fc'] == 'high':
        note(f'Chlorine is {v["fc"]:g} ppm, above the {span("fc")} ppm it needs. That is not a hazard (pool codes only act above 10 ppm), so skip the next dose and let it drift down. Your chlorine\'s label may ask for a lower level before swimming.', 'fc')

    # 4. Stabiliser. The pad is only trustworthy inside its pH window.
    if not CYA_PH_WINDOW[0] <= v['ph'] <= CYA_PH_WINDOW[1]:
        if status['cya'] != 'ok':
            note('The stabiliser pad only reads properly when pH is between 7.0 and 8.4. Fix pH first, then retest before acting on stabiliser.', 'cya', 'ph')
        else:
            # In range on the strip, but read outside the pad's window: say so, or "in range" claims more than the strip can.
            note(f'Stabiliser read {v["cya"]:g} ppm, but that pad only reads properly when pH is between 7.0 and 8.4, and pH is {v["ph"]:g}. Treat it as a rough figure and check it again once pH is back in range.', 'cya', 'ph')
    elif status['cya'] == 'low':
        rise = aim['cya'] - v['cya']
        grams = stabiliser_grams(rise, litres, products['stabiliser']['strength'])
        halves = grams > 2000
        if halves: grams /= 2
        step('cya-up', 'stabiliser', grams, 'g', 'Add stabiliser',
             'Add slowly through the weir with the pump running (or hang it in a sock in front of a return jet). It dissolves over a few days: do not backwash for a week.',
             f'Stabiliser is {v["cya"]:g} ppm (aim for {span("cya")}). It shields chlorine from sunlight, which otherwise destroys it within hours. Most of that protection is reached by about 40 ppm; going higher adds little and forces the chlorine level up.'
             + (' This is half the full amount, because the stabiliser pad is the least precise on the strip and too much is hard to undo. Retest in a week before adding the rest.' if halves else ''), needs=('cya', 'ph'))
    elif v['cya'] >= 100:
        fraction = 1 - ideal['cya'][1] / v['cya']
        note(f'Stabiliser is very high at {v["cya"]:g} ppm or more. Chlorine has to stay above a twentieth of that to keep working, which is why its range above has moved up. Only dilution lowers stabiliser: replacing roughly {fraction * 100:.0f}% of the water (about {_nice(litres * fraction / 1000):g} kL), by backwashing and refilling in stages, would bring it back to 50. Avoid stabilised chlorine (pills, floaters) meanwhile.', 'cya', 'ph')
    elif status['cya'] == 'high':
        note(f'Stabiliser reads about {v["cya"]:g} ppm, above the 50 the bottle advises. It is not harmful in itself, but chlorine has to rise with it, which is why the chlorine range above has moved up. Do not add any more; it only leaves the pool by dilution (backwashing, splash-out, overflow).', 'cya', 'ph')

    for i, s in enumerate(steps): s['order'] = i + 1
    if unsure:
        names = _join([LABELS[k] if k == 'ph' else LABELS[k].lower() for k in unsure])
        text = f'The {names} pad{"s" if len(unsure) > 1 else ""} did not match the chart closely enough to trust, so no advice here rests on {"them" if len(unsure) > 1 else "it"}. Retake the test, or set {"them" if len(unsure) > 1 else "it"} by eye on the review screen.'
        others = sorted(held - set(unsure), key=PARAMS.index)
        if others:
            text += f' Advice for {_join([LABELS[k] if k == "ph" else LABELS[k].lower() for k in others])} is held back too, because it depends on {"those readings" if len(unsure) > 1 else "that reading"}.'
        notes.insert(0, text)
        status = {k: 'unsure' if k in unsure else status[k] for k in PARAMS}
    issues = [k for k in PARAMS if status[k] in ('low', 'high')]
    retest = 24 if steps else None
    if any(s['key'] == 'cya-up' for s in steps) and len(steps) == 1: retest = 24 * 7
    return {
        'values': v, 'status': status, 'steps': steps, 'notes': notes, 'issues': issues, 'unsure': unsure,
        'ideal': ideal, 'chlorineBasis': basis,
        'headline': headline(status, steps), 'safety': SAFETY if len(steps) > 1 or any(s['product'] == 'acid' for s in steps) else None,
        'retestHours': retest, 'balance': balance(v, settings, temp_c),
    }


def _join(names):
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]


def headline(status, steps):
    issues = [k for k in PARAMS if status[k] in ('low', 'high')]
    unsure = [k for k in PARAMS if status[k] == 'unsure']
    if not issues and not unsure: return 'Water is balanced'
    if not issues:
        text = (f'{len(unsure)} readings' if len(unsure) > 2 else _join([LABELS[k] if k == 'ph' else LABELS[k].lower() for k in unsure])) + ' unclear'
    elif len(issues) > 2: return f'{len(issues)} readings need attention'
    else: text = _join([f'{LABELS[k] if k == "ph" else LABELS[k].lower()} {status[k]}' for k in issues])
    return text if text.startswith('pH') else text[0].upper() + text[1:]


def reading_status(key, reading, ideal):
    """Status of one stored reading: low / ok / high, or unsure for a poor colour match."""
    return 'unsure' if reading.get('confidence') == 'poor' else status_of(key, reading['value'], ideal)


def test_statuses(readings, settings):
    """Statuses of a stored test, judged against the ranges that applied to it."""
    ideal = effective_ideal(readings, settings)
    return {key: reading_status(key, readings[key], ideal) for key in PARAMS}


# --- Drift between tests -----------------------------------------------------
def _when(text):
    return dt.datetime.fromisoformat(text.replace('Z', '+00:00'))


def drift(tests, doses, key):
    """Typical change per day in a reading across gaps with no relevant dose.

    Dosing makes history a sawtooth; only the undisturbed stretches say how
    fast the pool itself is moving. Needs at least two such stretches.
    """
    relevant = sorted(_when(d['at']) for d in doses if not d.get('voidedAt') and key in AFFECTS.get(d['product'], ()))
    # Strip tests only. A shop's meter and a strip can differ by a colour step on the same water, and that
    # difference is not the pool moving: a shop test dropped in between two strips must not show up as drift.
    rows = sorted((_when(t['at']), t['readings'][key]['value']) for t in tests if not t.get('voidedAt') and t.get('source') != 'lab')
    rates = []
    for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
        days = (t1 - t0).total_seconds() / 86400
        if not 0.5 <= days <= 14: continue
        # `relevant` is sorted: the first dose at or after t0 is the only one that can fall in the gap.
        first = bisect.bisect_left(relevant, t0)
        if first < len(relevant) and relevant[first] <= t1: continue
        rates.append((v1 - v0) / days)
    if len(rates) < 2: return None
    rates.sort()
    middle = len(rates) // 2
    return rates[middle] if len(rates) % 2 else (rates[middle - 1] + rates[middle]) / 2


def trends(tests, doses, settings):
    """Plain-language observations about where the water is heading."""
    live = [t for t in tests if not t.get('voidedAt')]
    if len(live) < 3: return []
    latest = max(live, key=lambda t: t['at'])['readings']
    ideal, out = effective_ideal(latest, settings), []
    rate = drift(tests, doses, 'ph')
    if rate is not None and abs(rate * 7) >= 0.1:
        text = f'pH drifts {"up" if rate > 0 else "down"} about {abs(rate * 7):.1f} a week between treatments.'
        edge = ideal['ph'][1] if rate > 0 else ideal['ph'][0]
        days = (edge - latest['ph']['value']) / rate
        if rate > 0: text += ' An upward drift is normal: carbon dioxide escapes from the water and granular chlorine is alkaline. It is slower when alkalinity sits at the low end of its range.'
        if 0 < days < 21: text += f' At that pace it leaves the ideal range in about {max(1, round(days))} day{"s" if round(days) != 1 else ""}.'
        out.append({'key': 'ph', 'text': text})
    rate = drift(tests, doses, 'fc')
    if rate is not None and rate <= -0.1:
        text = f'The pool uses about {abs(rate):.1f} ppm of chlorine a day.'
        days = (latest['fc']['value'] - ideal['fc'][0]) / -rate
        if latest['fc']['value'] > ideal['fc'][0]:
            text += f' From the last reading, expect to need chlorine in about {max(1, round(days))} day{"s" if round(days) != 1 else ""}.'
        out.append({'key': 'fc', 'text': text})
    rate = drift(tests, doses, 'ta')
    if rate is not None and abs(rate * 7) >= 8:
        out.append({'key': 'ta', 'text': f'Alkalinity is {"rising" if rate > 0 else "falling"} about {abs(rate * 7):.0f} ppm a week.'})
    rate = drift(tests, doses, 'cya')
    if rate is not None and rate * 30 >= 10:
        out.append({'key': 'cya', 'text': f'Stabiliser is climbing about {rate * 30:.0f} ppm a month, which usually means stabilised chlorine (pills or floaters) is in use.'})
    return out


# --- A test that the water has moved on from ---------------------------------
def doses_since(test, doses):
    """Chemicals logged after this test's sample was taken that were not part of its own plan."""
    at = _when(test['at'])
    return sorted((d for d in doses if not d.get('voidedAt') and d.get('testId') != test['id'] and _when(d['at']) > at), key=lambda d: d['at'])


def hold_if_dosed_since(analysis, test, doses, settings):
    """A plan is only good for the water that was tested.

    A test can be the newest on record and still be older than the last thing
    added to the pool: a shop's report entered days after the sample was taken,
    or the test before one that was deleted. Its readings are still shown, but
    no amounts are given from it, because they would not allow for what has
    gone in since.
    """
    later = doses_since(test, doses)
    if not later: return analysis
    names = list(dict.fromkeys(settings['products'].get(d['product'], {}).get('name', d['product']) for d in later))
    had_plan = bool(analysis['steps'])
    analysis.update(steps=[], safety=None, retestHours=None, dosedSince=True)
    analysis['notes'].insert(0, f'{_join(names)} {"was" if len(names) == 1 else "were"} added after this sample was taken, so the pool is no longer the water that was tested.'
        + (' No amounts are given from this test: they would not allow for what has gone in since.' if had_plan else '') + ' Test again to see where the water stands now.')
    return analysis


# --- Strips checked against a shop's meter -----------------------------------
# How far apart in time (hours) a strip test and a shop test may be for each reading to be a fair comparison.
# Sunlight uses chlorine up by the hour and pH drifts over days; alkalinity and stabiliser barely move in two days.
CHECK_WINDOW = {'fc': 12, 'ph': 24, 'ta': 48, 'cya': 48}


def strip_verdict(key, reading, value, chart):
    """Whether a strip reading agrees with a shop's figure for the same water: 'agrees', or the strip read 'higher' or 'lower'.

    A strip can only show the colours on its chart, so it is allowed half the
    gap between the two chart steps the shop's figure falls between.
    """
    steps = [s['value'] for s in chart['params'][key]['swatches']]
    upper = next((i for i, step in enumerate(steps) if step >= value), len(steps) - 1) or 1
    slack = (steps[upper] - steps[upper - 1]) / 2
    low, high = reading.get('low', reading['value']), reading.get('high', reading['value'])
    # At either end of its chart a strip is saying "this or beyond", which is all it can say.
    if (value >= steps[-1] and high >= steps[-1]) or (value <= steps[0] and low <= steps[0]): return 'agrees'
    if value < low - slack - 1e-9: return 'higher'
    if value > high + slack + 1e-9: return 'lower'
    return 'agrees'


def strip_checks(tests, doses, chart, limit=20):
    """Each of the newest shop tests beside the strip test nearest to it in time, reading by reading.

    A reading is only compared when the two tests are close enough in time for
    it (CHECK_WINDOW), the strip's colour match was not poor, and nothing that
    moves that reading was added to the pool between the two.
    """
    live = [t for t in tests if not t.get('voidedAt')]
    strips = [(_when(t['at']), t) for t in live if t.get('source') != 'lab']
    added = [(_when(d['at']), d['product']) for d in doses if not d.get('voidedAt')]
    out = []
    for shop in sorted((t for t in live if t.get('source') == 'lab'), key=lambda t: t['at'], reverse=True)[:limit]:
        at = _when(shop['at'])
        when, strip = min(strips, key=lambda row: abs(row[0] - at), default=(None, None))
        hours = abs((when - at).total_seconds()) / 3600 if strip else None
        if not strip or hours > max(CHECK_WINDOW.values()): continue
        first, last = min(at, when), max(at, when)
        between = {product for moment, product in added if first <= moment <= last}
        rows = {}
        for key in PARAMS:
            reading = strip['readings'][key]
            if reading.get('confidence') == 'poor': verdict = 'unclear'
            elif hours > CHECK_WINDOW[key]: verdict = 'apart'
            elif any(key in AFFECTS.get(product, ()) for product in between): verdict = 'dosed'
            else: verdict = strip_verdict(key, reading, shop['readings'][key]['value'], chart)
            rows[key] = {'shop': shop['readings'][key]['value'], 'strip': reading['value'], 'verdict': verdict}
        out.append({'shopId': shop['id'], 'stripId': strip['id'], 'shopAt': shop['at'], 'stripAt': strip['at'], 'hours': round(hours, 1), 'stripLater': when > at, 'readings': rows})
    return out
